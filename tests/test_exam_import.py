import base64
import copy
import unittest
from unittest.mock import patch

from highschoolphysics.db import connect, initialize_database, seed_demo_data
from highschoolphysics.repository import PhysicsRepository
from highschoolphysics.exam_import import import_bundle, get_asset
from highschoolphysics.exam_views import render_exams
from highschoolphysics.grading import grade_answer
from highschoolphysics.errors import InvalidRequest, PermissionDenied, StateConflict

PNG=base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'test-fixture').decode()

class ExamImportTests(unittest.TestCase):
    def setUp(self):
        self.conn=connect(':memory:');initialize_database(self.conn);seed_demo_data(self.conn)
        self.repo=PhysicsRepository(self.conn)
        self.admin=self.conn.execute("select id from users where role='admin'").fetchone()[0]
        self.stu=self.conn.execute("select * from users where username='stu_1001'").fetchone()
        self.bundle={'format':'hsp-exam-v1','batch_key':'test-1','title':'选择填空周测','grade':'高三','questions':[
            {'number':'8','stem':'多选题','question_type':'multiple_choice','points':6,'options':dict(A='甲',B='乙',C='丙',D='丁'),
             'answer':{'answer':['B','D'],'partial_points':3},'images':[PNG],
             'knowledge_node_ids':['kn-pep2019-r1-c04-s03'],'ability_tag_ids':['ab-model-construction']},
            {'number':'11(2)','stem':'实验规律','question_type':'fill','points':2,'answer':{'answer':'匀加速'},
             'knowledge_node_ids':['kn-pep2019-r1-c04-s03'],'ability_tag_ids':['ab-model-construction']}
        ],'classes':[{'class_id':self.stu['class_id'],'students':[{'student_id':self.stu['id'],'name':self.stu['display_name'],'scan_image':PNG,
            'responses':{'8':{'answer':'B'},'11(2)':{'answer':'相同时间速度变化相同','confirmed_score':2,'score_reason':'原图视觉核对，等价表达'}}}]}]}
    def tearDown(self):self.conn.close()
    def test_preview_atomic_publish_retry_and_student_visibility(self):
        before=self.conn.execute('select count(*) from questions').fetchone()[0]
        result=import_bundle(self.repo,self.admin,self.bundle)
        self.assertEqual(result['response_count'],2)
        self.assertEqual(self.conn.execute('select count(*) from questions').fetchone()[0],before)
        result=import_bundle(self.repo,self.admin,self.bundle,preview=False)
        aid=result['assessments'][0]['id']
        scores=[r[0] for r in self.conn.execute('select score from student_responses where assessment_id=? order by max_score desc',(aid,))]
        self.assertEqual(scores,[3,2])
        self.assertEqual(result['assessments'][0]['wrong_count'],1)
        repeated=import_bundle(self.repo,self.admin,self.bundle,preview=False)
        self.assertTrue(repeated['already_imported'])
        self.assertEqual(self.conn.execute('select count(*) from questions').fetchone()[0],before+2)
        page=render_exams(self.repo,dict(self.stu),aid)
        self.assertIn('相同时间速度变化相同',page)
        self.assertIn('知识点',page);self.assertIn('能力',page)
        self.assertNotIn('李华',page)
        rates=self.repo.class_diagnostics(self.admin,aid)
        self.assertEqual(rates['knowledge_error_rates'][0]['error_rate'],.5)
    def test_invalid_record_rolls_back_and_different_retry_rejected(self):
        bad=copy.deepcopy(self.bundle);bad['classes'][0]['students'][0]['responses'].pop('8')
        with self.assertRaises(InvalidRequest):import_bundle(self.repo,self.admin,bad,False)
        self.assertEqual(self.conn.execute('select count(*) from exam_imports').fetchone()[0],0)
        with patch.object(PhysicsRepository,'grade_assessment',side_effect=RuntimeError('simulate failure')):
            with self.assertRaises(RuntimeError):import_bundle(self.repo,self.admin,self.bundle,False)
        self.assertEqual(self.conn.execute('select count(*) from exam_assets').fetchone()[0],0)
        import_bundle(self.repo,self.admin,self.bundle,False)
        bad=copy.deepcopy(self.bundle);bad['title']='变化'
        with self.assertRaises(StateConflict):import_bundle(self.repo,self.admin,bad,False)
    def test_private_evidence_and_student_import_denial(self):
        r=import_bundle(self.repo,self.admin,self.bundle,False)
        media=self.conn.execute('select id from exam_assets where student_id=?',(self.stu['id'],)).fetchone()[0]
        self.assertTrue(get_asset(self.repo,self.stu['id'],media).startswith(b'\x89PNG'))
        other=self.conn.execute("select id from users where role='student' and id!=?",(self.stu['id'],)).fetchone()[0]
        with self.assertRaises(PermissionDenied):get_asset(self.repo,other,media)
        with self.assertRaises(PermissionDenied):import_bundle(self.repo,self.stu['id'],self.bundle,False)
    def test_options_normalization_and_partial_credit(self):
        rule={'type':'multiple_choice','points':6,'answer':['B','D'],'partial_points':3}
        for answer,score in [('BD',6),(['D','B'],6),('B，D',6),('B',3),('',0),('BCD',0),('A',0)]:
            self.assertEqual(grade_answer(rule,answer)['score'],score)

    def test_chunked_upload_is_complete_and_owned_before_preview(self):
        import json
        from highschoolphysics.exam_import import stage_chunk, staged_bundle
        text=json.dumps(self.bundle,ensure_ascii=False)
        half=len(text)//2
        result=stage_chunk(self.repo,self.admin,{'index':0,'total':2,'text':text[:half]})
        uid=result['upload_id']
        with self.assertRaises(StateConflict):staged_bundle(self.repo,self.admin,uid)
        with self.assertRaises(PermissionDenied):staged_bundle(self.repo,self.stu['id'],uid)
        stage_chunk(self.repo,self.admin,{'upload_id':uid,'index':1,'total':2,'text':text[half:]})
        self.assertEqual(staged_bundle(self.repo,self.admin,uid),self.bundle)
        self.assertEqual(import_bundle(self.repo,self.admin,staged_bundle(self.repo,self.admin,uid))['student_count'],1)

    def test_unpublished_taxonomy_is_actionable_and_blocks_import(self):
        self.conn.execute("update knowledge_ontology_versions set status='draft'");self.conn.commit()
        with self.assertRaisesRegex(StateConflict,'知识体系'):
            import_bundle(self.repo,self.admin,self.bundle)

    def test_three_reviewed_correct_redos_and_failed_retry_stays_visible(self):
        import_bundle(self.repo,self.admin,self.bundle,False)
        wrong=self.conn.execute("select id from wrong_questions where student_id=? and assessment_id!='assess-week-1'",(self.stu['id'],)).fetchone()[0]
        for i in range(3):
            attempt=self.repo.submit_redo_attempt(self.stu['id'],wrong,['B','D'])
            self.repo.review_redo_attempt(self.admin,attempt['id'],6)
            row=self.conn.execute('select latest_redo_status from wrong_questions where id=?',(wrong,)).fetchone()
            self.assertEqual(row[0],'done' if i==2 else 'reviewed')
        # Reviewing the same attempt twice cannot count as another success.
        self.repo.review_redo_attempt(self.admin,attempt['id'],6)
        self.assertEqual(self.repo.verified_redo_correct_count(wrong),3)
        failed=self.repo.submit_redo_attempt(self.stu['id'],wrong,'A')
        self.repo.review_redo_attempt(self.admin,failed['id'],0)
        detail=self.repo.wrong_question_detail(self.admin,wrong)
        self.assertTrue(self.repo._wrong_needs_redo(detail))

    def test_explicit_admin_roster_addition_preserves_current_identity(self):
        from tests.http_support import seed_other_class
        seed_other_class(self.conn)
        other=self.conn.execute("select * from users where id='stu-2001'").fetchone()
        addition={'student_id':other['id'],'name':other['display_name'],'current_class_id':other['class_id'],'reason':'本次参测名单已确认，保留现在的班级身份'}
        student=copy.deepcopy(self.bundle['classes'][0]['students'][0]);student.update(student_id=other['id'],name=other['display_name'])
        self.bundle['classes'][0]['students'].append(student)
        with self.assertRaises(InvalidRequest):import_bundle(self.repo,self.admin,self.bundle)
        self.bundle['classes'][0]['roster_additions']=[addition]
        with self.assertRaises(PermissionDenied):import_bundle(self.repo,'user-teacher-li',self.bundle)
        result=import_bundle(self.repo,self.admin,self.bundle,False)
        self.assertEqual(result['student_count'],2)
        self.assertEqual(self.conn.execute('select class_id from users where id=?',(other['id'],)).fetchone()[0],other['class_id'])
        aid=result['assessments'][0]['id']
        self.assertIn(other['display_name'],render_exams(self.repo,dict(other),aid))
