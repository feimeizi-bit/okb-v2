"""Strict, registered single-stratum diagnostics; no live execution or ranking."""
import copy
import math
import re
from pathlib import Path

from .fact_judge import VERSION as FACT_VERSION, SYSTEM, canonical, fingerprint, strict_json
from .four_layer import validate_plan
from .outcome_v2 import aggregate
from .report_v2 import build_report, load_bundle, sha

PLAN_VERSION = 'okb-multirun-plan-v1-draft'
IMPORT_VERSION = 'okb-multirun-imports-v1-draft'
REPORT_VERSION = 'okb-multirun-report-v1-draft'


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def fields(obj, names, label):
    require(isinstance(obj, dict) and set(obj) == set(names.split()), 'invalid fields: ' + label)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def identity(agent):
    """Declared metadata comparison, NOT proof of effective runtime configuration.

New producers must record adapter_version/executor_version/observation_scope in
manifest agent config. Unknown values cannot inherit values from registration.
"""
    if 'runtime_identity' in agent:
        require(agent['runtime_identity'].get('format')=='okb-runtime-identity-v1-draft'
                and agent['runtime_identity'].get('errors')==[], 'runtime identity unresolved')
    for k in ('name', 'agent_kind'):
        require(nonempty(agent.get(k)), 'missing agent identity: ' + k)
    # model 由 Agent CLI 上报才算已知：openclaw 从 meta 取、Hermes-Q 根本不给（实测 manifest
    # 里 model=None）。None 是「如实未上报」，仍然跨 run 可比（两边都必须同样未上报）。
    require(agent.get('model') is None or nonempty(agent.get('model')), 'invalid agent model')
    require(agent['agent_kind'] in ('real', 'mock', 'cli-template'), 'unknown agent kind')
    temp = agent.get('temperature')
    require(temp is None or (type(temp) in (int, float) and math.isfinite(temp)),
            'invalid agent temperature')
    config = agent.get('config')
    require(isinstance(config, dict), 'missing agent config')
    for k in ('adapter_version', 'executor_version'):
        require(nonempty(config.get(k)), 'missing configured ' + k)
    scope = config.get('observation_scope')
    require(isinstance(scope, dict) and bool(scope), 'missing observation scope')
    require(isinstance(agent.get('config_files'), list) and isinstance(agent.get('capabilities'), dict)
            and bool(agent['capabilities']), 'missing configuration provenance')
    paths = []
    for f in agent['config_files']:
        require(isinstance(f, dict) and nonempty(f.get('path')) and f.get('exists') is True
                and isinstance(f.get('sha256'), str) and bool(re.fullmatch('[a-f0-9]{64}', f['sha256'])),
                'unavailable config file fingerprint')
        paths.append(f['path'])
    require(len(set(paths)) == len(paths), 'duplicate config file path')
    return dict(agent_kind=agent['agent_kind'], model=agent['model'], temperature=temp,
        adapter_version=config['adapter_version'], executor_version=config['executor_version'],
        observation_scope=scope, config_sha256=fingerprint({k:agent[k] for k in
            ('config', 'config_files', 'capabilities')}))


