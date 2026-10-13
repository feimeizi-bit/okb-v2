"""S1 draft receipt validator. Not a substitute for adapter/source verification."""
from datetime import datetime
from pathlib import Path
import json
from jsonschema import Draft202012Validator, FormatChecker

SCHEMA = Path(__file__).resolve().parents[2] / 'schemas/observation-receipt.v1.schema.json'


def validate_receipt(receipt, *, run_id, task_id, instance_id, evidence_ids):
    """Validate shape and bindings against independently verified run evidence."""
    schema = json.loads(SCHEMA.read_text(encoding='utf8'))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(receipt)
    if (receipt['run_id'], receipt['task_id'], receipt['instance_id']) != (run_id, task_id, instance_id):
        raise ValueError('observation identity mismatch')
    refs = receipt['evidence_refs'] + receipt['control_refs']
    if not set(refs) <= set(evidence_ids):
        raise ValueError('observation refers to unverified evidence')
    start = datetime.fromisoformat(receipt['started_at'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(receipt['completed_at'].replace('Z', '+00:00'))
    if start > end:
        raise ValueError('observation time reversed')
    complete = receipt['state'] in ('complete_empty', 'complete_nonempty')
    if complete:
        if not (receipt['enumeration_complete'] and receipt['instance_verified']
                and receipt['control_passed'] and receipt['control_cleaned']
                and receipt['control_refs'] and not receipt['errors']):
            raise ValueError('complete observation lacks control or coverage proof')
        if receipt['item_count'] is None:
            raise ValueError('complete observation needs item count')
        if (receipt['item_count'] == 0) != (receipt['state'] == 'complete_empty'):
            raise ValueError('empty/nonempty count mismatch')
    return receipt


def dependency_status(receipt, requirement):
    """Call only after source verification and validate_receipt; no inferred scope."""
    if receipt['scope_id'] != requirement['scope_id']:
        return {'sufficient': False, 'reason': 'scope_mismatch'}
    if receipt['window'] != requirement['window']:
        return {'sufficient': False, 'reason': 'window_mismatch'}
    if receipt['state'] not in ('complete_empty', 'complete_nonempty'):
        return {'sufficient': False, 'reason': 'observation_' + receipt['state']}
    return {'sufficient': True, 'reason': 'complete_declared_scope'}
