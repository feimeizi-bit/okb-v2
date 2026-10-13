"""Offline inventory checks, never a certificate of independent holdout custody."""
import collections
import hashlib
import re

from semantic_robustness import build_requests
from scoring.fact_judge import strict_json

FORMAT = 'okb-semantic-split-manifest-v1-draft'
DEVELOPMENT = 'public-author-designed-development-tests-not-independent-holdout'
CANDIDATE = 'independent-validation-candidate-not-yet-verified'
RISKS = ('negation', 'wrong_subject', 'stale_fact', 'quotation', 'injection',
         'missing_evidence', 'artifact_reply_scope')
LABELS = ('supported', 'contradicted', 'insufficient')
THRESHOLDS = dict(cases=120, families=30, each_proposed_label=30,
                  equivalence_groups=12, cases_per_equivalence_group=3)
BINDINGS = ('model_config_sha256', 'prompt_sha256', 'requirements_sha256')
EXPOSURES = ('tuner_seen_candidate_content', 'tuner_seen_candidate_labels',
             'validation_results_seen')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fields(obj, names):
    if not isinstance(obj, dict) or set(obj) != set(names):
        raise ValueError('unexpected fields')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value):
        raise ValueError('invalid identifier')
    return value.casefold()


def digest(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('invalid fingerprint')


def inventory(sources):
    """Read explicitly supplied bytes; manifest data cannot request file access."""
    if not isinstance(sources, dict) or not sources:
        raise ValueError('sources required')
    descriptors, cases, source_ids = [], {}, set()
    for source_id, raw in sources.items():
        key = identifier(source_id)
        if key in source_ids:
            raise ValueError('source aliases collide')
        source_ids.add(key)
        corpus = strict_json(raw.decode('utf8'))
        if corpus.get('purpose') not in (DEVELOPMENT, CANDIDATE):
            raise ValueError('explicit source purpose required')
        if type(corpus.get('case_count')) is not int:
            raise ValueError('integer case count required')
        requests = build_requests(corpus)
        descriptors.append(dict(source_id=source_id, sha256=sha(raw), purpose=corpus['purpose']))
        ids = set()
        for case, req in zip(corpus['cases'], requests):
            cid = identifier(case['id'])
            if cid in ids:
                raise ValueError('case aliases collide')
            ids.add(cid)
            if case['equivalence_group'] is not None:
                identifier(case['equivalence_group'])
            cases[source_id, case['id']] = (case, req, corpus['purpose'])
    return descriptors, cases


def template(sources):
    descriptors, cases = inventory(sources)
    # Existing public cases must never be silently converted to locked validation.
    if any(s['purpose'] != DEVELOPMENT for s in descriptors):
        raise ValueError('template preparation supports public development only')
    return dict(format=FORMAT, sources=descriptors,
        protocol={k:None for k in BINDINGS},
        custody=dict(custodian_id=None, custodian_is_non_tuner=None, rater_ids=[],
                     **{k:None for k in EXPOSURES}),
        cases=[dict(source_id=sid, case_id=cid, request_sha256=req['request_sha256'],
                    family_id=None, split='development', risk_tags=[])
               for (sid, cid), (_, req, _) in cases.items()])


def review(manifest, sources):
    fields(manifest, ('format', 'sources', 'protocol', 'custody', 'cases'))
    if manifest['format'] != FORMAT:
        raise ValueError('unsupported manifest')
    descriptors, source_cases = inventory(sources)
    if not isinstance(manifest['sources'], list):
        raise ValueError('source list required')
    for item in manifest['sources']:
        fields(item, ('source_id', 'sha256', 'purpose'))
        identifier(item['source_id']); digest(item['sha256'])
    if sorted(manifest['sources'], key=lambda s:s['source_id']) != sorted(descriptors, key=lambda s:s['source_id']):
        raise ValueError('source bytes, purpose or coverage mismatch')
    fields(manifest['protocol'], BINDINGS)
    for value in manifest['protocol'].values():
        if value is not None:
            digest(value)
    custody = manifest['custody']
    fields(custody, ('custodian_id', 'custodian_is_non_tuner', 'rater_ids', *EXPOSURES))
    if custody['custodian_id'] is not None:
        identifier(custody['custodian_id'])
    for key in ('custodian_is_non_tuner', *EXPOSURES):
        if custody[key] is not None and type(custody[key]) is not bool:
            raise ValueError('explicit bool or null required')
    if not isinstance(custody['rater_ids'], list):
        raise ValueError('rater list required')
    raters = [identifier(v) for v in custody['rater_ids']]
    if len(set(raters)) != len(raters):
        raise ValueError('duplicate rater aliases')
    if not isinstance(manifest['cases'], list):
        raise ValueError('case list required')
    seen, requests, families, groups, candidates = set(), set(), {}, {}, []
    missing_families = []
    for row in manifest['cases']:
        fields(row, ('source_id', 'case_id', 'request_sha256', 'family_id', 'split', 'risk_tags'))
        identifier(row['source_id']); identifier(row['case_id']); digest(row['request_sha256'])
        key = row['source_id'], row['case_id']
        if key in seen or key not in source_cases:
            raise ValueError('duplicate or unknown case')
        seen.add(key)
        case, req, purpose = source_cases[key]
        if row['request_sha256'] != req['request_sha256']:
            raise ValueError('request binding mismatch')
        if req['request_sha256'] in requests:
            raise ValueError('duplicate request across sources')
        requests.add(req['request_sha256'])
        split = row['split']
        if split not in ('development', 'validation_candidate'):
            raise ValueError('invalid split')
        if split == 'validation_candidate' and purpose != CANDIDATE:
            raise ValueError('public development cannot become validation')
        family = identifier(row['family_id']) if row['family_id'] is not None else None
        if family is None:
            missing_families.append(dict(source_id=key[0], case_id=key[1]))
        elif family in families and families[family] != split:
            raise ValueError('family crosses splits')
        elif family is not None:
            families[family] = split
        tags = row['risk_tags']
        if (not isinstance(tags, list) or any(t not in RISKS for t in tags)
                or len(set(tags)) != len(tags)):
            raise ValueError('invalid risk tags')
        group = case['equivalence_group']
        if group is not None:
            group = identifier(group)
            members = groups.setdefault(group, [])
            if members and (members[0]['family'], members[0]['split'], members[0]['label']) != (family, split, case['expected']):
                raise ValueError('equivalence group crosses family/split or proposed labels')
            members.append(dict(family=family, split=split, label=case['expected']))
        if split == 'validation_candidate':
            candidates.append((row, case))
    if seen != set(source_cases):
        raise ValueError('source case coverage mismatch')
    label_counts = {label:sum(c['expected'] == label for _, c in candidates) for label in LABELS}
    risk_counts = {tag:sum(tag in r['risk_tags'] for r, _ in candidates) for tag in RISKS}
    eligible_groups = sum(len(g) >= THRESHOLDS['cases_per_equivalence_group'] and
        g[0]['split'] == 'validation_candidate' and g[0]['family'] is not None for g in groups.values())
    candidate_families = sum(split == 'validation_candidate' for split in families.values())
    checks = dict(all_cases_have_declared_family=not missing_families,
        candidate_case_count=len(candidates) >= THRESHOLDS['cases'],
        candidate_family_count=candidate_families >= THRESHOLDS['families'],
        proposed_label_counts=all(v >= THRESHOLDS['each_proposed_label'] for v in label_counts.values()),
        equivalence_groups=eligible_groups >= THRESHOLDS['equivalence_groups'],
        declared_risk_coverage=all(risk_counts.values()),
        protocol_fingerprints_present=all(manifest['protocol'].values()),
        non_tuner_custodian_declared=custody['custodian_id'] is not None and custody['custodian_is_non_tuner'] is True,
        two_rater_aliases_declared=len(raters) == 2,
        no_tuner_or_results_exposure_declared=all(custody[k] is False for k in EXPOSURES))
    return dict(format='okb-semantic-split-review-v1-draft', total_cases=len(seen),
        development_cases=len(seen)-len(candidates), validation_candidate_cases=len(candidates),
        candidate_families=candidate_families, proposed_label_counts=label_counts,
        declared_risk_counts=risk_counts, eligible_equivalence_groups=eligible_groups,
        missing_family_cases=missing_families, thresholds=dict(THRESHOLDS), checks=checks,
        unmet_checks=[k for k,v in checks.items() if not v], offline_checks_passed=all(checks.values()),
        protocol=dict(manifest['protocol']), custody_declarations=dict(custody),
        independence_verified=False, freeze_accepted=False, human_labels_verified=False,
        semantic_reliability_validated=False, release_authorized=False,
        limitations=['Family, equivalence and risk declarations require independent content review; semantic duplicates can evade hashes.',
                     'Only supplied sources are checked; omitted tuning data cannot be detected.',
                     'Expected labels are author proposals, not adjudicated human reference answers.',
                     'Fingerprints do not certify custody, prior exposure, identities, freeze time or absence of later tuning.',
                     'Any validation used for tuning must be retired; new generalization needs a new locked set.'])
