import json
import unittest

from highschoolphysics.exam_statistics import question_statistics
from highschoolphysics.response_workflow import set_choice_rate_policy
from highschoolphysics.errors import PermissionDenied, StateConflict
from tests import test_response_workflow as fixtures


class ChoiceRatePolicyTests(unittest.TestCase):
    setUp = fixtures.ResponseWorkflowTests.setUp
    tearDown = fixtures.ResponseWorkflowTests.tearDown

    def test_explicit_exam_policy_does_not_invent_scores(self):
        q = dict(grading_rule=dict(type='multiple_choice', answer='ACD'), options=dict.fromkeys('ABCD'))
        answers = ['ACD', 'AD', 'A', 'AB', '', 'AC', '???']
        rows = [dict(student_id=str(i), final_answer=a, outcome='correct' if i == 0 else 'blank' if i == 4 else 'pending' if i == 5 else 'wrong') for i, a in enumerate(answers)]
        exact = question_statistics(q, rows, 9)
        result = question_statistics(q, rows, 9, 'full_half_zero')
        self.assertEqual(exact['correct_rate'], 1/6)
        self.assertEqual(result['correct_rate'], 2/5)
        self.assertEqual(result['partial_count'], 2)
        self.assertEqual(result['strict_correct_rate'], 1/6)
        self.assertEqual(result['option_unknown_count'], 1)
        self.assertEqual(result['pending_count'], 1)
        self.assertEqual(result['missing_count'], 2)
        self.assertIsNone(result['score_rate'])
        self.assertEqual(result['score_count'], 0)
        self.assertIsNone(question_statistics(dict(grading_rule=dict(type='multiple_choice')), rows, 9, 'full_half_zero')['choice_score_rate'])

    def test_policy_is_audited_scoped_and_locked_after_publication(self):
        set_choice_rate_policy(self.repo, self.admin, self.a, 'full_half_zero', '教师明确要求漏选半对')
        row = self.c.execute('select scope_json from assessment_sessions where id=?', (self.a,)).fetchone()
        self.assertEqual(json.loads(row[0])['choice_rate_policy'], 'full_half_zero')
        other = self.c.execute('select scope_json from assessment_sessions where id=?', ('assess-week-1',)).fetchone()
        self.assertNotIn('choice_rate_policy', json.loads(other[0]))
        self.assertEqual(self.c.execute("select count(*) from audit_events where action='choice_rate_policy_set'").fetchone()[0], 1)
        with self.assertRaises(PermissionDenied):
            set_choice_rate_policy(self.repo, self.student, self.a, 'exact', 'test')
        self.c.execute("update assessment_sessions set grading_status='published' where id=?", (self.a,)); self.c.commit()
        with self.assertRaises(StateConflict):
            set_choice_rate_policy(self.repo, self.admin, self.a, 'exact', 'test')
