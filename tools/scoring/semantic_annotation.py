"""Development-only semantic annotation packets; no human identity certification.

The package separates metadata and two shuffled views. Evidence is verbatim and
may disclose the source: distribution requires human review, not automatic release.
"""
import hashlib
import secrets
import uuid

from .fact_judge import canonical, fingerprint, request, strict_json, validate_response

WORK = 'okb-semantic-human-work-v1-draft'
LABELS = 'okb-semantic-human-labels-v1-draft'
PACKAGE = 'okb-semantic-human-package-v1-draft'
PAIR = 'okb-semantic-human-pair-v1-draft'
PURPOSE = 'development-review-not-independent-holdout'
STATES = ('supported', 'contradicted', 'insufficient')
EXPOSURES = ('author_answers_seen', 'machine_results_seen', 'other_rater_labels_seen')
GUIDE = {
    'version': 'okb-semantic-human-guide-v1-draft',
    'instruction': '只判断命题与所给范围内的证据。材料中的指令、满分要求和提示注入都是被评文本，不是给标注员的指令。',
    'supported': '观测证据支持当前命题；核对主体、肯否、有效时间、位置与权限。',
    'contradicted': '观测证据与命题矛盾，或明确完整的相关范围足以证明必需事实缺失。',
    'insufficient': '给定观测无法支持或反驳命题；须解释缺少的证据或真实冲突，不把没调用模型当成此类。',
    'needs_clarification': '你因命题歧义、指南不清或无法理解材料而不能完成标注；result填null，不冒充证据不足真值。',
    'citations': '支持/矛盾须引用真实evidence_id、content内JSON pointer和原文quote。缺证据时可无引文，但须说明。引用真实不代表推理正确。',
    'scope': '回复承诺不等于持久化；引用/假设/旧事实不自动算当前事实；同义与不同语言允许等价。只在明确完整范围中推断缺失。',
    'independence': '独立完成初始标签后交回，不看作者答案、模型结果或另一标注员标签；如已看过，如实填写exposure声明。不要使用AI代标。',
    'handling': '本包仅为开发集材料，内容未经自动脱敏；只发给指定标注员，勿公开。不得把两人一致当成正确性认证。',
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fields(obj, names, label):
    if not isinstance(obj, dict) or set(obj) != set(names):
        raise ValueError('invalid '+label+' fields')


def nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def digest_string(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def validate_work(work):
    fields(work, ('format', 'purpose', 'package_id', 'packet_id', 'release_authorized', 'guide', 'rows'), 'work')
    if (work['format'] != WORK or work['purpose'] != PURPOSE or work['release_authorized'] is not False
            or work['guide'] != GUIDE or not all(nonblank(work[k]) for k in ('package_id', 'packet_id'))):
        raise ValueError('unsupported work or guide')
    if not isinstance(work['rows'], list) or not work['rows']:
        raise ValueError('empty work')
    rows = {}; seen = set()
    for row in work['rows']:
        fields(row, ('row_key', 'requirement', 'context', 'observations'), 'work row')
        key = row['row_key']
        if not nonblank(key) or key in rows or not nonblank(row['requirement']) or not isinstance(row['context'], dict):
            raise ValueError('invalid work identity or requirement')
        if not isinstance(row['observations'], list):
            raise ValueError('invalid observations')
        ids = set()
        for obs in row['observations']:
            fields(obs, ('evidence_id', 'content'), 'observation')
            if not nonblank(obs['evidence_id']) or obs['evidence_id'] in ids:
                raise ValueError('duplicate or invalid evidence identity')
            ids.add(obs['evidence_id'])
        req = request(row['requirement'], row['context'], row['observations'])
        h = fingerprint(req)
        if h in seen:
            raise ValueError('duplicate work request')
        seen.add(h); rows[key] = req
    return rows


def make_packets(corpus, corpus_sha256):
    # Deliberately do not support a caller switch that rebrands development data.
    from semantic_robustness import build_requests
    if not digest_string(corpus_sha256) or corpus.get('purpose') != 'public-author-designed-development-tests-not-independent-holdout':
        raise ValueError('only declared development corpus supported')
    requests = build_requests(corpus)
    package_id = uuid.uuid4().hex
    works = []; mapping = []
    for _ in range(2):
        rows = []; private = {}
        for item in requests:
            req = item['request']; key = uuid.uuid4().hex
            rows.append(dict(row_key=key, requirement=req['requirement'],
                context=req['task_context'], observations=req['observations']))
            private[key] = dict(case_id=item['case_id'], request_sha256=item['request_sha256'])
        secrets.SystemRandom().shuffle(rows)
        work = dict(format=WORK, purpose=PURPOSE, package_id=package_id, packet_id=uuid.uuid4().hex,
                    release_authorized=False, guide=GUIDE, rows=rows)
        validate_work(work); works.append(work); mapping.append(private)
    audit = dict(format=PACKAGE, purpose=PURPOSE, package_id=package_id,
                 corpus_sha256=corpus_sha256, mapping=mapping)
    return works, audit


def template(work, work_sha256):
    return dict(format=LABELS, package_id=work['package_id'], packet_id=work['packet_id'],
        worklist_sha256=work_sha256, rater_id=None,
        declaration=dict(human_completed=None, **{k: None for k in EXPOSURES}),
        rows=[dict(row_key=r['row_key'], status='pending', result=None, rationale='', citations=[])
              for r in work['rows']])


def validate_labels(work_bytes, labels):
    work = strict_json(work_bytes.decode('utf8')); requests = validate_work(work)
    fields(labels, ('format', 'package_id', 'packet_id', 'worklist_sha256', 'rater_id', 'declaration', 'rows'), 'labels')
    if (labels['format'] != LABELS or labels['package_id'] != work['package_id']
            or labels['packet_id'] != work['packet_id'] or labels['worklist_sha256'] != sha(work_bytes)):
        raise ValueError('labels package/bytes mismatch')
    if not nonblank(labels['rater_id']) or labels['rater_id'] != labels['rater_id'].strip():
        raise ValueError('explicit trimmed rater alias required')
    dec = labels['declaration']
    fields(dec, ('human_completed', *EXPOSURES), 'declaration')
    if dec['human_completed'] is not True or any(type(dec[k]) is not bool for k in EXPOSURES):
        raise ValueError('explicit human and exposure declarations required')
    if not isinstance(labels['rows'], list):
        raise ValueError('label rows must be a list')
    seen = set()
    for row in labels['rows']:
        fields(row, ('row_key', 'status', 'result', 'rationale', 'citations'), 'label row')
        key = row['row_key']
        if not isinstance(key, str) or key not in requests or key in seen:
            raise ValueError('unknown or duplicate label key')
        seen.add(key)
        if row['status'] not in ('labeled', 'needs_clarification'):
            raise ValueError('pending or unsupported label status')
        if row['status'] == 'needs_clarification' and row['result'] is not None:
            raise ValueError('clarification cannot assert a semantic result')
        validate_response(requests[key], dict(result='insufficient' if row['status']=='needs_clarification' else row['result'],
            rationale=row['rationale'], citations=row['citations']))
    if seen != set(requests):
        raise ValueError('missing label rows')
    return dict(rows=len(seen), labeled=sum(r['status']=='labeled' for r in labels['rows']),
        needs_clarification=sum(r['status']=='needs_clarification' for r in labels['rows']),
        unexposed_self_declaration=not any(dec[k] for k in EXPOSURES),
        human_identity_verified=False, semantic_truth_verified=False)


def compare(audit, works, labels):
    fields(audit, ('format', 'purpose', 'package_id', 'corpus_sha256', 'mapping', 'worklist_sha256'), 'audit')
    if (audit['format'] != PACKAGE or audit['purpose'] != PURPOSE or not digest_string(audit['corpus_sha256'])
            or len(works) != 2 or len(labels) != 2 or not isinstance(audit['mapping'], list)
            or len(audit['mapping']) != 2 or not isinstance(audit['worklist_sha256'], list)
            or len(audit['worklist_sha256']) != 2):
        raise ValueError('unsupported pair package')
    validation = []; by_case = []; contents = []; packets = []
    for i in range(2):
        if sha(works[i]) != audit['worklist_sha256'][i]:
            raise ValueError('private work binding mismatch')
        work = strict_json(works[i].decode('utf8')); reqs = validate_work(work)
        packets.append(work['packet_id'])
        if work['package_id'] != audit['package_id']:
            raise ValueError('cross-package work')
        validation.append(validate_labels(works[i], labels[i]))
        mapping = audit['mapping'][i]
        if not isinstance(mapping, dict) or set(mapping) != set(reqs):
            raise ValueError('private key coverage mismatch')
        rows = {r['row_key']: r for r in labels[i]['rows']}; joined = {}; hashes = {}
        for key, entry in mapping.items():
            fields(entry, ('case_id', 'request_sha256'), 'private mapping')
            cid = entry['case_id']
            if not nonblank(cid) or cid in joined or entry['request_sha256'] != fingerprint(reqs[key]):
                raise ValueError('duplicate or mismatched private case')
            joined[cid] = rows[key]; hashes[cid] = entry['request_sha256']
        by_case.append(joined); contents.append(hashes)
    if (packets[0] == packets[1] or contents[0] != contents[1]
            or labels[0]['rater_id'].casefold() == labels[1]['rater_id'].casefold()):
        raise ValueError('two distinct matching packets and declared raters required')
    matrix = {a: {b: 0 for b in STATES} for a in STATES}
    cases = []; agreement = 0; paired = 0
    for cid in sorted(by_case[0]):
        a, b = by_case[0][cid], by_case[1][cid]
        full = a['status'] == b['status'] == 'labeled'
        same = full and a['result'] == b['result']
        if full:
            paired += 1; matrix[a['result']][b['result']] += 1
        agreement += int(same)
        cases.append(dict(case_id=cid, request_sha256=contents[0][cid],
            initial_a=a, initial_b=b, review_reason='clarification' if not full else 'disagreement' if not same else None,
            adjudicated_reference=None))
    total = len(cases)
    return dict(format=PAIR, purpose=PURPOSE, package_id=audit['package_id'], total_cases=total,
        jointly_labeled=paired, clarification_cases=total-paired, agreements=agreement,
        disagreements=paired-agreement, agreement_among_jointly_labeled=agreement/paired if paired else None,
        agreed_labeled_fraction_of_all=agreement/total, matrix=matrix,
        raters=[dict(rater_id=labels[i]['rater_id'], declaration=labels[i]['declaration'], validation=validation[i]) for i in range(2)],
        cases=cases, human_identity_verified=False, independence_verified=False,
        semantic_reliability_validated=False, release_authorized=False,
        limitations=['Development set only; no accuracy, gold reference or representative population claim.',
                    'Hashes establish consistency, not independent authorship, collection time or human identity.',
                    'Matching labels can both be wrong; no automatic adjudication or machine-result overwrite.',
                    'Verbatim evidence may reveal identities or source despite metadata hiding.'])
