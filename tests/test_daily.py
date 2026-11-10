import json
import unittest
from datetime import datetime, timedelta, timezone

from highschoolphysics import daily, learning, learning_views, student_learning
from highschoolphysics.db import connect, initialize_database, seed_demo_data
from highschoolphysics.errors import InvalidRequest, StateConflict
from highschoolphysics.repository import PhysicsRepository


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class DailyTaskTests(unittest.TestCase):
    def setUp(self):
        self.c = connect(':memory:'); initialize_database(self.c); seed_demo_data(self.c)
        self.repo = PhysicsRepository(self.c)
        self.repo.resolve_review_item('user-teacher-li', 'resp-1001-q2', 'C', '核对')
        self.repo.grade_assessment('user-teacher-li', 'assess-week-1', publish=True)
        learning.migrate(self.c)
        self.c.execute("update student_responses set created_at='2026-01-01T00:00:00+00:00',effective_from='2026-01-01T00:00:00+00:00'")
        self.c.commit()
        self.user = dict(self.c.execute("select * from users where id='stu-1001'").fetchone())
        self.other = dict(self.c.execute("select * from users where id='stu-1002'").fetchone())
        self.wid = 'wq-assess-week-1-stu-1002-q-fill-1'

    def tearDown(self):
        self.c.close()

    def bank_question(self, knowledge, stem):
        qid = self.repo.create_question('user-teacher-li', stem, {'A': '甲', 'B': '乙'}, {'answer': 'B'}, '解析',
                                        'single_choice', '测试', '高三', '', 'medium')['id']
        self.repo.confirm_question_tags('user-teacher-li', qid, knowledge_node_ids=[knowledge],
                                        ability_tag_ids=['ab-calculation'], literacy_tag_ids=[])
        return qid

    def redo_correct(self, days_ago):
        learning.api(self.repo, self.other, 'solution', dict(wrong_id=self.wid))
        learning.submit(self.repo, self.other['id'], dict(wrong_id=self.wid, unified='1', self_outcome='correct',
                                                          request_key='redo-%s' % days_ago))
        self.c.execute('update redo_attempts set submitted_at=? where student_id=?', (ago(days_ago), self.other['id']))
        self.c.commit()

    def progress(self):
        g = student_learning.group_for(self.repo, self.other, self.wid)
        return g['members'][0]['progress']

    def test_home_is_today_plan_and_plan_is_stable_for_the_day(self):
        page = learning_views.student(self.repo, self.user, {})
        self.assertIn('今日任务', page)
        self.assertIn('daily-tasks', page)
        self.assertIn('app?module=today', page)
        self.assertIn('back=today', page)
        self.assertIn('同班', page)
        row = self.c.execute('select items_json from student_daily_plans where student_id=?', (self.user['id'],)).fetchone()
        self.assertEqual(['wq-assess-week-1-stu-1001-q-newton-1'], json.loads(row[0]))
        learning_views.student(self.repo, self.user, {})
        self.assertEqual(1, self.c.execute('select count(*) from student_daily_plans where student_id=?', (self.user['id'],)).fetchone()[0])
        # Plans never touch first-attempt evidence.
        self.assertEqual(0, self.c.execute('select count(*) from redo_attempts').fetchone()[0])

    def test_daily_size_uses_class_setting_and_defaults_to_three(self):
        self.assertEqual(3, daily.size(self.c, self.user))
        learning.api(self.repo, dict(self.c.execute("select * from users where id='user-teacher-li'").fetchone()),
                     'settings', dict(class_id=self.user['class_id'], daily_limit='5'))
        self.assertEqual(5, daily.size(self.c, self.user))

    def test_pick_puts_due_spaced_reviews_first_then_easiest_points(self):
        def group(gid, count, rate_key, created):
            w = dict(id=gid, personal=False, assessment_id='a', question_id=rate_key, created_at=created,
                     progress=dict(count=count, due='2026-01-05', available=True))
            return dict(id=gid, members=[w], anchor=w)
        groups = [group('hard', 0, 'q-hard', '2026-01-03'), group('easy', 0, 'q-easy', '2026-01-01'),
                  group('review', 1, 'q-review', '2026-01-01'), group('mid', 0, 'q-mid', '2026-01-02')]
        rates = {('a', 'q-hard'): 0.2, ('a', 'q-easy'): 0.9, ('a', 'q-mid'): 0.6}
        self.assertEqual(['review', 'easy', 'mid'], [g['id'] for g in daily.pick(groups, rates, 3)])
        self.assertEqual(['easy', 'mid', 'hard'], [g['id'] for g in daily.pick(groups, rates, 3, exclude={'review'})])

    def test_similar_requires_one_original_redo_and_counts_as_verification(self):
        similar = self.bank_question('kn-pep2019-r1-c02', '相似的练习题')
        self.bank_question('kn-pep2019-e1', '无关题目')
        with self.assertRaises(StateConflict):
            student_learning.api(self.repo, self.other, 'daily-similar', dict(wrong_id=self.wid, request_key='sim1'))
        self.redo_correct(3)
        self.assertEqual(1, self.progress()['count'])
        self.assertTrue(self.progress()['available'])
        page = learning_views.student(self.repo, self.other, {'module': ['today']})
        self.assertIn('换一道相似题复测', page)
        self.assertIn('间隔复测', page)
        result = student_learning.api(self.repo, self.other, 'daily-similar', dict(wrong_id=self.wid, request_key='sim1'))
        self.assertTrue(result['url'].endswith('&back=today'))
        again = student_learning.api(self.repo, self.other, 'daily-similar', dict(wrong_id=self.wid, request_key='sim1'))
        self.assertEqual(result, again)
        tid = result['url'].split('trial=')[1].split('&')[0]
        trial = student_learning.owned_trial(self.c, self.other, tid)
        self.assertEqual(similar, trial['question_id'])
        trial_page = learning_views.student(self.repo, self.other, {'trial': [tid], 'back': ['today']})
        self.assertIn('data-next="app?module=today"', trial_page)
        submitted = student_learning.api(self.repo, self.other, 'bank-submit', dict(trial_id=tid, answer='B'))
        self.assertEqual('correct', submitted['outcome'])
        self.assertEqual(2, self.progress()['count'])
        self.assertFalse(self.progress()['available'])
        g = student_learning.group_for(self.repo, self.other, self.wid)
        self.assertTrue(daily.done_today(self.c, self.other['id'], g))
        self.assertGreaterEqual(daily.streak(self.c, self.other['id']), 1)

    def test_wrong_similar_attempt_restarts_the_cycle(self):
        self.bank_question('kn-pep2019-r1-c02', '相似的练习题')
        self.redo_correct(3)
        tid = student_learning.api(self.repo, self.other, 'daily-similar', dict(wrong_id=self.wid, request_key='sim2'))['url'].split('trial=')[1].split('&')[0]
        student_learning.api(self.repo, self.other, 'bank-submit', dict(trial_id=tid, answer='A'))
        self.assertEqual(0, self.progress()['count'])

    def test_similar_unavailable_without_matching_bank_question(self):
        self.redo_correct(3)
        with self.assertRaises(StateConflict):
            student_learning.api(self.repo, self.other, 'daily-similar', dict(wrong_id=self.wid, request_key='sim3'))

    def test_daily_actions_are_scoped_to_the_student(self):
        with self.assertRaises(StateConflict):
            student_learning.api(self.repo, self.user, 'daily-similar', dict(wrong_id=self.wid, request_key='sim4'))
        with self.assertRaises(InvalidRequest):
            student_learning.api(self.repo, self.other, 'daily-similar', dict(wrong_id=self.wid, request_key='bad key!'))

    def test_more_only_after_current_tasks_are_done(self):
        learning_views.student(self.repo, self.other, {})
        self.assertEqual({'url': 'app?module=today'}, student_learning.api(self.repo, self.other, 'daily-more', dict(request_key='more1')))
        learning.api(self.repo, self.other, 'solution', dict(wrong_id=self.wid))
        learning.submit(self.repo, self.other['id'], dict(wrong_id=self.wid, unified='1', self_outcome='correct', request_key='today-redo'))
        page = learning_views.student(self.repo, self.other, {})
        self.assertIn('今天的任务完成了', page)
        self.assertIn('今日已完成', page)
        self.assertEqual('app?module=today&empty=1', student_learning.api(self.repo, self.other, 'daily-more', dict(request_key='more2'))['url'])

    def test_practice_from_today_returns_to_today(self):
        page = learning_views.student(self.repo, self.user, {'practice': ['wq-assess-week-1-stu-1001-q-newton-1'], 'back': ['today']})
        self.assertIn('data-next="app?module=today&amp;completed=1"', page.replace('&completed', '&amp;completed'))


if __name__ == '__main__':
    unittest.main()
