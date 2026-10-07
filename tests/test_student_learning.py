import json
import unittest
from highschoolphysics import learning, learning_views, student_learning, question_bank
from highschoolphysics.db import connect, initialize_database, seed_demo_data
from highschoolphysics.repository import PhysicsRepository
from highschoolphysics.errors import InvalidRequest, PermissionDenied, ResourceNotFound, StateConflict


class StudentLearningTests(unittest.TestCase):
    def setUp(self):
        self.c=connect(':memory:');initialize_database(self.c);seed_demo_data(self.c)
        self.repo=PhysicsRepository(self.c)
        self.repo.resolve_review_item('user-teacher-li','resp-1001-q2','C','核对')
        self.repo.grade_assessment('user-teacher-li','assess-week-1',publish=True)
        learning.migrate(self.c)
        self.user=dict(self.c.execute("select * from users where id='stu-1001'").fetchone())
        self.other=dict(self.c.execute("select * from users where id='stu-1002'").fetchone())
        self.c.execute("update student_responses set created_at='2026-01-01T00:00:00+00:00',effective_from='2026-01-01T00:00:00+00:00'")
        self.c.commit()

    def tearDown(self):self.c.close()

    def trial(self,kind='single_choice'):
        q=(self.repo.create_question('user-teacher-li','试做独立新题',{'A':'甲','B':'乙'}, {'answer':'B'},'解析',kind,'测试','高三','','medium')['id'],)
        result=student_learning.api(self.repo,self.user,'bank-start',dict(question_id=q[0],request_key='start-'+kind))
        return student_learning.owned_trial(self.c,self.user,result['url'].split('=')[1])

    def test_home_is_count_and_four_modules_and_settings_filter_only_queue(self):
        page=learning_views.student(self.repo,self.user,{})
        for label in ('待复习','错题本','历史测试','知识图谱','题库','关注设置'):self.assertIn(label,page)
        self.assertNotIn('student-wrong',page)
        originals=[tuple(r) for r in self.c.execute('select * from student_responses')]
        student_learning.api(self.repo,self.user,'student-preferences',dict(types=['experiment'],levels=['挑战']))
        page=learning_views.student(self.repo,self.user,{})
        self.assertIn('待复习 <strong>0</strong>',page)
        self.assertEqual(originals,[tuple(r) for r in self.c.execute('select * from student_responses')])
        self.assertIn('student-wrong',learning_views.student(self.repo,self.user,{'module':['wrong']}))
        with self.assertRaises(InvalidRequest):student_learning.api(self.repo,self.user,'student-preferences',dict(types=['fake']))

    def test_choice_trial_requires_answer_freezes_and_idempotent_add(self):
        t=self.trial();before=self.c.execute('select count(*) from student_responses').fetchone()[0]
        with self.assertRaises(InvalidRequest):student_learning.api(self.repo,self.user,'bank-submit',dict(trial_id=t['id']))
        with self.assertRaises(InvalidRequest):student_learning.api(self.repo,self.user,'bank-submit',dict(trial_id=t['id'],answer='A',self_outcome='correct'))
        self.c.execute("update questions set answer_json='{}' where id=?",(t['question_id'],));self.c.commit()
        frozen=student_learning.owned_trial(self.c,self.user,t['id']);self.assertEqual(frozen['rule_json'],t['rule_json'])
        result=student_learning.api(self.repo,self.user,'bank-submit',dict(trial_id=t['id'],answer='A'))
        self.assertIn(result['outcome'],('correct','wrong'))
        if result['outcome']=='wrong':
            for _ in range(2):student_learning.api(self.repo,self.user,'bank-add-wrong',dict(trial_id=t['id']))
            self.assertEqual(1,self.c.execute('select count(*) from student_personal_wrongs').fetchone()[0])
            self.assertTrue(any(w['personal'] for w in student_learning.wrongs(self.repo,self.user)))
        self.assertEqual(before,self.c.execute('select count(*) from student_responses').fetchone()[0])
        with self.assertRaises(ResourceNotFound):student_learning.owned_trial(self.c,self.other,t['id'])
        with self.assertRaises(PermissionDenied):question_bank.staff(self.user)
        self.assertTrue(question_bank.library(self.repo,self.user)['total'])

    def test_fill_self_report_requires_solution_and_preserves_initial_exam_answer(self):
        w=student_learning.wrongs(self.repo,self.user)[0]
        self.c.execute('update question_version_snapshots set grading_rule_json=? where id=?',(json.dumps({'type':'fill','answer':'2'}),w['snapshot']['id']));self.c.commit()
        before=tuple(self.c.execute('select initial_answer,outcome from student_responses where id=?',(w['response_id'],)).fetchone())
        payload=dict(wrong_id=w['id'],unified='1',self_outcome='correct',request_key='self-report')
        with self.assertRaises(InvalidRequest):learning.submit(self.repo,self.user['id'],payload)
        learning.api(self.repo,self.user,'solution',dict(wrong_id=w['id']))
        a=learning.submit(self.repo,self.user['id'],payload)
        self.assertEqual('correct',a['outcome']);self.assertEqual(1,a['self_reported']);self.assertEqual('verify',a['purpose'])
        self.assertEqual(a['id'],learning.submit(self.repo,self.user['id'],payload)['id'])
        with self.assertRaises(StateConflict):learning.submit(self.repo,self.user['id'],dict(payload,self_outcome='wrong'))
        self.assertEqual(before,tuple(self.c.execute('select initial_answer,outcome from student_responses where id=?',(w['response_id'],)).fetchone()))
        html=learning_views.student(self.repo,self.user,{'practice':[w['id']]})
        self.assertNotIn('textarea',html);self.assertNotIn('练习方式',html);self.assertIn('第 2 次重做',html)

    def test_first_choice_record_folded_and_fill_score_from_evidence(self):
        rows=student_learning.wrongs(self.repo,self.user)
        choice=next(w for w in rows if w['kind'] in student_learning.CHOICE)
        self.assertIn('<summary>首次作答记录</summary>',student_learning.first_record(self.c,choice))
        fill=dict(choice,kind='fill')
        self.assertIn('首次作答得分',student_learning.first_record(self.c,fill))
        self.assertNotIn('首次作答记录',student_learning.first_record(self.c,fill))
        self.c.execute("insert into response_evidence(id,response_id,raw_answer,extraction_method,imported_score,imported_max_score,created_at) values(?,?,'','test',2,4,?)", ('score-fixture',fill['response_id'],learning.now()));self.c.commit()
        self.assertIn('2.0 / 4.0',student_learning.first_record(self.c,fill))

    def test_graph_all_three_sorted_rates_links_and_history_separation(self):
        g=student_learning.graph(self.repo,self.user)
        for name in student_learning.KINDS.values():self.assertIn(name,g)
        self.assertIn('<meter',g);self.assertIn('app?module=bank&knowledge=',g)
        history=student_learning.history(self.repo,self.user)
        self.assertIn('历史考试',history);self.assertIn('最近练习结果',history)
        self.assertNotIn('李华',history)
        bank=learning_views.student(self.repo,self.user,{'module':['bank']})
        self.assertIn('data-action="bank-start"',bank);self.assertIn('素养点',bank)
        with self.assertRaises(ResourceNotFound):student_learning.own_question(self.repo,self.other,'does-not-exist')

    def test_http_student_routes_trial_ownership_and_staff_mutations_blocked(self):
        import tempfile,sqlite3
        from pathlib import Path
        from tests.http_support import LivePhysicsServer
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'student.sqlite3'
            with sqlite3.connect(path) as dest:self.c.backup(dest)
            with LivePhysicsServer(path,seed=False) as server:
                _,cookie,_=server.login('stu_1001','student123')
                for route in ('/app','/app?module=wrong','/app?module=history','/app?module=graph','/app?module=bank','/app?review=1'):
                    code,_,html=server.request('GET',route,headers={'Cookie':cookie,'X-Forwarded-Prefix':'/physics'})
                    self.assertEqual(200,code,route);self.assertNotIn('李华',html.decode())
                qid=self.c.execute("select id from questions where question_type='single_choice' limit 1").fetchone()[0]
                code,_,body=server.post_json('/api/learning/bank-start',dict(question_id=qid,request_key='http-start'),cookie)
                self.assertEqual(200,code);tid=json.loads(body)['result']['url'].split('=')[1]
                code,_,_=server.request('GET','/app?trial='+tid,headers={'Cookie':cookie})
                self.assertEqual(200,code)
                _,other,_=server.login('stu_1002','student123')
                code,_,_=server.post_json('/api/learning/bank-solution',dict(trial_id=tid),other)
                self.assertEqual(404,code)
                code,_,_=server.post_json('/api/learning/student-preferences',dict(types=['single_choice'],levels=['基础']),cookie)
                self.assertEqual(200,code)
                code,_,_=server.post_json('/api/learning/question',dict(stem='禁止学生建题'),cookie)
                self.assertEqual(403,code)
                code,_,_=server.request('GET','/question-bank',headers={'Cookie':cookie})
                self.assertEqual(403,code)
