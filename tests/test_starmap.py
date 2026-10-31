import unittest

from highschoolphysics import starmap, student_learning
from highschoolphysics.errors import InvalidRequest, PermissionDenied
from tests import test_diagnosis_adaptive as adaptive_tests
from tests.http_support import seed_other_class


class StarMapTests(unittest.TestCase):
    setUp = adaptive_tests.AdaptiveTests.setUp
    tearDown = adaptive_tests.AdaptiveTests.tearDown

    def teacher(self):
        return dict(self.c.execute("select * from users where id='user-teacher-li'").fetchone())

    def test_student_and_teacher_views_scope_permissions_and_preserve_data(self):
        seed_other_class(self.c)
        self.c.commit()
        before = [tuple(r) for r in self.c.execute('select * from student_responses')]
        with self.assertRaises(PermissionDenied):
            starmap.build(self.repo, self.user, {'student_id': [self.other['id']]})
        with self.assertRaises(PermissionDenied):
            starmap.build(self.repo, self.teacher(), {'class_id': ['class-physics-2']})
        with self.assertRaises(PermissionDenied):
            starmap.build(self.repo, self.teacher(), {'student_id': ['stu-2001']})
        with self.assertRaises(InvalidRequest):
            starmap.build(self.repo, self.user, {'window': ['7']})

        sdata = starmap.build(self.repo, self.other, {})
        self.assertEqual('student', sdata['mode'])
        self.assertEqual('all', sdata['window'])
        self.assertEqual([], sdata['students'])
        self.assertTrue(any(n['kind'] == 'knowledge' for n in sdata['nodes']))
        self.assertTrue(any(n['kind'] == 'ability' for n in sdata['nodes']))
        self.assertTrue(any(e['kind'] == 'ability' for e in sdata['edges']))
        # Active wrong questions link directly to the student's wrong-book practice view.
        wrong_urls = [q['url'] for k, q in sdata['questions'].items()
                      if any(r[0] == k and r[1] != 'c' for r in sdata['records'])]
        self.assertTrue(any(u.startswith('app?practice=') for u in wrong_urls))

        cdata = starmap.build(self.repo, self.teacher(), {})
        self.assertEqual('class', cdata['mode'])
        self.assertEqual(self.user['class_id'], cdata['class_id'])
        self.assertTrue(all(len(r) == 3 and r[2] >= r[1] for r in cdata['records']))

        single = starmap.build(self.repo, self.teacher(), {'student_id': [self.user['id']]})
        self.assertEqual('student', single['mode'])
        self.assertIn(self.user['display_name'], single['label'])
        self.assertEqual(before, [tuple(r) for r in self.c.execute('select * from student_responses')])

    def test_grouped_parts_count_once_per_task_family(self):
        from tests.test_student_learning import StudentLearningTests
        StudentLearningTests.grouped_fixture(self)
        data = starmap.build(self.repo, self.user, {})
        grouped = [r for r in data['records'] if r[0].startswith('group:student-group-fixture@')]
        self.assertEqual(1, len(grouped))
        html = starmap.page(self.repo, self.user, {})
        self.assertIn('data-starmap', html)
        self.assertIn('对比同班同题', html)
