"""Offline-declared judge groups with one durable, serial reservation ledger.

Declarations are not human approval or remote monetary caps. Unknown or pending
consumption blocks every scope; no recovery, retry, or automatic reconciliation.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import os
from pathlib import Path
import re
import sqlite3

from .audited_facts import money
from .cost_review import exact_fields
from .cost_runtime import runtime_contract
from .fact_judge import FactError, canonical, fingerprint, strict_json

VERSION = 'okb-judge-cost-group-v1-draft'
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z')
DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def bounded_money(value):
    amount = money(value)
    if len(value) > 48 or amount > money('1e12') or not -18 <= amount.as_tuple().exponent <= 12:
        raise FactError('group amount outside limits')
    return amount


def contract_scope(scope_id, contract):
    if not isinstance(scope_id, str) or not IDENTIFIER.fullmatch(scope_id):
        raise FactError('invalid cost scope identifier')
    try:
        rebuilt = runtime_contract(contract['inventory'], contract['quote'],
            contract['token_counts'], contract['budget'], contract['max_calls'])
        if canonical(rebuilt) != canonical(contract):
            raise FactError('group requires a complete unchanged runtime contract')
        requests = sorted(row['request_sha256'] for row in contract['inventory']['requests'])
        return dict(scope_id=scope_id, contract_sha256=contract['contract_sha256'],
            currency=contract['budget']['currency'], request_sha256=requests,
            max_calls=contract['max_calls'], reserve_per_request=contract['budget']['reserve_per_request'],
            reserved_batch_cost=contract['reserved_batch_cost'])
    except (KeyError, TypeError):
        raise FactError('invalid group runtime contract') from None


def cost_group(scopes, *, group_id, currency, max_total_cost, ledger_path):
    content = dict(format=VERSION, group_id=group_id, currency=currency,
        max_total_cost=max_total_cost, ledger_path=str(Path(ledger_path).resolve()),
        scopes=[contract_scope(scope_id, contract) for scope_id, contract in scopes],
        execution_authorized=False, hard_monetary_cap=False)
    policy = dict(content, group_sha256=fingerprint(content))
    return validate_group(policy)


def validate_group(policy):
    exact_fields(policy, {'format', 'group_id', 'currency', 'max_total_cost', 'ledger_path',
        'scopes', 'execution_authorized', 'hard_monetary_cap', 'group_sha256'}, 'cost group')
    content = {key:value for key,value in policy.items() if key != 'group_sha256'}
    if (policy['format'] != VERSION or policy['execution_authorized'] is not False
            or policy['hard_monetary_cap'] is not False
            or policy['group_sha256'] != fingerprint(content)):
        raise FactError('invalid cost group version, flags or fingerprint')
    if not isinstance(policy['group_id'], str) or not IDENTIFIER.fullmatch(policy['group_id']):
        raise FactError('invalid cost group identifier')
    currency = policy['currency']
    if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency):
        raise FactError('invalid group currency')
    ledger = policy['ledger_path']
    if not isinstance(ledger, str) or not Path(ledger).is_absolute() or str(Path(ledger).resolve()) != ledger:
        raise FactError('group must bind one canonical absolute ledger path')
    total = bounded_money(policy['max_total_cost'])
    scopes = policy['scopes']
    if total <= 0 or not isinstance(scopes, list) or not 1 <= len(scopes) <= 1000:
        raise FactError('positive group budget and scopes required')
    seen = set()
    with localcontext() as context:
        context.prec = 80
        reserved = Decimal(0)
        for scope in scopes:
            exact_fields(scope, {'scope_id', 'contract_sha256', 'currency', 'request_sha256',
                'max_calls', 'reserve_per_request', 'reserved_batch_cost'}, 'cost scope')
            identifier = scope['scope_id']
            if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier) or identifier in seen:
                raise FactError('invalid or duplicate scope identifier')
            seen.add(identifier)
            hashes = scope['request_sha256']
            if (scope['currency'] != currency or not isinstance(scope['contract_sha256'], str)
                    or not DIGEST.fullmatch(scope['contract_sha256'])
                    or type(scope['max_calls']) is not int or not 1 <= scope['max_calls'] <= 10000
                    or not isinstance(hashes, list) or len(hashes) != scope['max_calls']
                    or any(not isinstance(value, str) or not DIGEST.fullmatch(value) for value in hashes)
                    or hashes != sorted(set(hashes))):
                raise FactError('invalid scope requests, currency or contract')
            reserve = bounded_money(scope['reserve_per_request'])
            batch = bounded_money(scope['reserved_batch_cost'])
            if reserve <= 0 or reserve * scope['max_calls'] != batch:
                raise FactError('invalid scope reservation')
            reserved += batch
        if reserved > total:
            raise FactError('group cannot reserve every declared scope')
    return strict_json(canonical(policy))


def initialize_ledger(policy):
    policy = validate_group(policy)
    path = Path(policy['ledger_path'])
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(descriptor)
    connection = None
    try:
        connection = sqlite3.connect(path, isolation_level=None)
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('CREATE TABLE policy (content TEXT NOT NULL)')
        connection.execute('INSERT INTO policy VALUES (?)', (canonical(policy),))
        connection.execute('CREATE TABLE events (seq INTEGER PRIMARY KEY, payload TEXT NOT NULL, previous_sha256 TEXT NOT NULL, sha256 TEXT NOT NULL)')
        connection.execute("CREATE TRIGGER no_event_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'append only'); END")
        connection.execute("CREATE TRIGGER no_event_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'append only'); END")
        connection.commit()
    finally:
        if connection is not None:
            connection.close()


class CostGroup:
    def __init__(self, policy):
        self.policy = validate_group(policy)
        self.path = Path(self.policy['ledger_path'])
        self.scopes = {scope['scope_id']:scope for scope in self.policy['scopes']}
        self.summary()

    @contextmanager
    def _transaction(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path.as_uri()+'?mode=rw', uri=True,
                isolation_level=None, timeout=5)
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute('SELECT content FROM policy').fetchall()
            if rows != [(canonical(self.policy),)]:
                raise FactError('cost ledger policy mismatch')
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError):
            raise FactError('cost ledger unavailable; dispatch denied') from None
        finally:
            if connection is not None:
                if connection.in_transaction:
                    connection.rollback()
                connection.close()

    def validate_scope(self, scope_id, contract):
        if self.scopes.get(scope_id) != contract_scope(scope_id, contract):
            raise FactError('runtime contract outside declared group scope')
        summary = self.summary()
        if summary['halted']:
            raise FactError('cost group pending, unknown or halted')
        return self.scopes[scope_id]

    def _state(self, connection):
        attempts = {}
        previous = '0'*64
        rows = connection.execute('SELECT seq, payload, previous_sha256, sha256 FROM events ORDER BY seq').fetchall()
        for sequence, row in enumerate(rows):
            ordinal, payload, predecessor, recorded_hash = row
            event = strict_json(payload)
            content = dict(seq=ordinal, previous_sha256=predecessor, event=event)
            if ordinal != sequence or predecessor != previous or fingerprint(content) != recorded_hash:
                raise FactError('cost ledger chain mismatch')
            previous = recorded_hash
            if not isinstance(event, dict):
                raise FactError('invalid cost ledger event')
            kind = event.get('kind')
            if kind == 'reserved':
                exact_fields(event, {'kind', 'scope_id', 'request_sha256', 'attempt_sha256', 'recorded_at'}, 'group reserve event')
                scope = self.scopes.get(event['scope_id'])
                identifier = fingerprint(dict(scope_id=event['scope_id'], request_sha256=event['request_sha256']))
                if scope is None or event['request_sha256'] not in scope['request_sha256'] or identifier != event['attempt_sha256'] or identifier in attempts:
                    raise FactError('duplicate or unreviewed group attempt')
                attempts[identifier] = dict(scope_id=event['scope_id'], reserve=scope['reserve_per_request'],
                    cost=None, state='pending', halted=True)
            elif kind == 'settled':
                exact_fields(event, {'kind', 'attempt_sha256', 'estimated_cost', 'batch_halted', 'recorded_at'}, 'group settlement event')
                attempt = attempts.get(event['attempt_sha256'])
                if attempt is None or attempt['state'] != 'pending' or type(event['batch_halted']) is not bool:
                    raise FactError('invalid or duplicate group settlement')
                cost = event['estimated_cost']
                if cost is not None:
                    bounded_money(cost)
                attempt.update(cost=cost, state='unknown' if cost is None else 'known',
                    halted=cost is None or event['batch_halted'] or bounded_money(cost) > bounded_money(attempt['reserve']))
            else:
                raise FactError('invalid cost ledger event kind')
        return attempts, len(rows), previous

    def _summary(self, attempts):
        with localcontext() as context:
            context.prec = 80
            known = sum((bounded_money(attempt['cost']) for attempt in attempts.values() if attempt['cost'] is not None), Decimal(0))
            reserved = sum((max(bounded_money(attempt['reserve']), bounded_money(attempt['cost']) if attempt['cost'] is not None else Decimal(0)) for attempt in attempts.values()), Decimal(0))
            cap = bounded_money(self.policy['max_total_cost'])
            return dict(format=VERSION, group_id=self.policy['group_id'], group_sha256=self.policy['group_sha256'],
                currency=self.policy['currency'], max_total_cost=self.policy['max_total_cost'],
                committed_reservation_cost=format(reserved, 'f'), estimated_known_cost=format(known, 'f'),
                remaining_reservation_capacity=format(max(Decimal(0), cap-reserved), 'f'),
                attempted_calls=len(attempts), pending_calls=sum(attempt['state']=='pending' for attempt in attempts.values()),
                unknown_calls=sum(attempt['state']=='unknown' for attempt in attempts.values()),
                halted=reserved > cap or any(attempt['halted'] for attempt in attempts.values()),
                hard_monetary_cap=False, human_approval_verified=False)

    def _append(self, connection, event, sequence, previous):
        event = dict(event, recorded_at=datetime.now(timezone.utc).isoformat())
        content = dict(seq=sequence, previous_sha256=previous, event=event)
        connection.execute('INSERT INTO events VALUES (?, ?, ?, ?)',
            (sequence, canonical(event), previous, fingerprint(content)))

    def summary(self):
        with self._transaction() as connection:
            attempts, _, _ = self._state(connection)
            return self._summary(attempts)

    def reserve(self, scope_id, request_sha256):
        scope = self.scopes.get(scope_id)
        if scope is None or request_sha256 not in scope['request_sha256']:
            raise FactError('request outside declared group scope')
        identifier = fingerprint(dict(scope_id=scope_id, request_sha256=request_sha256))
        with self._transaction() as connection:
            attempts, sequence, previous = self._state(connection)
            summary = self._summary(attempts)
            if identifier in attempts or summary['halted']:
                raise FactError('cost group already attempted, pending, unknown or halted')
            with localcontext() as context:
                context.prec = 80
                if bounded_money(summary['committed_reservation_cost']) + bounded_money(scope['reserve_per_request']) > bounded_money(self.policy['max_total_cost']):
                    raise FactError('aggregate reservation budget exhausted')
            self._append(connection, dict(kind='reserved', scope_id=scope_id,
                request_sha256=request_sha256, attempt_sha256=identifier), sequence, previous)
        return identifier

    def settle(self, attempt_sha256, estimated_cost, *, batch_halted):
        if estimated_cost is not None:
            bounded_money(estimated_cost)
        if type(batch_halted) is not bool:
            raise FactError('invalid group halt flag')
        with self._transaction() as connection:
            attempts, sequence, previous = self._state(connection)
            if attempt_sha256 not in attempts or attempts[attempt_sha256]['state'] != 'pending':
                raise FactError('group settlement without unique pending attempt')
            self._append(connection, dict(kind='settled', attempt_sha256=attempt_sha256,
                estimated_cost=estimated_cost, batch_halted=batch_halted), sequence, previous)