def validate_registration(reg, paper, plan, paper_sha, plan_sha):
    fields(reg, 'format comparison_id paper_sha256 plan_sha256 protocol agents batches attempt_policy', 'registration')
    require(reg['format'] == PLAN_VERSION and nonempty(reg['comparison_id']), 'invalid comparison identity')
    require(reg['paper_sha256'] == paper_sha and reg['plan_sha256'] == plan_sha, 'registration source mismatch')
    validate_plan(plan, paper, paper_sha)
    require(reg['attempt_policy'] == 'first_attempt_only', 'unsupported attempt policy')
    protocol = reg['protocol']
    fields(protocol, 'outcome_version evidence_policy mode judge_model fact_protocol system_sha256', 'protocol')
    require(protocol['outcome_version'] == 'okb-four-layer-v2'
            and protocol['evidence_policy'] in ('scoped-v1', 'scoped-v2'),
            'multirun requires a scoped evidence policy with v2 outcomes')
    require(protocol['mode'] in ('prepare', 'replay', 'live'), 'invalid mode')
    require(protocol['judge_model'] is None if protocol['mode']=='prepare' else nonempty(protocol['judge_model']),
            'invalid judge model')
    require(protocol['fact_protocol'] == FACT_VERSION and protocol['system_sha256'] == sha(SYSTEM.encode()),
            'incompatible current judge protocol')
    agents = reg['agents']
    require(isinstance(agents, dict) and bool(agents) and all(nonempty(a) for a in agents), 'missing planned agents')
    for a, config in agents.items():
        fields(config, 'agent_kind model temperature adapter_version executor_version observation_scope config_sha256', 'agent identity')
        require(config['agent_kind'] in ('real','mock','cli-template') and all(nonempty(config[k]) for k in
                ('adapter_version','executor_version')), 'unknown planned configuration')
        require(config['model'] is None or nonempty(config['model']), 'invalid planned model')
        require(config['temperature'] is None or (type(config['temperature']) in (int,float)
                and math.isfinite(config['temperature'])), 'invalid planned temperature')
        require(isinstance(config['observation_scope'],dict) and bool(config['observation_scope']), 'missing planned observation scope')
        require(isinstance(config['config_sha256'],str) and bool(re.fullmatch('[a-f0-9]{64}',config['config_sha256'])), 'invalid config fingerprint')
    selected = {s['sample_id'] for s in plan['samples']}
    batches, attempts, primary, children = {}, {}, {}, set()
    require(isinstance(reg['batches'],list) and bool(reg['batches']), 'empty planned batches')
    for batch in reg['batches']:
        fields(batch, 'run_id tasks', 'batch')
        rid = batch['run_id']
        require(nonempty(rid) and rid not in batches, 'duplicate/empty planned run')
        require(isinstance(batch['tasks'],list) and bool(batch['tasks']), 'empty planned tasks')
        batches[rid] = batch
        tids = set()
        for t in batch['tasks']:
            fields(t, 'task_id agent sample_id repeat attempt_id retry_of', 'planned task')
            require(nonempty(t['task_id']) and t['task_id'] not in tids, 'duplicate/empty task in batch')
            tids.add(t['task_id'])
            require(t['agent'] in agents and t['sample_id'] in selected, 'unregistered agent/sample')
            require(type(t['repeat']) is int and t['repeat'] >= 1, 'invalid global repeat')
            aid = t['attempt_id']
            require(nonempty(aid) and aid not in attempts, 'duplicate/empty attempt')
            unit = (t['agent'], t['sample_id'], t['repeat'])
            if t['retry_of'] is None:
                require(unit not in primary, 'duplicate primary comparison unit')
                primary[unit] = aid
            else:
                prev = attempts.get(t['retry_of'])
                require(prev is not None and prev['unit']==unit and prev['run_id']!=rid and t['retry_of'] not in children,
                        'retry must follow one prior attempt in same unit')
                children.add(t['retry_of'])
            attempts[aid] = dict(t, run_id=rid, unit=unit)
    # Balanced planned units; missing imports do not shrink these denominators.
    units = {a:{(s,r) for ag,s,r in primary if ag==a} for a in agents}
    reference = next(iter(units.values()))
    require(all(u==reference for u in units.values()) and {s for s,r in reference}==selected,
            'planned agents must cover identical selected sample/repeat units')
    for sid in selected:
        repeats = sorted(r for s,r in reference if s==sid)
        require(repeats == list(range(1,len(repeats)+1)), 'global repeats must be contiguous from 1')
    return batches, attempts, primary


