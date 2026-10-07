import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from highschoolphysics import learning, learning_views, response_workflow as workflow
from highschoolphysics.db import connect, initialize_database, seed_demo_data
from highschoolphysics.errors import InvalidRequest, PermissionDenied, StateConflict
from highschoolphysics.outcomes import decide
from highschoolphysics.repository import PhysicsRepository
from tests.http_support import LivePhysicsServer, seed_other_class


class ResponseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.c=connect(':memory:');initialize_database(self.c);seed_demo_data(self.c)
        self.repo=PhysicsRepository(self.c)
        self.repo.resolve_review_item('user-teacher-li','resp-1001-q2','C','核对')
        self.repo.grade_assessment('user-teacher-li','assess-week-1',publish=True)
        learning.migrate(self.c)
        self.admin=dict(self.c.execute("select * from users where role='admin'").fetchone())
        self.teacher=dict(self.c.execute("select * from users where id='user-teacher-li'").fetchone())
        self.student=dict(self.c.execute("select * from users where id='stu-1001'").fetchone())
        self.paper=self.repo.assemble_paper(self.admin['id'],'test','fixture',[{'question_id':'q-newton-1','points':0}])['paper']['id']
        self.a=self.repo.create_assessment_from_paper(self.admin['id'],self.paper,'class-physics-1','test','','高二','')['id']
        self.c.execute("update assessment_participants set status='not_included' where assessment_id=? and student_id<>'stu-1001'",(self.a,));self.c.commit()

    def tearDown(self): self.c.close()

    def payload(self,answer='B',result='',**kw):
        return dict(assessment_id=self.a,csv='学生,题号,作答,结果\nstu_1001,1,%s,%s\n'%(answer,result),request_key='import-1',**kw)

    def save(self,p):
        preview=learning.api(self.repo,self.admin,'answers',p)
        result=learning.api(self.repo,self.admin,'answers',dict(p,confirm=True,preview_token=preview['preview_token']))
        return preview,result

    def response(self,aid=None):
        return dict(self.c.execute("select * from student_responses where assessment_id=? and student_id='stu-1001' and question_id='q-newton-1'",(aid or self.a,)).fetchone())

    def correction(self,r,answer,outcome,key='correction-1'):
        p=dict(response_id=r['id'],answer=answer,outcome=outcome,reason='核对原图确认转录错误',reason_code='extraction_error',request_key=key,expected_decision_id=r['effective_decision_id'])
        preview=learning.api(self.repo,self.admin,'response-correct',p)
        self.assertTrue(preview['preview'])
        learning.api(self.repo,self.admin,'response-correct',dict(p,confirm=True,preview_token=preview['preview_token']))
        return p,preview

    def test_import_preview_required_and_immutable_evidence(self):
        p=self.payload()
        with self.assertRaises(StateConflict):learning.api(self.repo,self.admin,'answers',dict(p,confirm=True))
        preview,result=self.save(p)
        self.assertEqual(preview['records'][0]['outcome'],'correct')
        evidence=self.c.execute('select * from response_evidence where batch_id=?',(result['batch_id'],)).fetchone()
        self.assertIsNone(evidence['extraction_confidence'])
        self.assertEqual(evidence['source_row'],2)
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute("update response_evidence set raw_answer='C' where id=?",(evidence['id'],))
        self.c.rollback()
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute('delete from response_decisions where response_id=?',(self.response()['id'],))
        self.c.rollback()

    def test_preview_is_invalidated_when_decision_algorithm_changes(self):
        p=self.payload()
        preview=learning.api(self.repo,self.admin,'answers',p)
        with patch.object(workflow,'VERSION','next-version'):
            with self.assertRaises(StateConflict):
                learning.api(self.repo,self.admin,'answers',dict(p,confirm=True,preview_token=preview['preview_token']))
        self.assertEqual(self.c.execute('select count(*) from student_responses where assessment_id=?',(self.a,)).fetchone()[0],0)

    def test_external_agreement_and_conflict_review(self):
        p=self.payload('A','正确',source_type='external',source_name='教师核对表',source_reason='已逐项核对原卡')
        preview,_=self.save(p)
        self.assertEqual(preview['records'][0]['category'],'external_conflict')
        r=self.response()
        with self.assertRaises(StateConflict):learning.api(self.repo,self.admin,'publish',{'assessment_id':self.a})
        learning.api(self.repo,self.admin,'response-review',dict(response_id=r['id'],answer='A',outcome='wrong',reason='按原图确认 A，标准答案 B',request_key='review-1',expected_decision_id=r['effective_decision_id']))
        learning.api(self.repo,self.admin,'publish',{'assessment_id':self.a})
        self.assertEqual(self.response()['outcome'],'wrong')
        self.assertEqual(self.c.execute("select count(*) from response_review_items where response_id=? and status='open'",(r['id'],)).fetchone()[0],0)

    def test_source_is_required_and_no_result_only_import(self):
        for p in [self.payload('A','错误',source_type='external'),self.payload('','正确',source_type='external',source_name='表',source_reason='核对')]:
            with self.assertRaises(InvalidRequest):learning.api(self.repo,self.admin,'answers',p)
        with self.assertRaises(InvalidRequest):learning.api(self.repo,self.admin,'answers',dict(assessment_id=self.a,request_key='rows',records=[dict(student_id='stu-1001',number=1,supplied_outcome='wrong')]))

    def test_external_agreement_uses_same_effective_result(self):
        preview,_=self.save(self.payload('B','正确',source_type='external',source_name='教师确认表',source_reason='已复核'))
        self.assertEqual(preview['records'][0]['method'],'external_rule_agreement')
        self.assertEqual(self.response()['outcome'],'correct')

    def test_missing_cells_and_duplicates_are_atomic(self):
        for text in ['学生,题号,作答,结果\nstu_1001,1', '学生,题号,作答,结果\nstu_1001,1,B,\nstu_1001,1,A,', '学生,题号,作答,结果\n未知学生,1,B,']:
            with self.assertRaises(InvalidRequest):learning.api(self.repo,self.admin,'answers',dict(self.payload(),csv=text))
        self.assertEqual(self.c.execute('select count(*) from student_responses where assessment_id=?',(self.a,)).fetchone()[0],0)

    def test_stale_preview_and_key_content_conflict(self):
        p=self.payload();preview=learning.api(self.repo,self.admin,'answers',p)
        self.c.execute("update assessment_participants set status='absent' where assessment_id=? and student_id='stu-1001'",(self.a,));self.c.commit()
        with self.assertRaises((InvalidRequest,StateConflict)):learning.api(self.repo,self.admin,'answers',dict(p,confirm=True,preview_token=preview['preview_token']))
        with self.assertRaises(StateConflict):learning.api(self.repo,self.admin,'answers',dict(p,csv=p['csv'].replace(',B,',',A,')))

    def test_preview_is_bound_to_responses_changed_by_another_import(self):
        p=self.payload();preview=learning.api(self.repo,self.admin,'answers',p)
        self.save(dict(self.payload('A'),request_key='another-import'))
        with self.assertRaises(StateConflict):learning.api(self.repo,self.admin,'answers',dict(p,confirm=True,preview_token=preview['preview_token']))

    def test_blank_not_missing_and_absent_not_wrong(self):
        self.save(self.payload(''))
        learning.api(self.repo,self.admin,'publish',{'assessment_id':self.a})
        self.assertEqual(self.response()['outcome'],'blank')
        self.assertEqual(self.c.execute('select count(*) from wrong_questions where assessment_id=?',(self.a,)).fetchone()[0],1)

    def test_excluded_after_import_does_not_enter_learning_statistics(self):
        before=learning_views.metrics(self.repo,'stu-1001')
        self.c.execute("update assessment_participants set status='present' where assessment_id=? and student_id='stu-1002'",(self.a,));self.c.commit()
        p=dict(self.payload(),csv='学生,题号,作答,结果\nstu_1001,1,A,\nstu_1002,1,B,')
        self.save(p)
        learning.api(self.repo,self.admin,'participant',dict(assessment_id=self.a,student_id='stu-1001',status='absent'))
        learning.api(self.repo,self.admin,'publish',{'assessment_id':self.a})
        self.assertEqual(learning_views.metrics(self.repo,'stu-1001'),before)
        self.assertEqual(self.c.execute('select count(*) from wrong_questions where assessment_id=?',(self.a,)).fetchone()[0],0)
        publication=self.c.execute('select decisions_json from response_publications where assessment_id=?',(self.a,)).fetchone()[0]
        self.assertEqual(len(json.loads(publication)),1)

    def test_import_replay_after_publication_and_publish_protected(self):
        p=self.payload();preview,_=self.save(p)
        learning.api(self.repo,self.admin,'publish',{'assessment_id':self.a})
        self.assertTrue(learning.api(self.repo,self.admin,'answers',dict(p,confirm=True,preview_token=preview['preview_token']))['already_saved'])
        with self.assertRaises(StateConflict):learning.api(self.repo,self.admin,'publish',{'assessment_id':self.a})
        self.assertEqual(self.c.execute('select count(*) from response_publications where assessment_id=?',(self.a,)).fetchone()[0],1)

    def test_correct_misjudgment_preserves_first_and_redo_history(self):
        self.save(self.payload('A'));learning.api(self.repo,self.admin,'publish',{'assessment_id':self.a})
        r=self.response();w=dict(self.c.execute('select * from wrong_questions where response_id=?',(r['id'],)).fetchone())
        learning.submit(self.repo,self.student['id'],dict(wrong_id=w['id'],answer='B',request_key='redo-1',purpose='verify'))
        p,preview=self.correction(r,'B','correct')
        after=self.response()
        self.assertEqual(after['initial_answer'],'A');self.assertEqual(after['final_answer'],'B')
        self.assertEqual(self.c.execute('select is_active from wrong_questions where id=?',(w['id'],)).fetchone()[0],0)
        self.assertEqual(self.c.execute('select count(*) from redo_attempts where wrong_question_id=?',(w['id'],)).fetchone()[0],1)
        with self.assertRaises(StateConflict):learning.submit(self.repo,self.student['id'],dict(wrong_id=w['id'],answer='A',request_key='redo-2'))
        self.assertTrue(learning.api(self.repo,self.admin,'response-correct',dict(p,confirm=True,preview_token=preview['preview_token']))['message'].startswith('已处理'))
        self.assertEqual(self.c.execute('select count(*) from response_publications where assessment_id=?',(self.a,)).fetchone()[0],2)
        html=learning_views.exams(self.repo,self.student,self.a)
        self.assertIn('经更正的作答',html);self.assertIn('教师更正说明',html)
        self.assertNotIn(w['id'],learning_views.student(self.repo,self.student,{'module':['wrong']}))

    def test_new_error_scheduled_from_correction_not_historical_date(self):
        self.save(self.payload('B'));learning.api(self.repo,self.admin,'publish',{'assessment_id':self.a})
        r=self.response();self.c.execute("update student_responses set created_at='2020-01-01' where id=?",(r['id'],));self.c.commit()
        self.correction(r,'A','wrong')
        w=dict(self.c.execute('select * from wrong_questions where response_id=?',(r['id'],)).fetchone())
        p=learning.progress(self.c,w)
        self.assertEqual(p['due'],str(datetime.now(learning.TZ).date()+timedelta(days=1)))
        self.assertFalse(p['available'])

    def test_other_error_sources_keep_practice(self):
        r=self.response('assess-week-1')
        self.assertEqual(r['outcome'],'wrong')
        self.save(self.payload('A'));learning.api(self.repo,self.admin,'publish',{'assessment_id':self.a})
        self.correction(self.response(),'B','correct')
        html=learning_views.student(self.repo,self.student,{'module':['wrong']})
        original=self.c.execute('select id from wrong_questions where response_id=?',(r['id'],)).fetchone()[0]
        self.assertIn(original,html)

    def test_stale_correction_and_unconfirmed_write_rejected(self):
        self.save(self.payload('A'));learning.api(self.repo,self.admin,'publish',{'assessment_id':self.a})
        r=self.response();p,preview=self.correction(r,'B','correct')
        with self.assertRaises(StateConflict):learning.api(self.repo,self.admin,'response-correct',dict(p,request_key='different',confirm=True,preview_token=preview['preview_token']))
        with self.assertRaises(InvalidRequest):learning.api(self.repo,self.admin,'response-correct',dict(p,reason_code='identity_error'))

    def test_student_and_other_teacher_cannot_read_evidence(self):
        self.save(self.payload());r=self.response()
        with self.assertRaises(PermissionDenied):learning.api(self.repo,self.student,'response-history',{'response_id':r['id']})
        seed_other_class(self.c)
        other=dict(self.c.execute("select * from users where id='user-teacher-wang'").fetchone())
        with self.assertRaises(PermissionDenied):learning.api(self.repo,other,'response-history',{'response_id':r['id']})

    def test_evidence_asset_must_match_student_and_assessment(self):
        self.c.execute("insert into exam_assets(id,school_id,assessment_id,student_id,png) values('other','school-demo',?,'stu-1002',?)",(self.a,b'png'));self.c.commit()
        with self.assertRaises(PermissionDenied):learning.api(self.repo,self.admin,'answers',dict(assessment_id=self.a,request_key='asset-key',records=[dict(student_id='stu-1001',number=1,answer='B',source_asset_id='other')]))

    def test_migration_repeated_preserves_core_data(self):
        before=[tuple(r) for r in self.c.execute('select id,initial_answer,outcome from student_responses order by id')]
        count=self.c.execute('select count(*) from response_evidence').fetchone()[0]
        learning.migrate(self.c)
        self.assertEqual(before,[tuple(r) for r in self.c.execute('select id,initial_answer,outcome from student_responses order by id')])
        self.assertEqual(count,self.c.execute('select count(*) from response_evidence').fetchone()[0])
        self.assertEqual(self.c.execute('pragma integrity_check').fetchone()[0],'ok')
        self.assertFalse(self.c.execute('pragma foreign_key_check').fetchall())

    def test_concurrent_publication_creates_one_snapshot(self):
        import threading
        self.save(self.payload('A'))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'concurrent.sqlite3'
            with sqlite3.connect(path) as target:self.c.backup(target)
            barrier=threading.Barrier(2);results=[]
            def run():
                conn=connect(path)
                try:
                    barrier.wait()
                    workflow.publish(PhysicsRepository(conn),self.admin,{'assessment_id':self.a})
                    results.append('published')
                except StateConflict: results.append('blocked')
                finally: conn.close()
            threads=[threading.Thread(target=run) for _ in range(2)]
            for thread in threads:thread.start()
            for thread in threads:thread.join(timeout=10)
            self.assertEqual(sorted(results),['blocked','published'])
            conn=connect(path)
            self.assertEqual(conn.execute('select count(*) from response_publications where assessment_id=?',(self.a,)).fetchone()[0],1)
            self.assertEqual(conn.execute('select count(*) from wrong_questions where assessment_id=?',(self.a,)).fetchone()[0],1)
            conn.close()

    def test_changed_rule_cannot_publish_old_decision(self):
        self.save(self.payload())
        self.c.execute("update question_version_snapshots set grading_rule_json=? where assessment_id=?",(json.dumps({'type':'single_choice','answer':'A'}),self.a));self.c.commit()
        with self.assertRaises(StateConflict):learning.api(self.repo,self.admin,'publish',{'assessment_id':self.a})

    def test_evidence_migration_failure_rolls_back_additions(self):
        conn=connect(':memory:');initialize_database(conn);seed_demo_data(conn)
        real=workflow._execute_sqlite_script
        def fail_after_ddl(c,script):
            real(c,script)
            raise sqlite3.OperationalError('injected migration failure')
        with patch.object(workflow,'_execute_sqlite_script',fail_after_ddl):
            with self.assertRaises(sqlite3.OperationalError):learning.migrate(conn)
        self.assertIsNone(conn.execute("select 1 from sqlite_master where name='response_evidence'").fetchone())
        self.assertNotIn('effective_decision_id',[r[1] for r in conn.execute('pragma table_info(student_responses)')])
        learning.migrate(conn)
        self.assertEqual(conn.execute("select version from app_schema_migrations where feature='response_evidence'").fetchone()[0],14)
        conn.close()

    def test_http_review_and_history_require_teacher(self):
        self.save(self.payload('A','正确',source_type='external',source_name='review sheet',source_reason='核对'))
        r=self.response()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.sqlite3'
            with sqlite3.connect(path) as dest:self.c.backup(dest)
            with LivePhysicsServer(path,seed=False) as server:
                _,cookie,_=server.login('teacher_li','teacher123')
                status,_,body=server.request('GET','/exams?id='+self.a,headers={'Cookie':cookie})
                self.assertEqual(status,200);self.assertIn('确认待复核作答',body.decode())
                status,_,body=server.post_json('/api/learning/response-review',dict(response_id=r['id'],answer='A',outcome='wrong',reason='核对原图',request_key='http-review',expected_decision_id=r['effective_decision_id']),cookie)
                self.assertEqual(status,200)
                status,_,body=server.post_json('/api/learning/response-history',{'response_id':r['id']},cookie)
                self.assertEqual(status,200);self.assertGreater(len(json.loads(body)['result']['decisions']),1)
                _,cookie,_=server.login('stu_1001','student123')
                status,_,_=server.post_json('/api/learning/response-history',{'response_id':r['id']},cookie)
                self.assertEqual(status,403)


class OutcomeTests(unittest.TestCase):
    def test_choice_validation_and_uncertain_rules(self):
        self.assertEqual(decide({'type':'single_choice','answer':'B'},'BD')['outcome'],'pending')
        self.assertEqual(decide({'type':'single_choice','answer':'B'},'E',{'A':'a','B':'b'})['outcome'],'pending')
        self.assertEqual(decide({'type':'single_choice'},'A')['outcome'],'pending')
        self.assertEqual(decide({'type':'multiple_choice','answer':['B','D']},'D B')['outcome'],'correct')
        self.assertEqual(decide({'type':'multiple_choice','answer':['B','D']},'B')['outcome'],'wrong')
        self.assertEqual(decide({'type':'fill','answer':'2','match':'numeric_tolerance','tolerance':'NaN'},'2')['outcome'],'pending')
        self.assertEqual(decide({'type':'fill','answer':'2','match':'numeric_tolerance','tolerance':'0.1'},'2.05')['outcome'],'correct')
        self.assertEqual(decide({'type':'fill','answer':'10 m/s'},'10 m')['outcome'],'pending')
        self.assertEqual(decide({'type':'fill','answer':'10 m/s'},'-10 m/s')['outcome'],'pending')


if __name__=='__main__': unittest.main()
