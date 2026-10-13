"""Synthetic rater submissions exercise plumbing only; never real human labels."""
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
from scoring.semantic_annotation import make_packets, template, validate_labels, compare, sha
from annotate_semantic import main


def corpus():
    return dict(format='okb-semantic-robustness-v1',
        purpose='public-author-designed-development-tests-not-independent-holdout', case_count=2,
        cases=[dict(id=f'SECRET_CASE_{n}', requirement=f'Contains fact {n}', context={'scope':'fixture'},
            observations=[dict(evidence_id='E1', content={'text':f'fact {n}'})], expected='supported',
            category='SECRET_CATEGORY', equivalence_group=None) for n in range(2)])


def completed(work, raw, alias):
    labels = template(work, sha(raw)); labels['rater_id'] = alias
    labels['declaration'] = dict(human_completed=True, author_answers_seen=False,
        machine_results_seen=False, other_rater_labels_seen=False)
    for row in labels['rows']:
        obs = next(r for r in work['rows'] if r['row_key']==row['row_key'])['observations'][0]
        row.update(status='labeled', result='supported', rationale='SYNTHETIC PLUMBING TEST ONLY',
            citations=[dict(evidence_id='E1', pointer='/text', quote=obs['content']['text'])])
    return labels


class SemanticHumanTests(unittest.TestCase):
    def setUp(self):
        self.works, self.audit = make_packets(corpus(), 'a'*64)
        self.raw = [canonical(w).encode() for w in self.works]
        self.audit['worklist_sha256'] = [sha(b) for b in self.raw]
        self.labels = [completed(self.works[i], self.raw[i], f'synthetic-{i}') for i in range(2)]

    def test_metadata_absent_evidence_verbatim_and_ids_separate(self):
        for work in self.works:
            text = canonical(work)
            for secret in ('SECRET_CASE', 'SECRET_CATEGORY', 'expected', 'equivalence_group', 'request_sha256'):
                self.assertNotIn(secret, text)
            self.assertEqual({r['observations'][0]['content']['text'] for r in work['rows']}, {'fact 0','fact 1'})
        self.assertNotEqual(self.works[0]['packet_id'], self.works[1]['packet_id'])
        self.assertTrue({r['row_key'] for r in self.works[0]['rows']}.isdisjoint(r['row_key'] for r in self.works[1]['rows']))

    def test_no_automatic_holdout_upgrade(self):
        c = corpus(); c['purpose'] = 'independent-holdout'
        with self.assertRaises(ValueError): make_packets(c, 'a'*64)

    def test_pending_template_is_not_completed_annotation(self):
        with self.assertRaises(ValueError): validate_labels(self.raw[0], template(self.works[0], sha(self.raw[0])))

    def test_valid_labels_check_structure_not_truth(self):
        out = validate_labels(self.raw[0], self.labels[0])
        self.assertEqual(out['labeled'], 2)
        self.assertFalse(out['human_identity_verified']); self.assertFalse(out['semantic_truth_verified'])

    def test_wrong_package_packet_and_bytes_rejected(self):
        for k in ('package_id','packet_id','worklist_sha256'):
            with self.subTest(key=k):
                lab = copy.deepcopy(self.labels[0]); lab[k] = 'wrong'
                with self.assertRaises(ValueError): validate_labels(self.raw[0], lab)
        with self.assertRaises(ValueError): validate_labels(self.raw[0]+b'\n', self.labels[0])

    def test_missing_duplicate_and_extra_rows_rejected(self):
        for rows in ([], self.labels[0]['rows']*2,
                     self.labels[0]['rows']+[dict(self.labels[0]['rows'][0], row_key='alien')]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_labels(self.raw[0], dict(self.labels[0], rows=rows))

    def test_fake_quote_scope_pointer_and_empty_decisive_cites_rejected(self):
        attacks = [[], [dict(evidence_id='E1',pointer='/text',quote='invented')],
            [dict(evidence_id='ALIEN',pointer='/text',quote='fact')],
            [dict(evidence_id='E1',pointer='/missing',quote='fact')]]
        for cites in attacks:
            with self.subTest(cites=cites):
                lab = copy.deepcopy(self.labels[0]); lab['rows'][0]['citations'] = cites
                with self.assertRaises(ValueError): validate_labels(self.raw[0], lab)

    def test_human_and_exposure_declarations_explicit(self):
        for key in self.labels[0]['declaration']:
            lab = copy.deepcopy(self.labels[0]); lab['declaration'][key] = None
            with self.subTest(key=key), self.assertRaises(ValueError): validate_labels(self.raw[0], lab)
        lab = copy.deepcopy(self.labels[0]); lab['declaration']['human_completed'] = False
        with self.assertRaises(ValueError): validate_labels(self.raw[0], lab)

    def test_exposure_is_retained_not_silently_filtered(self):
        self.labels[0]['declaration']['author_answers_seen'] = True
        result = compare(self.audit, self.raw, self.labels)
        self.assertFalse(result['raters'][0]['validation']['unexposed_self_declaration'])
        self.assertEqual(result['total_cases'], 2)
        self.assertFalse(result['independence_verified'])

    def test_clarification_is_not_semantic_insufficient(self):
        self.labels[0]['rows'][0].update(status='needs_clarification', result=None,
                                        rationale='Synthetic ambiguous instruction', citations=[])
        out = compare(self.audit, self.raw, self.labels)
        self.assertEqual(out['clarification_cases'], 1); self.assertEqual(out['jointly_labeled'], 1)
        self.assertEqual(out['agreement_among_jointly_labeled'], 1)
        self.assertEqual(out['agreed_labeled_fraction_of_all'], .5)
        self.assertEqual(sum(sum(r.values()) for r in out['matrix'].values()), 1)
        self.labels[0]['rows'][0]['result'] = 'insufficient'
        with self.assertRaises(ValueError): validate_labels(self.raw[0], self.labels[0])

    def test_all_clarifications_keep_denominator_and_null_agreement(self):
        for lab in self.labels:
            for row in lab['rows']: row.update(status='needs_clarification', result=None, citations=[])
        out = compare(self.audit, self.raw, self.labels)
        self.assertEqual(out['total_cases'], 2); self.assertEqual(out['clarification_cases'], 2)
        self.assertIsNone(out['agreement_among_jointly_labeled'])

    def test_disagreement_not_adjudicated_or_labels_overwritten(self):
        self.labels[1]['rows'][0]['result'] = 'contradicted'
        before = copy.deepcopy(self.labels); out = compare(self.audit, self.raw, self.labels)
        self.assertEqual(out['disagreements'], 1); self.assertEqual(out['agreements'], 1)
        self.assertEqual(self.labels, before)
        self.assertTrue(all(c['adjudicated_reference'] is None for c in out['cases']))

    def test_same_declared_rater_or_packet_rejected(self):
        self.labels[1]['rater_id'] = self.labels[0]['rater_id'].upper()
        with self.assertRaises(ValueError): compare(self.audit, self.raw, self.labels)
        self.labels[1]['rater_id'] = 'other'
        with self.assertRaises(ValueError): compare(self.audit, self.raw[:1]*2, self.labels)

    def test_mapping_tamper_rejected(self):
        for field in ('case_id','request_sha256'):
            audit = copy.deepcopy(self.audit); entry = next(iter(audit['mapping'][1].values()))
            entry[field] = 'wrong'
            with self.subTest(field=field), self.assertRaises(ValueError): compare(audit, self.raw, self.labels)

    def test_duplicate_evidence_identity_rejected_at_prepare(self):
        c = corpus(); c['cases'][0]['observations'] *= 2
        with self.assertRaises(ValueError): make_packets(c, 'a'*64)

    def test_undeclared_work_fields_rejected(self):
        work = copy.deepcopy(self.works[0]); work['rows'][0]['expected'] = 'supported'
        raw = canonical(work).encode(); lab = copy.deepcopy(self.labels[0]); lab['worklist_sha256'] = sha(raw)
        with self.assertRaises(ValueError): validate_labels(raw, lab)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.src = self.root/'corpus.json'
        self.src.write_text(json.dumps(corpus()), encoding='utf8')
        self.dirs = [self.root/n for n in ('a','b','private')]

    def call(self, args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()): return main(args)

    def prepare(self):
        return self.call(['prepare','--corpus',str(self.src), '--rater-a-dir',str(self.dirs[0]),
            '--rater-b-dir',str(self.dirs[1]),'--private-dir',str(self.dirs[2])])

    def compare_args(self):
        paths = []
        for i, d in enumerate(self.dirs[:2]):
            raw = (d/'worklist.json').read_bytes(); work = json.loads(raw)
            lab = completed(work, raw, f'synthetic-{i}'); path = self.root/f'labels-{i}.json'
            path.write_text(json.dumps(lab), encoding='utf8'); paths.append(path)
        return ['compare','--private-dir',str(self.dirs[2]),'--worklist-a',str(self.dirs[0]/'worklist.json'),
            '--worklist-b',str(self.dirs[1]/'worklist.json'),'--labels-a',str(paths[0]),'--labels-b',str(paths[1]),
            '--out-dir',str(self.root/'comparison')]

    def test_full_offline_cli_initial_bytes_preserved_and_completion_valid(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            self.assertEqual(self.prepare(), 0); self.assertEqual(self.call(self.compare_args()), 0)
        out = self.root/'comparison'
        for i in range(2):
            self.assertEqual((out/f'initial-labels-{i+1}.json').read_bytes(), (self.root/f'labels-{i}.json').read_bytes())
        complete = json.loads((out/'complete.json').read_text())
        for name, digest in complete.items(): self.assertEqual(sha((out/name).read_bytes()), digest)
        self.assertFalse(json.loads((out/'comparison.json').read_text())['semantic_reliability_validated'])

    def test_prepare_separates_private_and_rejects_overwrite(self):
        self.assertEqual(self.prepare(), 0)
        self.assertEqual({p.name for p in self.dirs[0].iterdir()}, {'worklist.json','labels-template.json'})
        self.assertEqual(self.prepare(), 2)

    def test_nested_and_same_output_paths_rejected(self):
        self.dirs[2] = self.dirs[0]
        self.assertEqual(self.prepare(), 2); self.assertFalse(self.dirs[0].exists())
        self.dirs[2] = self.dirs[0]/'private'
        self.assertEqual(self.prepare(), 2)

    def test_duplicate_json_rejected(self):
        self.src.write_text('{"format":1,"format":2}')
        self.assertEqual(self.prepare(), 2); self.assertFalse(self.dirs[0].exists())

    def test_private_hash_tamper_rejected(self):
        self.assertEqual(self.prepare(), 0); args = self.compare_args()
        path = self.dirs[2]/'keymap.json'; path.write_bytes(path.read_bytes()+b'\n')
        self.assertEqual(self.call(args), 2); self.assertFalse((self.root/'comparison').exists())

    def test_corpus_drift_prevents_complete(self):
        import annotate_semantic
        original = annotate_semantic.make_packets
        def change(*args):
            self.src.write_bytes(self.src.read_bytes()+b'\n'); return original(*args)
        with patch.object(annotate_semantic, 'make_packets', side_effect=change): self.assertEqual(self.prepare(), 2)
        self.assertFalse((self.dirs[2]/'complete.json').exists())

    def test_real_33_case_corpus_has_only_blank_templates(self):
        self.src = ROOT/'samples/semantic-robustness.v1.json'
        self.assertEqual(self.prepare(), 0)
        for d in self.dirs[:2]:
            work = json.loads((d/'worklist.json').read_text(encoding='utf8'))
            blank = json.loads((d/'labels-template.json').read_text(encoding='utf8'))
            self.assertEqual(len(work['rows']), 33)
            self.assertTrue(all(r['status']=='pending' and r['result'] is None for r in blank['rows']))
            self.assertIsNone(blank['rater_id']); self.assertIsNone(blank['declaration']['human_completed'])
            self.assertEqual(self.call(['validate','--worklist',str(d/'worklist.json'),
                '--labels',str(d/'labels-template.json')]), 2)

    def test_label_drift_during_compare_prevents_complete(self):
        import annotate_semantic
        self.assertEqual(self.prepare(), 0); args = self.compare_args()
        original = annotate_semantic.compare
        def change(*values):
            path = self.root/'labels-0.json'; path.write_bytes(path.read_bytes()+b'\n')
            return original(*values)
        with patch.object(annotate_semantic, 'compare', side_effect=change): self.assertEqual(self.call(args), 2)
        self.assertFalse((self.root/'comparison/complete.json').exists())


if __name__ == '__main__': unittest.main()
