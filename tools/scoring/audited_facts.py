"""S3 append-only call journal. Reservations are estimates, not provider hard caps.

No automatic retry/resend/resume. Pending after interruption means unknown spend.
Only offline recovery is supported; a new live batch requires a new journal.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import os
from pathlib import Path
import urllib.request

from .fact_judge import (LiveFacts, FactError, SYSTEM, VERSION, canonical,
                         fingerprint, strict_json, validate_response)

JOURNAL_VERSION = 'okb-fact-call-journal-v1'

def user_agent() -> str:
    """Explicit User-Agent for judge transport.

    urllib's default (`Python-urllib/3.x`) is refused by CDN-fronted OpenAI-compatible
    gateways with HTTP 403 / Cloudflare 1010, which would fail the whole live path
    identically on Windows and on openKylin. The default reuses the string already sent by
    the legacy judge client (tools/scoring/judge_client.py) and is overridable through
    OKB_JUDGE_USER_AGENT. Credentials are never written to disk or logged.
    """
    return os.environ.get("OKB_JUDGE_USER_AGENT") or "okb-judge/1.0 (openkylin-memory-benchmark)"


def money(value):
    if not isinstance(value, str):
        raise FactError('money must be a decimal string')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise FactError('invalid decimal amount') from None
    if not result.is_finite() or result < 0:
        raise FactError('amount must be finite and nonnegative')
    return result


def validate_budget(obj):
    if isinstance(obj, dict) and obj.get('format') == 'okb-judge-budget-v2-draft':
        from .cache_billing import validate_budget as validate_cache_budget
        return validate_cache_budget(obj)
    fields = {'currency', 'max_total_cost', 'reserve_per_request',
              'input_per_million', 'output_per_million', 'price_source'}
    if not isinstance(obj, dict) or set(obj) != fields:
        raise FactError('invalid budget fields')
    if (not isinstance(obj['currency'], str) or len(obj['currency']) != 3
            or not obj['currency'].isascii() or not obj['currency'].isupper()
            or not obj['currency'].isalpha()):
        raise FactError('currency must be three uppercase letters')
    if not isinstance(obj['price_source'], str) or not obj['price_source'].strip():
        raise FactError('price provenance required')
    values = {k: money(obj[k]) for k in fields - {'currency', 'price_source'}}
    if values['max_total_cost'] <= 0 or values['reserve_per_request'] <= 0:
        raise FactError('positive total and reservation required')
    if values['reserve_per_request'] > values['max_total_cost']:
        raise FactError('one request reservation exceeds batch budget')
    return dict(obj)


def usage_valid(value):
    """Keep numeric raw usage fields, never arbitrary provider text or bodies."""
    if not isinstance(value, dict) or not value:
        return False
    for k,v in value.items():
        if not isinstance(k,str) or len(k)>100:
            return False
        if isinstance(v, dict):
            if v and not usage_valid(v):
                return False
        elif v is not None and (type(v) is not int or v < 0):
            return False
    return True


def estimate_cost(usage, budget):
    if budget.get('format') == 'okb-judge-budget-v2-draft':
        return None
    if not usage_valid(usage):
        return None
    details = usage.get('prompt_tokens_details')
    if isinstance(details, dict) and details.get('cache_write_tokens', 0) != 0:
        return None
    if any(type(usage.get(k)) is not int for k in ('prompt_tokens', 'completion_tokens')):
        return None
    # No assumed cache discount. Completion already includes provider-reported
    # reasoning tokens when applicable; never add nested counters a second time.
    return str((Decimal(usage['prompt_tokens'])*money(budget['input_per_million'])
                + Decimal(usage['completion_tokens'])*money(budget['output_per_million'])) / Decimal(1_000_000))


def deepseek_usage_valid(usage):
    if not usage_valid(usage):
        return False
    fields = ('prompt_tokens', 'completion_tokens', 'total_tokens',
              'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')
    if any(type(usage.get(field)) is not int for field in fields):
        return False
    if (usage['prompt_tokens'] != usage['prompt_cache_hit_tokens'] + usage['prompt_cache_miss_tokens']
            or usage['total_tokens'] != usage['prompt_tokens'] + usage['completion_tokens']):
        return False
    for field, detail, limit in (
            ('prompt_tokens_details', 'cached_tokens', usage['prompt_cache_hit_tokens']),
            ('completion_tokens_details', 'reasoning_tokens', usage['completion_tokens'])):
        if field in usage:
            details = usage[field]
            if not isinstance(details, dict):
                return False
            if detail in details:
                count = details[detail]
                if type(count) is not int or not 0 <= count <= limit:
                    return False
                if detail == 'cached_tokens' and count != limit:
                    return False
    return True


def read_journal(path):
    """Verify chain; tolerate only an incomplete final line, never middle damage."""
    raw = Path(path).read_bytes()
    lines = raw.splitlines(keepends=True)
    events = []
    previous = '0'*64
    tail_incomplete = False
    for i,line in enumerate(lines):
        if not line.endswith(b'\n'):
            tail_incomplete = True
            break
        try:
            row = strict_json(line.decode('utf8'))
        except Exception:
            raise FactError('invalid journal line') from None
        if set(row) != {'seq', 'previous_sha256', 'event', 'sha256'}:
            raise FactError('invalid journal row')
        content = {k:row[k] for k in ('seq','previous_sha256','event')}
        if row['seq'] != i or row['previous_sha256'] != previous or fingerprint(content) != row['sha256']:
            raise FactError('journal chain mismatch')
        previous = row['sha256']; events.append(row['event'])
    if not events or events[0].get('kind') != 'batch' or events[0].get('journal_version') != JOURNAL_VERSION:
        raise FactError('missing journal batch header')
    pending = {}; completed = {}; terminal = {}
    for event in events[1:]:
        key = event.get('request_sha256')
        if event.get('kind') == 'pending':
            if (key in pending or key != fingerprint(event['request'])
                    or event['request'].get('protocol') != events[0]['protocol']
                    or event['request'].get('system_sha256') != events[0]['system_sha256']):
                raise FactError('duplicate or mismatched pending request')
            pending[key] = event
        elif event.get('kind') in ('completed', 'invalid_response', 'unknown'):
            if key not in pending or key in terminal:
                raise FactError('terminal event without unique pending request')
            terminal[key] = event
            if event['kind'] == 'completed':
                if event.get('returned_model') != events[0]['model']:
                    raise FactError('completed model mismatch')
                validate_response(pending[key]['request'], event['response'])
                completed[key] = event
        else:
            raise FactError('unknown journal event')
    return dict(batch=events[0], events=events, pending=pending, completed=completed,
                terminal=terminal, unknown_requests=sorted(k for k in pending
                    if k not in terminal or terminal[k]['kind']=='unknown'),
                incomplete_tail=tail_incomplete, last_sha256=previous)


def recovered_records(path, *, model):
    journal = read_journal(path)
    if journal['batch']['model'] != model or journal['batch']['protocol'] != VERSION:
        raise FactError('recovery model/protocol mismatch')
    return [{'protocol': VERSION, 'model': model, 'request_sha256': key,
             'response': event['response']} for key,event in journal['completed'].items()]


class AuditedFacts(LiveFacts):
    def __init__(self, *, journal_path, budget, max_output_tokens,
                 runtime_contract=None, reviewed_requests=None,
                 wire_profile='legacy-chat-v1', reasoning_effort=None,
                 cost_group=None, cost_scope=None, **kwargs):
        super().__init__(**kwargs)
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 32768:
            raise FactError('explicit output limit 1..32768 required')
        self.budget = validate_budget(budget)
        self.max_output_tokens = max_output_tokens
        from .cost_review import wire_body
        self.wire_options = dict(wire_profile=wire_profile, reasoning_effort=reasoning_effort)
        wire_body({}, self.model, max_output_tokens, **self.wire_options)
        self.runtime_binding = None
        if runtime_contract is not None:
            from .cost_runtime import validate_runtime_contract
            self.runtime_binding = validate_runtime_contract(runtime_contract, reviewed_requests,
                model=self.model, base_url=self.base, max_output_tokens=max_output_tokens,
                budget=self.budget, max_calls=self.max_calls, **self.wire_options)
        elif reviewed_requests is not None:
            raise FactError('reviewed requests require a runtime contract')
        if (wire_profile == 'openai-cache-chat-v1') != (self.budget.get('format') == 'okb-judge-budget-v2-draft'):
            raise FactError('cache pricing and wire profile must be selected together')
        if self.budget.get('format') == 'okb-judge-budget-v2-draft' and self.runtime_binding is None:
            raise FactError('cache billing requires bound complete input counts')
        self.cost_group = cost_group
        self.cost_scope = cost_scope
        if (cost_group is None) != (cost_scope is None):
            raise FactError('group and scope must be supplied together')
        if cost_group is not None:
            if self.runtime_binding is None:
                raise FactError('aggregate cost group requires a runtime contract')
            cost_group.validate_scope(cost_scope, runtime_contract)
        self.path = Path(journal_path)
        self.sequence = 0; self.previous = '0'*64
        self.reserved = Decimal(0); self.estimated = Decimal(0)
        self.seen = set(); self.unmeasured = 0; self.halted = False
        # Exclusive creation: never reopen an uncertain batch for new spending.
        with self.path.open('x', encoding='utf8'):
            pass
        header = dict(kind='batch', journal_version=JOURNAL_VERSION, protocol=VERSION,
                          model=self.model, endpoint_sha256=hashlib.sha256(self.base.encode()).hexdigest(),
                          system_sha256=hashlib.sha256(SYSTEM.encode()).hexdigest(),
                          max_calls=self.max_calls, max_output_tokens=max_output_tokens,
                          budget=self.budget, hard_monetary_cap=False)
        if self.runtime_binding:
            header['runtime_contract_sha256'] = self.runtime_binding['contract_sha256']
        if wire_profile != 'legacy-chat-v1':
            header.update(**self.wire_options,
                output_token_tolerance=10 if self.model == 'qwen3.8-max' else 0)
        if self.cost_group is not None:
            header['cost_group_binding'] = dict(group_sha256=self.cost_group.policy['group_sha256'],
                scope_id=self.cost_scope)
        self._append(header)

    def _append(self, event):
        event = dict(event, recorded_at=datetime.now(timezone.utc).isoformat())
        content = dict(seq=self.sequence, previous_sha256=self.previous, event=event)
        row = dict(content, sha256=fingerprint(content))
        try:
            with self.path.open('a', encoding='utf8', newline='\n') as f:
                f.write(canonical(row)+'\n'); f.flush(); os.fsync(f.fileno())
        except OSError:
            self.halted = True
            raise FactError('journal persistence failed; batch halted') from None
        self.previous = row['sha256']; self.sequence += 1

    def _exchange(self, body):
        req = urllib.request.Request(self.base+'/chat/completions', data=canonical(body).encode(),
            headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json',
                     'User-Agent':user_agent()})
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=self.timeout) as response:
            raw = response.read(2_000_001)
            if len(raw)>2_000_000:
                raise FactError('response too large')
            return strict_json(raw.decode('utf8'))

    def judge(self, req):
        if (req.get('protocol') != VERSION
                or req.get('system_sha256') != hashlib.sha256(SYSTEM.encode()).hexdigest()):
            raise FactError('request protocol or system mismatch')
        key = fingerprint(req)
        if self.halted or key in self.seen:
            raise FactError('batch halted or request already attempted; no automatic resend')
        reserve = money(self.budget['reserve_per_request'])
        if self.calls >= self.max_calls or self.reserved+reserve > money(self.budget['max_total_cost']):
            raise FactError('call or reservation budget exhausted')
        from .cost_review import wire_body
        body = wire_body(req, self.model, self.max_output_tokens, **self.wire_options)
        if self.runtime_binding and self.runtime_binding['wire_requests'].get(key) != fingerprint(body):
            raise FactError('request is outside the cost-bound batch')
        group_attempt = None
        if self.cost_group is not None:
            try:
                group_attempt = self.cost_group.reserve(self.cost_scope, key)
            except Exception:
                self.halted = True
                raise
        # Persist before dispatch. A crash after this point is conservatively unknown.
        self._append(dict(kind='pending', request_sha256=key, request=req,
                          wire_request_sha256=fingerprint(body), reserve_cost=str(reserve)))
        self.calls += 1; self.seen.add(key); self.reserved += reserve
        try:
            envelope = self._exchange(body)
        except Exception as exc:
            self.unmeasured += 1
            self.halted = True
            self._append(dict(kind='unknown', request_sha256=key,
                              error_type=type(exc).__name__, usage=None, estimated_cost=None))
            if group_attempt is not None:
                self.cost_group.settle(group_attempt, None, batch_halted=True)
            raise FactError('transport failed; consumption unknown') from None
        usage = envelope.get('usage') if isinstance(envelope,dict) else None
        actual_model = envelope.get('model') if isinstance(envelope,dict) else None
        if not isinstance(actual_model,str) or not actual_model.strip() or len(actual_model)>200:
            actual_model = None
        cost = estimate_cost(usage, self.budget)
        cache_receipt = None
        returned_tier = envelope.get('service_tier') if isinstance(envelope, dict) else None
        if self.budget.get('format') == 'okb-judge-budget-v2-draft':
            from .cache_billing import response_cost
            cache_receipt = response_cost(usage, self.budget, returned_model=actual_model,
                service_tier=returned_tier, expected_input_tokens=self.runtime_binding['input_tokens'][key])
            cost = cache_receipt['estimated_cost'] if cache_receipt else None
        if self.wire_options['wire_profile'] == 'deepseek-thinking-v1' and not deepseek_usage_valid(usage):
            cost = None
        if self.runtime_binding and actual_model != self.model:
            cost = None
        if cost is None:
            self.unmeasured += 1
            self.halted = True  # no continuing spend on unaccountable provider usage
        else:
            self.estimated += money(cost)
            tolerance = 10 if self.model == 'qwen3.8-max' and self.wire_options['wire_profile'] != 'legacy-chat-v1' else 0
            if self.runtime_binding and usage['completion_tokens'] > self.max_output_tokens + tolerance:
                self.halted = True
            if money(cost)>reserve:
                self.reserved += money(cost)-reserve
                self.halted = True
        meta = dict(request_sha256=key, returned_model=actual_model,
                    usage=usage if usage_valid(usage) else None, estimated_cost=cost,
                    cost_source='token_usage_no_cache_discount' if cost is not None else 'unknown',
                    batch_halted=self.halted)
        if self.budget.get('format') == 'okb-judge-budget-v2-draft':
            meta.update(billing_profile='openai-chat-cache-v1',
                returned_service_tier=returned_tier if returned_tier in ('default', 'auto', 'flex', 'priority') else None,
                token_buckets=cache_receipt['token_buckets'] if cache_receipt else None,
                cost_source='four_declared_disjoint_token_buckets' if cache_receipt else 'unknown')
        try:
            if actual_model != self.model:
                raise FactError('returned model missing or mismatched')
            choice = envelope['choices'][0]
            if choice.get('finish_reason') != 'stop':
                raise FactError('response incomplete or nonstandard finish')
            result = validate_response(req, strict_json(choice['message']['content']))
        except Exception as exc:
            self._append(dict(kind='invalid_response', error_type=type(exc).__name__, **meta))
            if group_attempt is not None:
                self.cost_group.settle(group_attempt, cost, batch_halted=self.halted)
            raise FactError('provider output invalid; audit retained') from None
        # Durable terminal record before accepting a result into memory.
        self._append(dict(kind='completed', response=result, **meta))
        if group_attempt is not None:
            try:
                self.cost_group.settle(group_attempt, cost, batch_halted=self.halted)
            except Exception:
                self.halted = True
                raise
        self.records.append(dict(protocol=VERSION, model=self.model, request_sha256=key, response=result))
        return result

    def audit_summary(self):
        summary = dict(journal_version=JOURNAL_VERSION, reserved_cost=str(self.reserved),
                    estimated_known_cost=str(self.estimated), currency=self.budget['currency'],
                    unmeasured_calls=self.unmeasured, halted=self.halted,
                    max_output_tokens=self.max_output_tokens, hard_monetary_cap=False)
        if self.cost_group is not None:
            summary['cost_group'] = self.cost_group.summary()
        if self.budget.get('format') == 'okb-judge-budget-v2-draft':
            summary['billing_profile'] = 'openai-chat-cache-v1'
            summary['pricing_conditions'] = self.budget['pricing_conditions']
        return summary
