"""Offline, caller-declared cost scenarios. Never authorization or a hard cap."""
from decimal import Decimal, localcontext
import hashlib
from urllib.parse import urlsplit

from .fact_judge import SYSTEM, VERSION, canonical, fingerprint
from .audited_facts import money

VERSION_REVIEW = 'okb-judge-cost-review-v1-draft'
VERSION_REVIEW_REASONING = 'okb-judge-cost-review-v2-draft'
VERSION_REVIEW_DEEPSEEK = 'okb-judge-cost-review-v3-draft'
VERSION_REVIEW_CACHE = 'okb-judge-cost-review-v4-draft'
WIRE_PROFILES = ('legacy-chat-v1', 'reasoning-chat-v1', 'deepseek-thinking-v1', 'openai-cache-chat-v1')


def wire_body(req, model, max_output_tokens, *, wire_profile='legacy-chat-v1', reasoning_effort=None):
    if not isinstance(model, str) or not model.strip():
        raise ValueError('model required')
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 32768:
        raise ValueError('output limit must be 1..32768')
    if wire_profile not in WIRE_PROFILES:
        raise ValueError('unsupported judge wire profile')
    messages = [dict(role='system', content=SYSTEM), dict(role='user', content=canonical(req))]
    if wire_profile == 'openai-cache-chat-v1':
        if model != 'gpt-6.1-sol' or reasoning_effort not in ('low', 'medium', 'high', 'xhigh', 'max'):
            raise ValueError('cache billing profile requires gpt-6.1-sol and explicit effort')
        return dict(model=model, reasoning_effort=reasoning_effort,
            max_completion_tokens=max_output_tokens, service_tier='default',
            prompt_cache_options=dict(mode='implicit', ttl='30m'), messages=messages)
    if wire_profile == 'deepseek-thinking-v1':
        if model != 'deepseek-flash' or reasoning_effort not in ('low', 'high', 'max'):
            raise ValueError('DeepSeek profile requires deepseek-flash and explicit low/high/max effort')
        return dict(model=model, thinking=dict(type='enabled'), reasoning_effort=reasoning_effort,
                    max_tokens=max_output_tokens, messages=messages)
    if model.startswith('deepseek-') or model in ('deepseekflash4.1', 'DeepSeek-V4.1-Flash'):
        raise ValueError('selected DeepSeek model requires its native thinking wire profile')
    if wire_profile == 'reasoning-chat-v1':
        if reasoning_effort not in ('low', 'medium', 'high', 'xhigh', 'max'):
            raise ValueError('explicit reasoning effort required')
        body = dict(model=model, reasoning_effort=reasoning_effort,
                    max_completion_tokens=max_output_tokens, messages=messages)
        if model == 'gpt-6.1-sol':
            body['prompt_cache_options'] = dict(mode='explicit', ttl='30m')
        return body
    if reasoning_effort is not None:
        raise ValueError('reasoning effort requires the reasoning wire profile')
    if model in ('gpt-6.1-sol', 'qwen3.8-max'):
        raise ValueError('selected reasoning model requires a total-completion-token wire profile')
    return dict(model=model, temperature=0, max_tokens=max_output_tokens,
                messages=messages)


def endpoint_hash(base_url):
    parsed = urlsplit(base_url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or any(c.isspace() for c in base_url)):
        raise ValueError('HTTPS base URL without credentials/query/fragment required')
    return hashlib.sha256(base_url.rstrip('/').encode()).hexdigest()


def inventory(requests, *, model, base_url, max_output_tokens,
              wire_profile='legacy-chat-v1', reasoning_effort=None):
    if bool(model) != bool(base_url):
        raise ValueError('model and base URL must be supplied together')
    # Validate limit even when the model is still undecided.
    wire_options = dict(wire_profile=wire_profile, reasoning_effort=reasoning_effort)
    wire_body({}, model or 'unselected', max_output_tokens, **wire_options)
    endpoint = endpoint_hash(base_url) if base_url else None
    seen = set(); rows = []
    for item in requests:
        req = item['request']; key = fingerprint(req)
        if (key != item['request_sha256'] or key in seen or req.get('protocol') != VERSION
                or req.get('system_sha256') != hashlib.sha256(SYSTEM.encode()).hexdigest()):
            raise ValueError('duplicate, modified or incompatible request')
        seen.add(key)
        body = wire_body(req, model or 'unselected', max_output_tokens, **wire_options)
        rows.append(dict(source=item['source'], request_sha256=key,
            messages_utf8_bytes=len(canonical(body['messages']).encode()),
            wire_request_sha256=fingerprint(body) if model else None,
            wire_utf8_bytes=len(canonical(body).encode()) if model else None))
    result = dict(format=VERSION_REVIEW, protocol=VERSION,
        system_sha256=hashlib.sha256(SYSTEM.encode()).hexdigest(), model=model,
        endpoint_sha256=endpoint, max_output_tokens=max_output_tokens,
        calls_if_all_dispatched_once=len(rows), automatic_retries=0,
        requests=rows, request_set_sha256=fingerprint(rows))
    if wire_profile != 'legacy-chat-v1':
        result.update(format=VERSION_REVIEW_CACHE if wire_profile == 'openai-cache-chat-v1' else VERSION_REVIEW_DEEPSEEK if wire_profile == 'deepseek-thinking-v1' else VERSION_REVIEW_REASONING, **wire_options,
            output_token_tolerance=10 if model == 'qwen3.8-max' else 0)
    return result


