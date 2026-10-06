import unittest
from highschoolphysics.choice_answers import extract_choice_answer, prepare_answers


class ChoiceAnswerTests(unittest.TestCase):
    def setUp(self):
        self.options = [{"key":k,"markdown":k} for k in "ABCD"]

    def test_explicit_keys_and_conflicts(self):
        for text,kind,expected in [
            ("【答案】B\n\nA错误；故选B。","single_choice","B"),
            ("**BD**\n\n故选DB。","multiple_choice","BD"),
            ("C\n\n故选A。","single_choice",None),
            ("【答案】E","single_choice",None),
            ("BD","single_choice",None),
            ("A错误，B正确","multiple_choice",None),
        ]:
            with self.subTest(text=text):
                self.assertEqual(extract_choice_answer(text,self.options,kind),expected)

    def test_review_freezes_key_and_separates_reasoning(self):
        document = dict(kind="multiple_choice",options=self.options,answer_md="BD\n\n【详解】故选BD。",analysis_md="",answer_state="needs_review",children=[])
        draft=prepare_answers(document)
        self.assertEqual(draft['answer_state'],'needs_review')
        verified=prepare_answers(document,reviewed=True)
        self.assertEqual(verified['answer_md'],'BD')
        self.assertEqual(verified['grading_rule']['answer'],'BD')
        self.assertEqual(verified['answer_state'],'verified')
        self.assertIn('【详解】',verified['analysis_md'])
        self.assertEqual(document['answer_state'],'needs_review')
        conflict=prepare_answers(dict(document,answer_md='C\n故选A。'),reviewed=True)
        self.assertEqual(conflict['answer_state'],'needs_review')
        self.assertIsNone(conflict['grading_rule'])

    def test_leaf_choice_classification_sets_grading_type(self):
        d = dict(kind='multiple_choice',options=self.options,answer_md='B',analysis_md='',answer_state='verified',children=[])
        fixed=prepare_answers(d,reviewed=True,choice_kind='single_choice')
        self.assertEqual(fixed['kind'],'single_choice')
        self.assertEqual(fixed['grading_rule']['type'],'single_choice')
        conflict=prepare_answers(dict(d,answer_md='BD'),reviewed=True,choice_kind='single_choice')
        self.assertIsNone(conflict['grading_rule'])
        self.assertEqual(conflict['answer_state'],'needs_review')
