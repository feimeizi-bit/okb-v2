"""Versioned offline cost-to-runtime binding, never payment authorization."""
from decimal import localcontext

from .audited_facts import money, validate_budget
from .cost_review import exact_fields, inventory, scenario
from .fact_judge import fingerprint

VERSION = 'okb-judge-runtime-contract-v1-draft'
VERSION_REASONING = 'okb-judge-runtime-contract-v2-draft'
VERSION_DEEPSEEK = 'okb-judge-runtime-contract-v3-draft'
VERSION_CACHE = 'okb-judge-runtime-contract-v4-draft'


def runtime_contract(inv, quote, tokens, budget, max_calls):
    estimate = scenario(inv, quote, tokens)
    budget = validate_budget(budget)
    if estimate['status'] != 'declared_scenario_only' or not inv['requests']:
        raise ValueError('priced nonempty inventory required')
    if type(max_calls) is not int or max_calls != inv['calls_if_all_dispatched_once']:
        raise ValueError('call cap must equal the reviewed request count')
    cache_budget = budget.get('format') == 'okb-judge-budget-v2-draft'
    if cache_budget != (inv.get('wire_profile') == 'openai-cache-chat-v1'):
        raise ValueError('cache budget and wire profile must be selected together')
    pricing_fields = ('currency', 'rates', 'pricing_conditions', 'price_source', 'billing_profile') if cache_budget else ('currency', 'input_per_million', 'output_per_million', 'price_source')
    for field in pricing_fields:
        if budget[field] != quote[field]:
            raise ValueError('runtime budget and quote mismatch')
    for field in ('max_total_cost', 'reserve_per_request'):
        amount = money(budget[field])
        if len(budget[field]) > 48 or amount > money('1e12') or not -18 <= amount.as_tuple().exponent <= 12:
            raise ValueError('runtime amount outside contract limits')
    with localcontext() as context:
        context.prec = 80
        reservation = money(budget['reserve_per_request'])
        total = money(budget['max_total_cost'])
        reserved = reservation * max_calls
        if reservation < money(estimate['largest_request_cost']):
            raise ValueError('reservation smaller than the largest declared request cost')
        if total < reserved or total < money(estimate['total_cost']):
            raise ValueError('runtime budget cannot reserve the full reviewed batch')
    version = VERSION_CACHE if cache_budget else VERSION_DEEPSEEK if inv.get('wire_profile') == 'deepseek-thinking-v1' else VERSION_REASONING if inv.get('wire_profile') else VERSION
    content = dict(format=version,
        inventory=inv, quote=quote, token_counts=tokens,
        budget=budget, max_calls=max_calls, reserved_batch_cost=format(reserved, 'f'),
        scenario_total_cost=estimate['total_cost'], execution_authorized=False,
        hard_monetary_cap=False)
    return dict(content, contract_sha256=fingerprint(content))


def validate_runtime_contract(contract, requests, *, model, base_url,
                              max_output_tokens, budget, max_calls,
                              wire_profile='legacy-chat-v1', reasoning_effort=None):
    fields = {'format', 'inventory', 'quote', 'token_counts', 'budget', 'max_calls',
        'reserved_batch_cost', 'scenario_total_cost', 'execution_authorized',
        'hard_monetary_cap', 'contract_sha256'}
    exact_fields(contract, fields, 'runtime contract')
    inv = inventory(requests, model=model, base_url=base_url,
                    max_output_tokens=max_output_tokens, wire_profile=wire_profile,
                    reasoning_effort=reasoning_effort)
    rebuilt = runtime_contract(inv, contract['quote'], contract['token_counts'], budget, max_calls)
    if fingerprint(contract) != fingerprint(rebuilt):
        raise ValueError('runtime contract does not match the current batch or parameters')
    binding = dict(contract_sha256=rebuilt['contract_sha256'],
        wire_requests={row['request_sha256']: row['wire_request_sha256'] for row in inv['requests']})
    if rebuilt['format'] == VERSION_CACHE:
        counts = {row['wire_request_sha256']:row['input_tokens'] for row in contract['token_counts']['records']}
        binding['input_tokens'] = {row['request_sha256']:counts[row['wire_request_sha256']] for row in inv['requests']}
    return binding
