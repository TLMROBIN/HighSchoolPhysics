import json
import unittest

from highschoolphysics.exam_statistics import question_statistics
from highschoolphysics.response_workflow import set_choice_rate_policy
from highschoolphysics.errors import InvalidRequest, PermissionDenied, StateConflict
from tests import test_response_workflow as fixtures


class ChoiceRatePolicyTests(unittest.TestCase):
    setUp = fixtures.ResponseWorkflowTests.setUp
    tearDown = fixtures.ResponseWorkflowTests.tearDown

    def test_uniform_normalized_credit_excludes_unknown_and_pending(self):
        q = dict(grading_rule=dict(type='multiple_choice', answer='ACD'), options=dict.fromkeys('ABCD'))
        answers = ['ACD', 'AD', 'A', 'AB', '', 'AC', '???']
        rows = [dict(student_id=str(i), final_answer=a, outcome='correct' if i == 0 else 'blank' if i == 4 else 'pending' if i == 5 else 'wrong') for i, a in enumerate(answers)]
        exact = question_statistics(q, rows, 9)
        result = question_statistics(q, rows, 9, 'full_half_zero')
        self.assertEqual(exact['correct_rate'], 2/5)
        self.assertEqual(question_statistics(q, rows, 9, 'exact')['correct_rate'], 2/5)
        self.assertEqual(result['correct_rate'], 2/5)
        self.assertEqual(result['partial_count'], 2)
        self.assertEqual(result['strict_correct_rate'], 1/6)
        self.assertEqual(result['option_unknown_count'], 1)
        self.assertEqual(result['pending_count'], 1)
        self.assertEqual(result['missing_count'], 2)
        self.assertEqual(result['score_rate'], 2/5)
        self.assertEqual(result['score_count'], 5)
        self.assertIsNone(question_statistics(dict(grading_rule=dict(type='multiple_choice')), rows, 9, 'full_half_zero')['choice_score_rate'])

    def test_policy_is_audited_scoped_and_locked_after_publication(self):
        with self.assertRaises(InvalidRequest):
            set_choice_rate_policy(self.repo, self.admin, self.a, 'exact', 'test')
        set_choice_rate_policy(self.repo, self.admin, self.a, 'full_half_zero', '教师明确要求漏选半对')
        row = self.c.execute('select scope_json from assessment_sessions where id=?', (self.a,)).fetchone()
        self.assertEqual(json.loads(row[0])['choice_rate_policy'], 'full_half_zero')
        other = self.c.execute('select scope_json from assessment_sessions where id=?', ('assess-week-1',)).fetchone()
        self.assertNotIn('choice_rate_policy', json.loads(other[0]))
        self.assertEqual(self.c.execute("select count(*) from audit_events where action='choice_rate_policy_set'").fetchone()[0], 1)
        with self.assertRaises(PermissionDenied):
            set_choice_rate_policy(self.repo, self.student, self.a, 'full_half_zero', 'test')
        self.c.execute("update assessment_sessions set grading_status='published' where id=?", (self.a,)); self.c.commit()
        with self.assertRaises(StateConflict):
            set_choice_rate_policy(self.repo, self.admin, self.a, 'full_half_zero', 'test')

    def test_published_policy_migration_is_audited_and_idempotent(self):
        from highschoolphysics.response_workflow import _migrate_choice_statistics
        self.c.execute("delete from app_schema_migrations where feature='uniform_multiple_choice_statistics'")
        self.c.execute("update assessment_sessions set grading_status='published' where id=?", (self.a,))
        self.c.execute("update question_version_snapshots set grading_rule_json=? where assessment_id=?",
                       (json.dumps(dict(type='multiple_choice',answer='AB')),self.a))
        self.c.commit()
        before=[tuple(r) for r in self.c.execute('select * from student_responses')]
        _migrate_choice_statistics(self.c)
        self.assertEqual(json.loads(self.c.execute('select scope_json from assessment_sessions where id=?',(self.a,)).fetchone()[0])['choice_rate_policy'],'full_half_zero')
        count=self.c.execute("select count(*) from audit_events where action='choice_statistics_corrected' and resource_id=?",(self.a,)).fetchone()[0]
        self.assertEqual(count,1)
        _migrate_choice_statistics(self.c)
        self.assertEqual(self.c.execute("select count(*) from audit_events where action='choice_statistics_corrected' and resource_id=?",(self.a,)).fetchone()[0],1)
        self.assertEqual(before,[tuple(r) for r in self.c.execute('select * from student_responses')])

    def test_published_answer_results_and_global_difficulty_share_credit(self):
        from highschoolphysics import learning_views, response_workflow as workflow
        from highschoolphysics.question_difficulty import statistics
        from highschoolphysics.exam_review import collect_source
        rule=dict(type='multiple_choice',answer='AB')
        self.c.execute('update question_version_snapshots set grading_rule_json=?,options_json=? where assessment_id=?',
                       (json.dumps(rule),json.dumps(dict.fromkeys('ABCD','选项')),self.a))
        self.c.commit()
        fixtures.ResponseWorkflowTests.save(self, fixtures.ResponseWorkflowTests.payload(self, 'A'))
        workflow.publish(self.repo,self.admin,dict(assessment_id=self.a))
        response=dict(self.c.execute('select * from student_responses where assessment_id=?',(self.a,)).fetchone())
        self.assertEqual(response['outcome'],'wrong')  # Remains eligible for review.
        source=collect_source(self.repo,self.admin,self.a)[1][0]
        self.assertEqual(source['statistics']['correct_rate'],.5)
        self.assertEqual(source['statistics']['score_rate'],.5)
        for user in (self.admin,self.student):
            self.assertIn('漏选半对（得分率50%）',learning_views.exams(self.repo,user,self.a))
        self.c.execute("update assessment_sessions set grading_status='reviewed' where id<>?",(self.a,))
        self.assertEqual(statistics(self.c,[response['question_id']])[response['question_id']]['correct_rate'],.5)