def exact_fields(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError('invalid '+label+' fields')


def nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def scenario(inv, quote=None, tokens=None):
    if quote is None and tokens is None:
        missing = ['price_quote', 'bound_input_token_counts', 'user_budget_approval',
                   'provider_usage_and_output_limit_semantics']
        if inv['model'] is None:
            missing.insert(0, 'model_and_endpoint')
        return dict(status='inputs_missing', currency=None, total_cost=None,
                    prerequisites=missing)
    if quote is None or tokens is None or inv['model'] is None:
        raise ValueError('scenario requires model, endpoint, quote and token counts')
    if inv.get('wire_profile') == 'openai-cache-chat-v1' or quote.get('format') == 'okb-judge-price-quote-v2-draft':
        from .cache_billing import scenario as cache_scenario
        return cache_scenario(inv, quote, tokens)
    exact_fields(quote, ('format', 'model', 'endpoint_sha256', 'currency',
        'input_per_million', 'output_per_million', 'price_source', 'observed_on'), 'quote')
    exact_fields(tokens, ('format', 'model', 'endpoint_sha256', 'protocol',
        'request_set_sha256', 'method', 'source', 'records'), 'token counts')
    if (quote['format'] != 'okb-judge-price-quote-v1-draft'
            or tokens['format'] != 'okb-judge-token-counts-v1-draft'):
        raise ValueError('unsupported estimate input version')
    for field in ('model', 'endpoint_sha256'):
        if quote[field] != inv[field] or tokens[field] != inv[field]:
            raise ValueError('estimate model/endpoint mismatch')
    if (tokens['protocol'] != inv['protocol'] or tokens['request_set_sha256'] != inv['request_set_sha256']
            or not all(nonblank(v) for v in (tokens['method'], tokens['source'], quote['price_source']))):
        raise ValueError('token protocol, request set or provenance mismatch')
    from datetime import date
    if not isinstance(quote['observed_on'], str) or date.fromisoformat(quote['observed_on']).isoformat() != quote['observed_on']:
        raise ValueError('quote date must be YYYY-MM-DD')
    currency = quote['currency']
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha() or not currency.isupper():
        raise ValueError('invalid currency')
    # Bound precision/size, including exponent forms, before doing Decimal math.
    prices = []
    for name in ('input_per_million', 'output_per_million'):
        price = money(quote[name])
        if len(quote[name]) > 48 or price > Decimal('1e12') or not -18 <= price.as_tuple().exponent <= 12:
            raise ValueError('price outside review limits')
        prices.append(price)
    if not isinstance(tokens['records'], list):
        raise ValueError('token records must be a list')
    expected = {r['wire_request_sha256']: r for r in inv['requests']}
    counts = {}
    for row in tokens['records']:
        exact_fields(row, ('wire_request_sha256', 'input_tokens'), 'token record')
        key, n = row['wire_request_sha256'], row['input_tokens']
        if key not in expected or key in counts or type(n) is not int or not 1 <= n <= 10**9:
            raise ValueError('invalid, duplicate or unbound token count')
        counts[key] = n
    if set(counts) != set(expected):
        raise ValueError('token coverage mismatch')
    with localcontext() as ctx:
        ctx.prec = 80
        output_budget = inv['max_output_tokens'] + inv.get('output_token_tolerance', 0)
        costs = [(Decimal(n)*prices[0]+Decimal(output_budget)*prices[1])/Decimal(10**6)
                 for n in counts.values()]
        total, largest = sum(costs, Decimal(0)), max(costs, default=Decimal(0))
    return dict(status='declared_scenario_only', currency=currency,
        total_cost=format(total, 'f'), largest_request_cost=format(largest, 'f'),
        input_tokens=sum(counts.values()), output_tokens_if_every_call_hits_limit=len(costs)*output_budget,
        price_quote=quote, token_count_method=tokens['method'], token_count_source=tokens['source'],
        assumptions=['One dispatch per request; no retries, cache discount or extra fees.',
            'Caller-declared input counts include full chat framing; not independently verified.',
            'All requests reach the chosen output limit; provider billing semantics still require verification.',
            'Quote applicability and freshness are caller assertions; no online price verification.'],
        prerequisites=['user_budget_approval', 'provider_usage_and_output_limit_semantics'],
        hard_currency_cap=False)
