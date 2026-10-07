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

    def grouped_fixture(self):
        from highschoolphysics.document_models import canonical_sha256
        teacher=dict(self.c.execute("select * from users where id='user-teacher-li'").fetchone())
        ids=[]
        for n in range(1,4):
            q=self.repo.create_question(teacher['id'],'实验小问%s'%n,{}, {'answer':'2'},'小问解析','experiment','分组测试','高三','','medium')
            self.repo.confirm_question_tags(teacher['id'],q['id'],knowledge_node_ids=['kn-pep2019-r1-c04-s03'],ability_tag_ids=['ab-model-construction'],literacy_tag_ids=[self.repo.literacy_tags()[0]['id']])
            ids.append(q['id'])
        doc=dict(schema_version=1,number='13',kind='experiment',stem_md='同一道完整实验题的公共条件 $R_1=2\\,\\Omega$。',options=[],answer_md='',analysis_md='',answer_state='verified',grading_rule=None,source_spans=[],asset_refs=[],issues=[],children=[dict(key='part-%s'%n,label='(%s)'%n,kind='experiment',stem_md='第%s小问的完整问题。'%n,options=[],answer_md='参考答案：2',analysis_md='按电路规律分析。',answer_state='verified',source_spans=[]) for n in range(1,4)])
        self.c.execute('insert into question_content_groups(id,school_id,current_revision_id,created_by) values(?,?,NULL,?)',('student-group-fixture',teacher['school_id'],teacher['id']))
        self.c.execute('insert into question_content_revisions(id,group_id,revision_no,schema_version,document_json,content_sha256,review_state,answer_state,created_by,change_reason) values(?,?,1,1,?,?,?,?,?,?)',('student-group-revision','student-group-fixture',json.dumps(doc),canonical_sha256(doc),'verified','verified',teacher['id'],'fixture'))
        self.c.execute('update question_content_groups set current_revision_id=? where id=?',('student-group-revision','student-group-fixture'))
        for n,qid in enumerate(ids,1):self.c.execute('insert into question_content_bindings(question_id,group_id,child_key) values(?,?,?)',(qid,'student-group-fixture','part-%s'%n))
        self.c.commit()
        aid=learning.api(self.repo,teacher,'assessment',dict(title='整题合并复习',class_id='class-physics-1',questions=ids))['url'].split('=')[1]
        students=self.c.execute('select u.username from users u join assessment_participants p on p.student_id=u.id where p.assessment_id=?',(aid,)).fetchall()
        records=[dict(student=u[0],number=str(n),score=0 if n==1 else 1 if n==2 else 2,max_score=2) for u in students for n in range(1,4)]
        payload=dict(assessment_id=aid,records=records,request_key='group-import')
        preview=learning.api(self.repo,teacher,'answers',payload)
        learning.api(self.repo,teacher,'answers',dict(payload,confirm=True,preview_token=preview['preview_token']))
        learning.api(self.repo,teacher,'publish',dict(assessment_id=aid))
        self.c.execute("update student_responses set effective_from='2026-01-01T00:00:00+00:00' where assessment_id=?",(aid,));self.c.commit()
        g=next(g for g in student_learning.group_wrongs(self.repo,self.user) if g['revision_id']=='student-group-revision')
        return g

    def test_grouped_parent_shown_once_with_child_scores_and_sidebars(self):
        g=self.grouped_fixture();self.assertEqual(2,len(g['members']))
        page=learning_views.student(self.repo,self.user,{'module':['wrong']})
        self.assertEqual(1,page.count('同一道完整实验题的公共条件'))
        self.assertIn('<article class="student-wrong">',page)
        self.assertNotIn('<details class="student-wrong">',page)
        self.assertIn('student-filter-sidebar',page);self.assertIn('data-filter-scope="wrong"',page)
        scores=student_learning.group_first_record(self.c,g,self.user)
        self.assertEqual(2,scores.count('需要复习'));self.assertEqual(1,scores.count('无需复习'))
        for score in ('0.0 / 2.0','1.0 / 2.0','2.0 / 2.0'):self.assertIn(score,scores)
        practice=learning_views.student(self.repo,self.user,{'practice':[g['members'][1]['id']]})
        self.assertEqual(1,practice.count('同一道完整实验题的公共条件'))
        self.assertEqual(1,practice.count('data-action="group-submit"'))
        self.assertIn('name="member_ids"',practice)

    def test_group_submit_atomic_and_idempotent_after_mastery(self):
        g=self.grouped_fixture();members=student_learning.review_members(g)
        first_answers=[tuple(r) for r in self.c.execute('select id,initial_answer,outcome from student_responses')]
        payload=dict(wrong_id=g['id'],member_ids=json.dumps([w['id'] for w in members]),result_0='correct',result_1='wrong',request_key='group-redo-fixture')
        learning.api(self.repo,self.user,'solution',dict(wrong_id=members[0]['id']))
        with self.assertRaises(InvalidRequest):student_learning.api(self.repo,self.user,'group-submit',payload)
        self.assertEqual(0,self.c.execute('select count(*) from redo_attempts').fetchone()[0])
        student_learning.api(self.repo,self.user,'group-solution',dict(wrong_id=g['id']))
        result=student_learning.api(self.repo,self.user,'group-submit',payload)
        self.assertEqual(['correct','wrong'],[p['outcome'] for p in result['parts']])
        self.assertEqual(result,student_learning.api(self.repo,self.user,'group-submit',payload))
        self.assertEqual(2,self.c.execute('select count(*) from redo_attempts').fetchone()[0])
        self.assertEqual(1,student_learning.group_attempt_count(self.c,self.user,g))
        self.assertEqual(first_answers,[tuple(r) for r in self.c.execute('select id,initial_answer,outcome from student_responses')])
        with self.assertRaises(StateConflict):student_learning.api(self.repo,self.user,'group-submit',dict(payload,result_1='correct'))
        # An acknowledged group batch can be retried after all members leave review.
        for w in members:
            self.c.execute('delete from redo_attempts where wrong_question_id=?',(w['id'],))
            for n,date in enumerate(('2026-01-02','2026-01-05')):
                self.c.execute('insert into redo_attempts(id,school_id,wrong_question_id,student_id,outcome,purpose,submitted_at) values(?,?,?,?,?,?,?)',('pre-%s-%s'%(w['id'],n),self.user['school_id'],w['id'],self.user['id'],'correct','verify',date+'T00:00:00+00:00'))
        self.c.commit()
        done=dict(payload,request_key='group-mastered',result_1='correct')
        result=student_learning.api(self.repo,self.user,'group-submit',done)
        self.assertEqual('correct',result['outcome'])
        self.assertEqual([],student_learning.review_members(student_learning.group_for(self.repo,self.user,g['id'])))
        self.assertEqual(result,student_learning.api(self.repo,self.user,'group-submit',done))
