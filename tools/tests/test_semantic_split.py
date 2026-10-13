"""Synthetic manifests test leakage guards, not real independence or semantics."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from scoring.fact_judge import canonical
from scoring.semantic_split import template, review, inventory, DEVELOPMENT, CANDIDATE, RISKS, BINDINGS, sha
from review_semantic_split import main


def encode(value):
    return canonical(value).encode('utf8')


def corpus(n=3, prefix='d', purpose=DEVELOPMENT):
    return dict(format='okb-semantic-robustness-v1', purpose=purpose, case_count=n,
        cases=[dict(id=f'{prefix}{i}', requirement=f'SYNTHETIC fact {prefix}{i}',
            context={}, observations=[dict(evidence_id='E1', content=f'fixture {prefix}{i}')],
            expected=('supported','contradicted','insufficient')[i//3%3],
            category='synthetic-only', equivalence_group=f'{prefix}-g{i//3}') for i in range(n)])


def ready_fixture():
    sources = {'dev':encode(corpus()), 'candidate':encode(corpus(120, 'v', CANDIDATE))}
    m = template({'dev':sources['dev']})
    descriptors, cases = inventory(sources); m['sources'] = descriptors
    m['cases'] = [dict(source_id=sid, case_id=cid, request_sha256=req['request_sha256'],
        family_id=f'{sid}-family-{i//3}', split='development' if sid=='dev' else 'validation_candidate',
        risk_tags=list(RISKS)) for i, ((sid,cid), (_,req,_)) in enumerate(cases.items())]
    m['protocol'] = {k:'a'*64 for k in BINDINGS}
    m['custody'] = dict(custodian_id='synthetic-keeper', custodian_is_non_tuner=True,
        rater_ids=['synthetic-a','synthetic-b'], tuner_seen_candidate_content=False,
        tuner_seen_candidate_labels=False, validation_results_seen=False)
    return m, sources


class SemanticSplitTests(unittest.TestCase):
    def setUp(self):
        self.m, self.sources = ready_fixture()

    def test_numeric_pass_is_never_independence_or_freeze(self):
        r = review(self.m, self.sources)
        self.assertTrue(r['offline_checks_passed'])
        self.assertEqual(r['validation_candidate_cases'], 120)
        self.assertEqual(r['candidate_families'], 40)
        for field in ('independence_verified','freeze_accepted','human_labels_verified',
                      'semantic_reliability_validated','release_authorized'):
            self.assertIs(r[field], False)

    def test_real_33_case_template_has_no_invented_families(self):
        sources = {'dev':(ROOT/'samples/semantic-robustness.v1.json').read_bytes()}
        m = template(sources); r = review(m, sources)
        self.assertEqual(r['total_cases'], 33)
        self.assertEqual(len(r['missing_family_cases']), 33)
        self.assertEqual(r['validation_candidate_cases'], 0)
        self.assertFalse(r['offline_checks_passed'])

    def test_candidate_cannot_be_auto_prepared(self):
        with self.assertRaises(ValueError): template(self.sources)

    def test_development_cannot_be_promoted(self):
        self.m['cases'][0]['split'] = 'validation_candidate'
        with self.assertRaisesRegex(ValueError, 'public development'): review(self.m, self.sources)

    def test_cross_split_family_alias_rejected(self):
        self.m['cases'][3]['family_id'] = self.m['cases'][0]['family_id'].upper()
        with self.assertRaisesRegex(ValueError, 'family crosses'): review(self.m, self.sources)

    def test_equivalence_cannot_be_spread_over_families(self):
        self.m['cases'][4]['family_id'] = 'different-family'
        with self.assertRaisesRegex(ValueError, 'equivalence group'): review(self.m, self.sources)

    def test_equivalence_group_cannot_cross_splits(self):
        c = json.loads(self.sources['candidate']); c['cases'][0]['equivalence_group'] = 'd-g0'
        self.sources['candidate'] = encode(c)
        self.m['sources'], _ = inventory(self.sources)
        with self.assertRaisesRegex(ValueError, 'equivalence group'): review(self.m, self.sources)

    def test_equivalence_with_conflicting_proposed_labels_rejected(self):
        c = json.loads(self.sources['candidate']); c['cases'][1]['expected'] = 'contradicted'
        self.sources['candidate'] = encode(c); self.m['sources'], _ = inventory(self.sources)
        with self.assertRaisesRegex(ValueError, 'proposed labels'): review(self.m, self.sources)

    def test_duplicate_request_with_different_ids_rejected(self):
        c = corpus(purpose=CANDIDATE)
        for i, case in enumerate(c['cases']): case['id'] = f'copy{i}'
        sources = dict(self.sources, copy=encode(c))
        self.m['sources'], cases = inventory(sources)
        self.m['cases'] += [dict(source_id=sid, case_id=cid, request_sha256=req['request_sha256'],
            family_id='copied', split='validation_candidate', risk_tags=[]) for (sid,cid),(_,req,_) in cases.items() if sid=='copy']
        with self.assertRaisesRegex(ValueError, 'duplicate request'): review(self.m, sources)

    def test_missing_extra_duplicate_case_rejected(self):
        for change in ('missing','extra','duplicate'):
            m = copy.deepcopy(self.m)
            if change=='missing': m['cases'].pop()
            elif change=='extra': m['cases'].append(dict(m['cases'][0],case_id='unknown'))
            else: m['cases'].append(m['cases'][0])
            with self.subTest(change=change), self.assertRaises(ValueError): review(m,self.sources)

    def test_changed_source_bytes_even_whitespace_rejected(self):
        with self.assertRaisesRegex(ValueError, 'source bytes'):
            review(self.m, dict(self.sources, dev=self.sources['dev']+b'\n'))

    def test_request_fingerprint_mismatch_rejected(self):
        self.m['cases'][0]['request_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'request binding'): review(self.m,self.sources)

    def test_missing_family_is_unmet_not_silent_singleton(self):
        for row in self.m['cases'][3:6]: row['family_id'] = None
        r = review(self.m,self.sources)
        self.assertIn('all_cases_have_declared_family', r['unmet_checks'])
        self.assertEqual(len(r['missing_family_cases']), 3)

    def test_author_label_counts_not_gold_and_cannot_ignore_minimum(self):
        c = json.loads(self.sources['candidate'])
        for case in c['cases']: case['expected'] = 'supported'
        self.sources['candidate'] = encode(c); self.m['sources'], _ = inventory(self.sources)
        r = review(self.m,self.sources)
        self.assertFalse(r['checks']['proposed_label_counts'])
        self.assertEqual(r['proposed_label_counts']['contradicted'],0)

    def test_minimum_families_and_groups_are_independent(self):
        for row in self.m['cases'][3:]: row['family_id'] = 'one-family'
        r = review(self.m,self.sources)
        self.assertFalse(r['checks']['candidate_family_count'])
        self.assertTrue(r['checks']['equivalence_groups'])
        c = json.loads(self.sources['candidate'])
        for case in c['cases']: case['equivalence_group'] = None
        self.sources['candidate'] = encode(c); self.m['sources'], _ = inventory(self.sources)
        self.assertFalse(review(self.m,self.sources)['checks']['equivalence_groups'])

    def test_no_configurable_threshold_downgrade(self):
        self.m['thresholds'] = dict(cases=1)
        with self.assertRaises(ValueError): review(self.m,self.sources)

    def test_case_minimum_cannot_pass_by_counting_development(self):
        for row in self.m['cases'][-3:]: row['split'] = 'development'
        r = review(self.m,self.sources)
        self.assertEqual(r['total_cases'],123)
        self.assertEqual(r['validation_candidate_cases'],117)
        self.assertFalse(r['checks']['candidate_case_count'])

    def test_missing_risk_and_protocol_bindings_retained_as_unmet(self):
        for row in self.m['cases']: row['risk_tags'] = []
        self.m['protocol']['prompt_sha256'] = None
        r = review(self.m,self.sources)
        self.assertFalse(r['checks']['declared_risk_coverage'])
        self.assertFalse(r['checks']['protocol_fingerprints_present'])

    def test_exposure_or_unknown_never_passes(self):
        for value in (True, None):
            self.m['custody']['validation_results_seen'] = value
            r = review(self.m,self.sources)
            self.assertFalse(r['checks']['no_tuner_or_results_exposure_declared'])
            self.assertIs(r['custody_declarations']['validation_results_seen'], value)

    def test_custody_and_raters_require_explicit_distinct_declarations(self):
        self.m['custody']['custodian_is_non_tuner'] = None
        self.assertFalse(review(self.m,self.sources)['checks']['non_tuner_custodian_declared'])
        self.m['custody']['rater_ids'] = ['A','a']
        with self.assertRaises(ValueError): review(self.m,self.sources)

    def test_bad_types_and_invalid_enum_rejected(self):
        for field, value in [('family_id',True),('risk_tags',['invented']),('split','test'),('request_sha256',True)]:
            m=copy.deepcopy(self.m); m['cases'][0][field]=value
            with self.subTest(field=field), self.assertRaises(ValueError): review(m,self.sources)
        self.m['custody']['validation_results_seen'] = 0
        with self.assertRaises(ValueError): review(self.m,self.sources)

    def test_cli_private_snapshot_complete_and_nonready_exit(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            p=Path(tmp); source=p/'source.json'; source.write_bytes(self.sources['dev'])
            out=p/'out'
            self.assertEqual(main(['prepare','--source',f'dev={source}','--out-dir',str(out)]),1)
            complete=json.loads((out/'complete.json').read_bytes())
            for name, h in complete.items(): self.assertEqual(sha((out/name).read_bytes()),h)
            args=['review','--source',f'dev={source}','--manifest',str(out/'manifest.json'),'--out-dir',str(p/'review')]
            self.assertEqual(main(args),1)
            self.assertEqual((p/'review/manifest.json').read_bytes(),(out/'manifest.json').read_bytes())
            with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(main(args),2)

    def test_cli_zero_exit_still_no_freeze_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            p=Path(tmp); args=['review']
            for sid,raw in self.sources.items():
                source=p/f'{sid}.json'; source.write_bytes(raw)
                args += ['--source',f'{sid}={source}']
            manifest=p/'manifest.json'; manifest.write_bytes(encode(self.m))
            self.assertEqual(main(args+['--manifest',str(manifest),'--out-dir',str(p/'out')]),0)
            self.assertFalse(json.loads((p/'out/review.json').read_bytes())['freeze_accepted'])

    def test_missing_source_and_ambiguous_aliases_rejected(self):
        with self.assertRaises(ValueError): review(self.m, {'dev':self.sources['dev']})
        with self.assertRaises(ValueError): inventory(dict(self.sources, DEV=self.sources['dev']))
        self.m['sources'].append(self.m['sources'][0])
        with self.assertRaises(ValueError): review(self.m,self.sources)

    def test_cli_invalid_json_and_output_overlap_no_complete(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
            p=Path(tmp); source=p/'source.json'; source.write_bytes(self.sources['dev'])
            manifest=p/'manifest.json'; manifest.write_text('{"format":1,"format":2}')
            self.assertEqual(main(['review','--source',f'dev={source}','--manifest',str(manifest),'--out-dir',str(p/'new')]),2)
            self.assertFalse((p/'new').exists())
            self.assertEqual(main(['prepare','--source',f'dev={source}','--out-dir',str(p)]),2)

    def test_cli_detects_midflight_source_change_before_write(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
            p=Path(tmp); source=p/'source.json'; source.write_bytes(self.sources['dev'])
            def mutate(*args):
                r=review(*args); source.write_bytes(b'changed'); return r
            with patch('review_semantic_split.review', side_effect=mutate):
                self.assertEqual(main(['prepare','--source',f'dev={source}','--out-dir',str(p/'new')]),2)
            self.assertFalse((p/'new').exists())


if __name__ == '__main__':
    unittest.main()
