import unittest

from highschoolphysics import learning, learning_views, question_bank
from highschoolphysics.db import connect, initialize_database, seed_demo_data
from highschoolphysics.question_difficulty import classify, statistics, badge
from highschoolphysics.repository import PhysicsRepository
from highschoolphysics.exporting import build_wrong_book_html


class QuestionDifficultyTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(':memory:')
        initialize_database(self.conn)
        seed_demo_data(self.conn)
        self.repo = PhysicsRepository(self.conn)
        self.repo.resolve_review_item('user-teacher-li', 'resp-1001-q2', 'C', '核对')
        self.repo.grade_assessment('user-teacher-li', 'assess-week-1', publish=True)
        learning.migrate(self.conn)
        self.admin = dict(self.conn.execute("select * from users where role='admin'").fetchone())
        self.student = dict(self.conn.execute("select * from users where id='stu-1001'").fetchone())
        self.qid = self.conn.execute('select question_id from student_responses limit 1').fetchone()[0]

    def tearDown(self):
        self.conn.close()

    def test_boundaries_without_rounding(self):
        for correct, expected in ((0,'挑战'), (29,'挑战'), (30,'拔高'), (49,'拔高'),
                                  (50,'巩固'), (69,'巩固'), (70,'基础'), (89,'基础'),
                                  (90,'送分'), (100,'送分')):
            with self.subTest(correct=correct):
                self.assertEqual(classify(correct,100),expected)
        self.assertEqual(classify(29999,100000),'挑战')
        self.assertEqual(classify(0,0),'未分级')

    def test_no_response_unpublished_and_pending_are_unrated(self):
        self.assertEqual(statistics(self.conn,['missing'])['missing']['label'],'未分级')
        self.conn.execute("update assessment_sessions set grading_status='reviewed'")
        self.assertEqual(statistics(self.conn,[self.qid])[self.qid]['label'],'未分级')
        self.conn.execute("update assessment_sessions set grading_status='published'")
        self.conn.execute("update student_responses set outcome='pending'")
        self.assertEqual(statistics(self.conn,[self.qid])[self.qid]['label'],'未分级')

    def test_confirmed_blank_and_wrong_count_exclusion_and_live_correction(self):
        rows = self.conn.execute('select id,student_id from student_responses where question_id=?', (self.qid,)).fetchall()
        self.conn.execute("update student_responses set outcome='wrong',review_status='reviewed' where question_id=?", (self.qid,))
        self.conn.execute("update student_responses set outcome='correct' where id=?",(rows[0]['id'],))
        result = statistics(self.conn,[self.qid])[self.qid]
        self.assertEqual(result['response_count'],len(rows))
        self.assertEqual(result['correct_count'],1)
        self.conn.execute("update student_responses set outcome='blank' where id=?",(rows[0]['id'],))
        self.assertEqual(statistics(self.conn,[self.qid])[self.qid]['response_count'],len(rows))
        self.conn.execute("update assessment_participants set status='not_included' where student_id=?",(rows[0]['student_id'],))
        self.assertEqual(statistics(self.conn,[self.qid])[self.qid]['response_count'],len(rows)-1)
        self.conn.execute("update student_responses set review_status='required' where question_id=?",(self.qid,))
        self.assertEqual(statistics(self.conn,[self.qid])[self.qid]['label'],'未分级')

    def test_redos_do_not_affect_exam_difficulty(self):
        wrong = dict(self.conn.execute('select * from wrong_questions limit 1').fetchone())
        before = statistics(self.conn,[wrong['question_id']])
        self.conn.execute('''insert into redo_attempts(id,school_id,wrong_question_id,student_id,answer,outcome,purpose)
                            values('difficulty-redo',?,?,?,'A','correct','verify')''',
                          (wrong['school_id'],wrong['id'],wrong['student_id']))
        self.assertEqual(statistics(self.conn,[wrong['question_id']]),before)

    def test_bank_exam_wrong_book_and_export_share_difficulty(self):
        q = self.repo.get_question(self.qid)
        self.assertEqual(q['difficulty_stats'],statistics(self.conn,[self.qid])[self.qid])
        listing = question_bank.library(self.repo,self.admin)
        self.assertTrue(listing['groups'])
        self.assertTrue(all('难度：' in g['html'] for g in listing['groups']))
        detail = question_bank.detail(self.repo,self.admin,self.qid)
        self.assertTrue(all('difficulty_stats' in u for u in detail['units']))
        student_html = learning_views.student(self.repo,self.student,{'module':['wrong']})
        self.assertIn('难度：',student_html)
        self.assertIn('正确率',student_html)
        teacher_html = ''.join(learning_views._teacher_question_groups(self.repo,self.admin))
        self.assertIn('难度：',teacher_html)
        exam_html = learning_views.exams(self.repo,self.admin,'assess-week-1')
        self.assertIn('难度：',exam_html)
        export = build_wrong_book_html(self.repo,self.admin['id'],'assess-week-1')
        self.assertIn('难度：',export)

    def test_same_question_aggregates_across_exams_by_response_count(self):
        paper = self.repo.assemble_paper(self.admin['id'], '难度统计测试卷', '',
                                         [{'question_id': self.qid, 'points': 0}])
        exam = self.repo.create_assessment_from_paper(self.admin['id'], paper['paper']['id'],
            'class-physics-1', '第二场考试', '', '高二', '')
        snapshot = self.conn.execute('select id from question_version_snapshots where assessment_id=?',
                                     (exam['id'],)).fetchone()[0]
        self.conn.execute("update student_responses set outcome='wrong',review_status='reviewed' where question_id=?", (self.qid,))
        before = statistics(self.conn, [self.qid])[self.qid]
        self.conn.execute("""insert into student_responses(id,school_id,assessment_id,student_id,question_id,snapshot_id,
                             initial_answer,final_answer,outcome,review_status)
                             values('second-exam',?,?,?,?,?,'B','B','correct','reviewed')""",
            (self.admin['school_id'],exam['id'],self.student['id'],self.qid,snapshot))
        self.conn.execute("update assessment_sessions set grading_status='published' where id=?", (exam['id'],))
        after = statistics(self.conn, [self.qid])[self.qid]
        self.assertEqual(after['response_count'], before['response_count'] + 1)
        self.assertEqual(after['correct_count'], 1)
        self.assertEqual(after['correct_rate'], 1 / after['response_count'])

    def test_school_mismatch_cannot_affect_statistics(self):
        self.conn.execute("insert into schools(id,name,org_scope) values('other-school','其他学校','other-school')")
        self.conn.execute("update student_responses set school_id='other-school' where question_id=?",(self.qid,))
        self.assertEqual(statistics(self.conn,[self.qid])[self.qid]['label'],'未分级')
        self.assertIn('未分级',badge(statistics(self.conn,[self.qid])[self.qid]))
