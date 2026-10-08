import copy
import json
import unittest

from highschoolphysics import learning, student_learning, wrong_causes
from highschoolphysics.errors import InvalidRequest, PermissionDenied, StateConflict


class WrongCauseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_student_learning import StudentLearningTests
        StudentLearningTests.setUp(self)
        self.group = student_learning.group_wrongs(self.repo, self.user)[0]

    def tearDown(self):
        self.c.close()

    def save(self, causes, **changes):
        state = wrong_causes.state(self.c, self.user, self.group)
        payload = dict(wrong_id=self.group['id'], scope_key=state['scope_key'],
                       revision=state['revision'], causes=causes, request_key='save-'+str(state['revision']))
        payload.update(changes)
        return learning.api(self.repo, self.user, 'wrong-causes', payload)

    def test_multiselect_cancel_reload_and_original_evidence(self):
        tables = ('student_responses', 'redo_attempts', 'wrong_question_error_tags', 'diagnostic_sessions')
        before = {t: [tuple(r) for r in self.c.execute('select * from '+t)] for t in tables}
        result = self.save(['calculation', 'knowledge_retrieval'])
        self.assertEqual(['knowledge_retrieval', 'calculation'], result['causes'])
        panel = wrong_causes.panel(self.c, self.user, self.group)
        self.assertEqual(2, panel.count('aria-pressed="true"'))
        self.assertIn('已保存：记得，但没想到用、计算、符号或单位错', panel)
        self.save(['calculation']);self.save([])
        self.assertEqual([], wrong_causes.state(self.c, self.user, self.group)['causes'])
        self.assertEqual(3, self.c.execute('select count(*) from student_wrong_cause_events').fetchone()[0])
        for t in tables:
            self.assertEqual(before[t], [tuple(r) for r in self.c.execute('select * from '+t)], t)

    def test_only_wrong_book_has_cause_buttons(self):
        self.assertIn('data-wrong-causes=', student_learning.page(self.repo, self.user, {'module':['wrong']}))
        for params in ({'practice':[self.group['id']]}, {'review':['1']}, {'module':['history']}, {'module':['bank']}):
            self.assertNotIn('data-wrong-causes=', student_learning.page(self.repo, self.user, params))

    def test_uncertain_exclusive_and_payload_validation(self):
        self.assertEqual(['unsure'], self.save(['unsure'])['causes'])
        for causes in (['unsure','calculation'], ['fake'], 'calculation', [None], [['calculation']]):
            with self.assertRaises(InvalidRequest):self.save(causes)
        with self.assertRaises(InvalidRequest):self.save([], revision=True)
        self.assertEqual(1, self.c.execute('select count(*) from student_wrong_cause_events').fetchone()[0])

    def test_student_scope_and_teacher_role(self):
        state = wrong_causes.state(self.c, self.user, self.group)
        p = dict(wrong_id=self.group['id'], scope_key=state['scope_key'], revision=0, causes=['calculation'], request_key='foreign')
        with self.assertRaises(PermissionDenied):learning.api(self.repo, self.other, 'wrong-causes', p)
        teacher = dict(self.c.execute("select * from users where id='user-teacher-li'").fetchone())
        with self.assertRaises(PermissionDenied):learning.api(self.repo, teacher, 'wrong-causes', p)
        self.assertEqual(0, self.c.execute('select count(*) from student_wrong_cause_events').fetchone()[0])

    def test_idempotency_and_stale_page_cannot_overwrite(self):
        result = self.save(['calculation'], request_key='stable')
        self.assertEqual(result, self.save(['calculation'], revision=0, request_key='stable'))
        with self.assertRaises(StateConflict):self.save(['knowledge_forgotten'], revision=0, request_key='stable')
        with self.assertRaises(StateConflict):self.save(['knowledge_forgotten'], revision=0, request_key='stale')
        with self.assertRaises(StateConflict):self.save(['calculation'], scope_key='old-source')
        self.assertEqual(['calculation'], wrong_causes.state(self.c, self.user, self.group)['causes'])

    def test_sources_are_frozen_and_new_source_has_no_old_labels(self):
        self.save(['knowledge_forgotten'])
        row = self.c.execute('select * from student_wrong_cause_events').fetchone()
        self.assertEqual(self.group['key'], row['group_key'])
        self.assertEqual(len(self.group['members']), len(json.loads(row['sources_json'])))
        revised = copy.deepcopy(self.group);revised['members'][0]['snapshot']['id'] = 'a-new-published-snapshot'
        self.assertEqual([], wrong_causes.state(self.c, self.user, revised)['causes'])
        self.assertEqual(['knowledge_forgotten'], wrong_causes.state(self.c, self.user, self.group)['causes'])

    def test_personal_wrong_is_supported(self):
        from tests.test_student_learning import StudentLearningTests
        trial = StudentLearningTests.trial(self)
        student_learning.api(self.repo, self.user, 'bank-submit', dict(trial_id=trial['id'], answer='A'))
        student_learning.api(self.repo, self.user, 'bank-add-wrong', dict(trial_id=trial['id']))
        self.group = next(g for g in student_learning.group_wrongs(self.repo,self.user) if g['anchor']['personal'])
        self.assertEqual(['formula_application'], self.save(['formula_application'])['causes'])