def load_comparison(registration_path, expected_sha, imports_path, paper_path, plan_path):
    paths = list(map(lambda p:Path(p).resolve(), (registration_path, imports_path, paper_path, plan_path)))
    snapshots = {p:p.read_bytes() for p in paths}
    require(sha(snapshots[paths[0]]) == expected_sha, 'registration fingerprint mismatch')
    reg, imports = [strict_json(snapshots[p].decode('utf8')) for p in paths[:2]]
    paper = [strict_json(s) for s in snapshots[paths[2]].decode('utf8').splitlines() if s.strip()]
    plan = strict_json(snapshots[paths[3]].decode('utf8'))
    batches, attempts, primary = validate_registration(reg, paper, plan, sha(snapshots[paths[2]]),sha(snapshots[paths[3]]))
    fields(imports, 'format batches', 'imports')
    require(imports['format']==IMPORT_VERSION and isinstance(imports['batches'],list), 'invalid import manifest')
    imported, seen_dirs, observed, run_files, batch_audits = set(), set(), {}, {}, []
    for entry in imports['batches']:
        fields(entry, 'run_id score_dir run_dir', 'import batch')
        rid=entry['run_id']
        require(rid in batches and rid not in imported, 'unknown or duplicate imported run')
        require(nonempty(entry['score_dir']) and nonempty(entry['run_dir']), 'empty import path')
        score, run = [(paths[1].parent/entry[k]).resolve() for k in ('score_dir','run_dir')]
        require(score != run and score not in seen_dirs and run not in seen_dirs, 'duplicate imported directory')
        seen_dirs.update((score,run));imported.add(rid)
        bundle, snap, files = load_bundle(score,run,paths[2],paths[3],verify_trace=True)
        for p,data in snap.items():
            require(p not in snapshots or snapshots[p]==data, 'shared source drift')
            snapshots[p]=data
        run_files[run]=files
        mf=strict_json(snap[(run/'manifest.json').resolve()].decode('utf8'))
        require(bundle['run_id']==rid, 'run identity mismatch')
        if mf.get('attempt_retention') is not None:
            require(all(mf.get('runtime_identity_unchanged',{}).get(a) is True for a in bundle['agents']),
                    'runtime configuration drift or verification incomplete')
        pr=reg['protocol'];sm=bundle['summary']
        require(sm['format']==pr['outcome_version'] and sm.get('evidence_policy','legacy')==pr['evidence_policy']
                and sm['mode']==pr['mode'] and sm.get('model')==pr['judge_model'], 'incompatible score protocol')
        planned={t['task_id']:t for t in batches[rid]['tasks']}
        require({t['task_id'] for t in bundle['tasks']}==set(planned)
                and {t['task_id'] for t in mf['tasks']}==set(planned), 'batch task coverage mismatch')
        require(set(bundle['agents'])=={t['agent'] for t in planned.values()}, 'batch declared agents mismatch')
        for name,agent in bundle['agents'].items():
            require(canonical(identity(agent))==canonical(reg['agents'][name]), 'agent configuration mismatch: '+name)
        for task in mf['tasks']:
            require(type(task.get('attempts')) is int and task['attempts']==1,
                    'in-run retries require separate retained attempts')
            # Reported actual models, when supplied, may not contradict config.
            require(isinstance(task.get('models'),list), 'missing observed model metadata')
            name=planned[task['task_id']]['agent']
            require(all(m==reg['agents'][name]['model'] for m in task['models']), 'observed model mismatch')
            require(reg['agents'][name]['agent_kind']!='real' or task['status']!='ok' or bool(task['models']),
                    'successful real task has no observed model')
        require(not mf.get('resumed') and not mf.get('merged_from'), 'resumed/merged runs not supported')
        for task in bundle['tasks']:
            p=planned[task['task_id']]
            require(task['agent']==p['agent'] and task['sample_id']==p['sample_id'], 'planned task identity mismatch')
            t=copy.deepcopy(task)
            t.update(source_task_id=task['task_id'],source_run_id=rid,source_repeat=task['repeat'],
                     task_id=p['attempt_id'],attempt_id=p['attempt_id'],retry_of=p['retry_of'],
                     repeat='-r'+str(p['repeat']),global_repeat=p['repeat'],import_status='loaded')
            observed[p['attempt_id']]=t
        batch_audits.append(dict(run_id=rid, trace=bundle['trace_verification'],call_audit=sm.get('call_audit')))
    bysample={s['id']:s for s in paper}
    history=[]
    for aid,p in attempts.items():
        t=observed.get(aid)
        if t is None:
            t=dict(task_id=aid,attempt_id=aid,retry_of=p['retry_of'],source_task_id=p['task_id'],
                source_run_id=p['run_id'],source_repeat=None,agent=p['agent'],sample_id=p['sample_id'],
                repeat='-r'+str(p['repeat']),global_repeat=p['repeat'],dimension=bysample[p['sample_id']]['dimension'],
                execution_status='not_imported',duration_ms=None,import_status='not_imported',
                **aggregate(False,[]),layer1=dict(valid=False,reason='planned_batch_not_imported'),checks=[],
                check_definitions=[],comparison='not_comparable',evidence_refs=[],evidence=[],script=bysample[p['sample_id']]['script'])
        t['included_in_primary_metrics']=p['retry_of'] is None
        history.append(t)
    primary_tasks=[t for t in history if t['included_in_primary_metrics']]
    bundle=dict(tasks=primary_tasks,agents=reg['agents'],selected={s['sample_id']:s for s in plan['samples']},samples=bysample,
        run_id=reg['comparison_id'],summary=dict(mode=reg['protocol']['mode'],evidence_policy=reg['protocol']['evidence_policy']),
        lock=dict(plan_sha256=reg['plan_sha256']),trace_verification=dict(status='verified_imports_missing_batches_explicit',batches=batch_audits))
    report=build_report(bundle)
    report.update(format=REPORT_VERSION,comparison_id=reg['comparison_id'],registration_sha256=expected_sha,
        configuration_identities=reg['agents'],protocol=reg['protocol'],attempt_policy=reg['attempt_policy'],
        planned_batches=len(batches),imported_batches=len(imported),missing_batches=sorted(set(batches)-imported),
        planned_attempts=len(attempts),imported_attempts=len(observed),planned_primary_tasks=len(primary),
        attempt_history=history,judge_call_audit=batch_audits,registration_precedence_independently_verified=False)
    report['limits'] += ['Only first planned attempts enter primary metrics; retries never replace failures.',
        'Configuration fields are declarations compared exactly, not independently verified runtime identities.',
        'Registration hash pins supplied bytes; it does not prove registration predates observed results.',
        'One configuration stratum and identical scoring plan per comparison; incompatible strata require separate reports.',
        'No confidence intervals or formal six-dimension aggregation; all reports remain private diagnostics.']
    report['source_fingerprints']={str(p):sha(data) for p,data in snapshots.items()}
    return report,snapshots,run_files,seen_dirs
