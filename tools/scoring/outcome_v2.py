"""S2 dual-axis aggregation; explicit opt-in, preserves v1 result semantics."""
VERSION = 'okb-four-layer-v2'


def aggregate(source_valid, checks):
    if type(source_valid) is not bool:
        raise ValueError('source validity must be boolean')
    if not checks and source_valid:
        raise ValueError('mandatory checks cannot be empty')
    if len({c['id'] for c in checks}) != len(checks):
        raise ValueError('duplicate check id')
    for c in checks:
        if c['result'] not in ('supported', 'contradicted', 'insufficient'):
            raise ValueError('invalid check state')
        if c['on_failure'] not in ('omitted', 'confused', 'wrongly_persisted', 'wrongly_reused'):
            raise ValueError('invalid error class')
    failed = [c['id'] for c in checks if c['result'] == 'contradicted']
    unresolved = [c['id'] for c in checks if c['result'] == 'insufficient']
    result = dict(task_outcome='unassessable', score=None, error_class=None,
                  classification_status='unresolved', violated_checks=failed,
                  unresolved_checks=unresolved)
    if not source_valid:
        result['reason_code'] = 'source_invalid'
        return result
    first_failure = next((i for i,c in enumerate(checks) if c['result']=='contradicted'), None)
    if first_failure is not None:
        result.update(task_outcome='fail', score=0, reason_code='mandatory_requirement_failed')
        # A preceding unresolved requirement could change the primary class.
        if not any(c['result']=='insufficient' for c in checks[:first_failure]):
            result.update(error_class=checks[first_failure]['on_failure'], classification_status='resolved')
    elif unresolved:
        result['reason_code'] = 'mandatory_check_unresolved'
    else:
        result.update(task_outcome='pass', score=1, error_class='remembered',
                      classification_status='resolved', reason_code='all_mandatory_supported')
    return result


def project_row(row, ordered_checks):
    out = dict(row)
    vector = {c['id']: c for c in row['layer2'] + row['layer3']}
    checks = [vector[c['id']] for c in ordered_checks] if row['layer1']['valid'] else []
    axes = aggregate(row['layer1']['valid'], checks)
    out.update(axes, version=VERSION, status='assessed' if axes['task_outcome']!='unassessable' else 'unassessable',
               verdict=axes['error_class'], legacy_report_compatible=False)
    out['layer4'] = dict(row['layer4'])
    out['layer4']['priority'] = 'explicit plan order; unresolved predecessors block class, not proven failure'
    out['layer4']['classification_status'] = axes['classification_status']
    return out
