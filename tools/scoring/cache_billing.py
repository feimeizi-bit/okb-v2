"""Versioned four-bucket declared pricing; never account billing verification."""
from datetime import date
from decimal import Decimal, localcontext
import re

from .audited_facts import money, usage_valid
from .fact_judge import FactError

PROFILE = 'openai-chat-cache-v1'
WIRE_PROFILE = 'openai-cache-chat-v1'
QUOTE_VERSION = 'okb-judge-price-quote-v2-draft'
BUDGET_VERSION = 'okb-judge-budget-v2-draft'
CONTEXT_WINDOW = 1050000
RATE_FIELDS = ('input_uncached_per_million', 'input_cache_hit_per_million',
    'input_cache_write_per_million', 'output_per_million')


def exact_fields(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise FactError('invalid '+label+' fields')


def amount(value):
    result = money(value)
    if len(value) > 48 or result > money('1e12') or not -18 <= result.as_tuple().exponent <= 12:
        raise FactError('billing amount outside limits')
    return result


def validate_rates(rates):
    exact_fields(rates, RATE_FIELDS, 'cache rates')
    for value in rates.values():
        amount(value)
    return dict(rates)


def validate_conditions(conditions):
    exact_fields(conditions, ('service_tier', 'min_input_tokens', 'max_input_tokens'), 'pricing conditions')
    if (conditions['service_tier'] != 'default'
            or type(conditions['min_input_tokens']) is not int
            or type(conditions['max_input_tokens']) is not int
            or not 1 <= conditions['min_input_tokens'] <= conditions['max_input_tokens'] <= 10**9):
        raise FactError('invalid declared pricing tier or input interval')
    return dict(conditions)


def validate_budget(budget):
    exact_fields(budget, ('format', 'billing_profile', 'currency', 'max_total_cost',
        'reserve_per_request', 'rates', 'pricing_conditions', 'price_source'), 'cache budget')
    if budget['format'] != BUDGET_VERSION or budget['billing_profile'] != PROFILE:
        raise FactError('unsupported cache budget version')
    if not isinstance(budget['currency'], str) or not re.fullmatch(r'[A-Z]{3}', budget['currency']):
        raise FactError('invalid cache budget currency')
    if not isinstance(budget['price_source'], str) or not budget['price_source'].strip():
        raise FactError('cache price provenance required')
    total = amount(budget['max_total_cost'])
    reserve = amount(budget['reserve_per_request'])
    if total <= 0 or reserve <= 0 or reserve > total:
        raise FactError('positive full cache budget reservation required')
    validate_rates(budget['rates'])
    validate_conditions(budget['pricing_conditions'])
    return dict(budget, rates=dict(budget['rates']), pricing_conditions=dict(budget['pricing_conditions']))


def scenario(inv, quote, tokens):
    exact_fields(quote, ('format', 'billing_profile', 'model', 'endpoint_sha256', 'currency',
        'rates', 'pricing_conditions', 'price_source', 'observed_on'), 'cache quote')
    exact_fields(tokens, ('format', 'model', 'endpoint_sha256', 'protocol', 'request_set_sha256',
        'method', 'source', 'records'), 'cache token counts')
    if (quote['format'] != QUOTE_VERSION or quote['billing_profile'] != PROFILE
            or inv.get('wire_profile') != WIRE_PROFILE or inv['model'] != 'gpt-6.1-sol'
            or tokens['format'] != 'okb-judge-token-counts-v1-draft'):
        raise FactError('incompatible cache pricing, tokens or wire version')
    for field in ('model', 'endpoint_sha256'):
        if quote[field] != inv[field] or tokens[field] != inv[field]:
            raise FactError('cache pricing model or endpoint mismatch')
    if tokens['protocol'] != inv['protocol'] or tokens['request_set_sha256'] != inv['request_set_sha256']:
        raise FactError('cache token request-set mismatch')
    if any(not isinstance(value, str) or not value.strip() for value in
            (quote['price_source'], tokens['method'], tokens['source'])):
        raise FactError('cache pricing and count provenance required')
    if not isinstance(quote['observed_on'], str) or date.fromisoformat(quote['observed_on']).isoformat() != quote['observed_on']:
        raise FactError('invalid cache quote date')
    if not isinstance(quote['currency'], str) or not re.fullmatch(r'[A-Z]{3}', quote['currency']):
        raise FactError('invalid cache quote currency')
    rates = validate_rates(quote['rates'])
    conditions = validate_conditions(quote['pricing_conditions'])
    if not isinstance(tokens['records'], list):
        raise FactError('cache token records must be a list')
    expected = {row['wire_request_sha256'] for row in inv['requests']}
    counts = {}
    for row in tokens['records']:
        exact_fields(row, ('wire_request_sha256', 'input_tokens'), 'cache token record')
        key = row['wire_request_sha256']
        count = row['input_tokens']
        if (key not in expected or key in counts or type(count) is not int
                or not conditions['min_input_tokens'] <= count <= conditions['max_input_tokens']
                or count+inv['max_output_tokens'] > CONTEXT_WINDOW):
            raise FactError('unbound cache input count or pricing interval mismatch')
        counts[key] = count
    if set(counts) != expected:
        raise FactError('incomplete cache token coverage')
    with localcontext() as context:
        context.prec = 80
        input_price = max(amount(rates[field]) for field in RATE_FIELDS[:3])
        output_limit = inv['max_output_tokens']
        costs = [(Decimal(count)*input_price+Decimal(output_limit)*amount(rates['output_per_million']))/Decimal(1000000)
            for count in counts.values()]
        total = sum(costs, Decimal(0))
        largest = max(costs, default=Decimal(0))
    return dict(status='declared_scenario_only', currency=quote['currency'],
        total_cost=format(total, 'f'), largest_request_cost=format(largest, 'f'),
        input_tokens=sum(counts.values()), output_tokens_if_every_call_hits_limit=len(costs)*output_limit,
        price_quote=quote, token_count_method=tokens['method'], token_count_source=tokens['source'],
        billing_profile=PROFILE, assumptions=[
            'Every input token uses the most expensive of the three declared input buckets.',
            'Every output reaches the selected total-generation limit; reasoning is not added twice.',
            'Full chat counts, pricing interval, routing and rates are caller declarations, not verified account facts.',
            'No unquoted fees, currency conversion, cache discounts, retries or gateway transformations are assumed.'],
        prerequisites=['user_budget_approval', 'account_pricing_and_gateway_applicability', 'provider_usage_and_output_limit_semantics'],
        hard_currency_cap=False)


def response_cost(usage, budget, *, returned_model, service_tier, expected_input_tokens):
    validate_budget(budget)
    if returned_model != 'gpt-6.1-sol' or service_tier != budget['pricing_conditions']['service_tier']:
        return None
    allowed = {'prompt_tokens', 'completion_tokens', 'total_tokens', 'prompt_tokens_details', 'completion_tokens_details'}
    if not usage_valid(usage) or set(usage)-allowed:
        return None
    totals = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    if any(type(usage.get(field)) is not int or not 0 <= usage[field] <= 10**9 for field in totals):
        return None
    prompt = usage['prompt_tokens']
    completion = usage['completion_tokens']
    conditions = budget['pricing_conditions']
    if (prompt != expected_input_tokens or prompt+completion != usage['total_tokens']
            or usage['total_tokens'] > CONTEXT_WINDOW
            or not conditions['min_input_tokens'] <= prompt <= conditions['max_input_tokens']):
        return None
    details = usage.get('prompt_tokens_details')
    if not isinstance(details, dict):
        return None
    if any(type(details.get(field)) is not int or details[field] < 0 for field in ('cached_tokens', 'cache_write_tokens')):
        return None
    for field,value in details.items():
        if field in ('cached_tokens', 'cache_write_tokens'):
            continue
        if field == 'text_tokens':
            if type(value) is not int or not 0 <= value <= prompt:
                return None
        elif value != 0:
            return None
    cached = details['cached_tokens']
    written = details['cache_write_tokens']
    if cached+written > prompt:
        return None
    output_details = usage.get('completion_tokens_details')
    if output_details is not None:
        if not isinstance(output_details, dict):
            return None
        for field,value in output_details.items():
            if field in ('reasoning_tokens', 'text_tokens', 'accepted_prediction_tokens', 'rejected_prediction_tokens'):
                if type(value) is not int or not 0 <= value <= completion:
                    return None
            elif value != 0:
                return None
    buckets = dict(input_uncached_tokens=prompt-cached-written, input_cache_hit_tokens=cached,
        input_cache_write_tokens=written, output_tokens=completion)
    with localcontext() as context:
        context.prec = 80
        rates = budget['rates']
        cost = (Decimal(buckets['input_uncached_tokens'])*amount(rates['input_uncached_per_million'])
            + Decimal(cached)*amount(rates['input_cache_hit_per_million'])
            + Decimal(written)*amount(rates['input_cache_write_per_million'])
            + Decimal(completion)*amount(rates['output_per_million']))/Decimal(1000000)
    return dict(estimated_cost=format(cost, 'f'), token_buckets=buckets, billing_profile=PROFILE)
