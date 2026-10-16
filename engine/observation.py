"""Read-only scope metadata. Configured paths are not verified live instances."""
import hashlib
import json


def file_scope(root, paths, parser, started_at, completed_at, *, full_capture=False):
    identity = json.dumps({'root': str(root.resolve()), 'paths': paths}, sort_keys=True)
    result = dict(scope_id='configured_memory_files', storage_kind='file',
                instance_id='configured:' + hashlib.sha256(identity.encode('utf8')).hexdigest(),
                method='file_scan:' + parser, collector_version='okb-file-scope-v1-draft',
                started_at=started_at, completed_at=completed_at,
                paths=paths, representation='decoded_utf8_replacement_then_parsed_entries',
                limitations=['configured_instance_not_verified', 'control_not_verified',
                             'parser_does_not_preserve_full_storage_text',
                             'scope_not_proven_to_cover_all_persistent_storage'])
    if full_capture:
        result['representation'] = 'strict_utf8_full_files_and_legacy_parsed_entries'
        result['collector_version'] = 'okb-full-file-scope-v1-draft'
        result['limitations'].remove('parser_does_not_preserve_full_storage_text')
        result['limitations'].append('metadata_race_checks_not_atomic_snapshot')
    return result


def partial_receipt(metadata, *, run_id, task_id, phase, evidence_id, item_count):
    """Do not promote read success, zero files, or parsed zero entries to complete."""
    return dict(version='okb-observation-receipt-v1-draft', run_id=run_id, task_id=task_id,
                **{k: metadata[k] for k in ('scope_id', 'storage_kind', 'instance_id', 'method',
                   'collector_version', 'started_at', 'completed_at')},
                window=phase, state='partial', enumeration_complete=False,
                instance_verified=False, control_passed=False, control_cleaned=False,
                item_count=item_count, evidence_refs=[evidence_id], control_refs=[],
                errors=list(metadata['limitations']))
