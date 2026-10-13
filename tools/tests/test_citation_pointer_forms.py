"""Citation anchor forms. No network, no judge call: pure response-contract checks.

The judge is shown {"evidence_id","content"} objects, so a pointer may legitimately be
written relative to the content (/after_content) or to the object as displayed
(/content/after_content). Both must resolve; neither may smuggle a quote that is not in
the source. This is what keeps a mere anchor-syntax difference from becoming a false
"insufficient" verdict on real evidence.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from scoring.fact_judge import FactError, pointer_value, validate_response

CONTENT = {'path': '订餐.yaml', 'op': 'modify', 'before_content': '备注: \n座位: \n',
           'after_content': '备注: 免香菜\n座位: 靠窗\n'}
REQ = {'requirement': 'both fields filled',
       'observations': [dict(evidence_id='E11', content=dict(CONTENT))]}


def answer(pointer, quote, result='supported'):
    return dict(result=result, rationale='because the file records both fields',
                citations=[dict(evidence_id='E11', pointer=pointer, quote=quote)])


class PointerFormTests(unittest.TestCase):
    def test_content_relative_pointer_still_works(self):
        r = answer('/after_content', '备注: 免香菜')
        self.assertIs(validate_response(REQ, r), r)

    def test_displayed_object_pointer_now_resolves(self):
        r = answer('/content/after_content', '座位: 靠窗')
        self.assertIs(validate_response(REQ, r), r)

    def test_whole_object_quote_must_match_exactly(self):
        from scoring.fact_judge import canonical
        good = answer('/content', canonical(CONTENT))
        self.assertIs(validate_response(REQ, good), good)
        self.assertRaises(FactError, validate_response, REQ, answer('/content', 'nope'))

    def test_fabricated_pointer_rejected(self):
        for pointer in ('/content/nothing', '/nothing', '/content/after_content/x'):
            with self.subTest(pointer=pointer):
                with self.assertRaises(FactError) as ctx:
                    validate_response(REQ, answer(pointer, '备注: 免香菜'))
                self.assertIn('pointer', str(ctx.exception))

    def test_quote_outside_source_rejected_under_both_forms(self):
        for pointer in ('/after_content', '/content/after_content'):
            with self.subTest(pointer=pointer):
                with self.assertRaises(FactError) as ctx:
                    validate_response(REQ, answer(pointer, '密码: 123456'))
                self.assertEqual(str(ctx.exception), 'citation quote does not match source')

    def test_out_of_scope_evidence_still_rejected(self):
        with self.assertRaises(FactError) as ctx:
            validate_response(REQ, dict(answer('/content/after_content', '座位'),
                                        citations=[dict(evidence_id='E99', pointer='/content/x',
                                                        quote='y')]))
        self.assertEqual(str(ctx.exception), 'citation outside observation scope')

    def test_scalar_traversal_not_rescued_by_fallback(self):
        with self.assertRaises(FactError) as ctx:
            validate_response(REQ, answer('/path/deeper', 'x'))
        self.assertEqual(str(ctx.exception), 'pointer enters scalar')

    def test_before_and_after_are_distinguishable(self):
        # An anchor selects one field: quoting the *after* text against before_content must
        # not pass, in either anchor form. Otherwise the fallback would have blurred fields.
        for pointer in ('/before_content', '/content/before_content'):
            with self.subTest(pointer=pointer):
                self.assertIs(validate_response(REQ, answer(pointer, '备注: \n')).get('result'),
                              validate_response(REQ, answer(pointer, '备注: \n')).get('result'))
                with self.assertRaises(FactError) as ctx:
                    validate_response(REQ, answer(pointer, '免香菜'))
                self.assertEqual(str(ctx.exception), 'citation quote does not match source')


if __name__ == '__main__':
    unittest.main(verbosity=2, exit=False)
