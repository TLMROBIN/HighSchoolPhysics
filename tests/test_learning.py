import json
import unittest
from datetime import date
from highschoolphysics.db import connect,initialize_database,seed_demo_data
from highschoolphysics.repository import PhysicsRepository
from highschoolphysics import learning,learning_views
from highschoolphysics.errors import StateConflict,PermissionDenied

class LearningTests(unittest.TestCase):
 def setUp(self):
  self.c=connect(':memory:');initialize_database(self.c);seed_demo_data(self.c);self.repo=PhysicsRepository(self.c)
  self.admin=dict(self.c.execute("select * from users where role='admin'").fetchone())
  self.repo.resolve_review_item('user-teacher-li','resp-1001-q2','C','核对')
  self.repo.grade_assessment('user-teacher-li','assess-week-1',publish=True)
  self.before=[tuple(r) for r in self.c.execute('select id,final_answer,score,max_score from student_responses')]
  learning.migrate(self.c)
  self.w=dict(self.c.execute("select * from wrong_questions where student_id='stu-1001' limit 1").fetchone())
 def tearDown(self): self.c.close()
 def test_migration_preserves_answers_and_removes_scores(self):
  learning.migrate(self.c)
  for rid,answer,score,maximum in self.before:
   r=self.c.execute('select * from student_responses where id=?',(rid,)).fetchone()
   self.assertEqual(r['initial_answer'],answer);self.assertIsNone(r['score']);self.assertIsNone(r['max_score'])
   self.assertEqual(r['outcome'],'blank' if not (answer or '').strip() else 'correct' if score==maximum else 'wrong')
  self.assertFalse(self.c.execute('pragma foreign_key_check').fetchall())
  self.assertEqual(self.c.execute('pragma integrity_check').fetchone()[0],'ok')
 def test_three_spaced_correct_reset_and_same_day(self):
  self.c.execute("update student_responses set created_at='2026-01-01T00:00:00+00:00'")
  def add(i,day,result):
   self.c.execute('insert into redo_attempts(id,school_id,wrong_question_id,student_id,answer,outcome,purpose,submitted_at) values(?,?,?,?,?,?,?,?)',(str(i),self.w['school_id'],self.w['id'],self.w['student_id'],'A',result,'verify',day+'T00:00:00+00:00'))
  add(1,'2026-01-02','correct');add(2,'2026-01-02','correct');add(3,'2026-01-03','correct')
  self.assertEqual(learning.progress(self.c,self.w,date(2026,1,4))['count'],1)
  add(4,'2026-01-05','correct');add(5,'2026-01-12','correct')
  self.assertEqual(learning.progress(self.c,self.w,date(2026,1,12))['count'],3)
  add(6,'2026-01-13','wrong');p=learning.progress(self.c,self.w,date(2026,1,13));self.assertEqual(p['count'],0);self.assertEqual(p['due'],'2026-01-14')
 def test_pending_review_replays_submission_time(self):
  self.c.execute("update student_responses set created_at='2026-01-01T00:00:00+00:00'")
  self.c.execute('insert into redo_attempts(id,school_id,wrong_question_id,student_id,outcome,purpose,submitted_at) values(?,?,?,?,?,?,?)',('pending',self.w['school_id'],self.w['id'],self.w['student_id'],'pending','verify','2026-01-02T00:00:00+00:00'));self.c.commit()
  self.assertTrue(learning.progress(self.c,self.w)['pending'])
  learning.api(self.repo,self.admin,'review',dict(attempt_id='pending',outcome='correct'))
  self.assertEqual(learning.progress(self.c,self.w)['due'],'2026-01-05')
  learning.api(self.repo,self.admin,'review',dict(attempt_id='pending',outcome='wrong'))
  self.assertEqual(learning.progress(self.c,self.w)['count'],0)
 def test_idempotency_and_solution_is_learning(self):
  uid=self.w['student_id'];self.c.execute("update student_responses set created_at='2026-01-01'");self.c.commit()
  p=dict(wrong_id=self.w['id'],answer='B',request_key='unique',purpose='verify')
  one=learning.submit(self.repo,uid,p);two=learning.submit(self.repo,uid,p);self.assertEqual(one['id'],two['id'])
  self.assertEqual(one['purpose'],'verify')
  with self.assertRaises(StateConflict):learning.submit(self.repo,uid,dict(p,answer='C'))
  # Resolve if the selected fixture is a fill question.
  if one['outcome']=='pending':learning.api(self.repo,self.admin,'review',dict(attempt_id=one['id'],outcome='wrong'))
  learning.api(self.repo,{'id':uid,'role':'student'},'solution',{'wrong_id':self.w['id']})
  three=learning.submit(self.repo,uid,dict(p,request_key='another'))
  self.assertEqual(three['purpose'],'learn')
 def test_choice_and_uncertain_fill(self):
  self.assertEqual(learning.check({'type':'multiple_choice','answer':['B','D']},'D B'),'correct')
  self.assertEqual(learning.check({'type':'multiple_choice','answer':['B','D']},'B'),'wrong')
  self.assertEqual(learning.check({'type':'fill','answer':'匀加速'},'相同时间速度变化相同'),'pending')
  self.assertEqual(learning.check({'type':'fill','answer':'2'},''),'blank')
 def test_views_hide_solution_and_other_student(self):
  user=dict(self.c.execute("select * from users where id='stu-1001'").fetchone())
  out=learning_views.student(self.repo,user,{'practice':[self.w['id']]})
  self.assertNotIn('李华',out);self.assertIn('首次作答记录',out);self.assertNotIn('我的得分',out)
  self.assertNotIn('correct_answer_json',out)
  self.assertIn('教师工作台',learning_views.teacher(self.repo,self.admin,{}))
  with self.assertRaises(PermissionDenied):learning.submit(self.repo,'stu-1002',dict(wrong_id=self.w['id'],request_key='x',answer='B'))
 def test_create_import_preview_publish_without_scores(self):
  p={'stem':'一个新的多选题','question_type':'multiple_choice','answer':'BD','options':'甲\n乙\n丙\n丁','knowledge':'kn-pep2019-r1-c04-s03','ability':'ab-model-construction'}
  learning.api(self.repo,self.admin,'question',p)
  q=self.c.execute('select id from questions where stem=?',(p['stem'],)).fetchone()[0]
  a=learning.api(self.repo,self.admin,'assessment',dict(title='无分数周测',class_id='class-physics-1',questions=[q]))['url'].split('=')[1]
  students=self.c.execute('select u.username from users u join assessment_participants p on p.student_id=u.id where p.assessment_id=?',(a,)).fetchall()
  csv='学生,题号,作答,结果\n'+'\n'.join(r[0]+',1,B,' for r in students)
  payload=dict(assessment_id=a,csv=csv)
  self.assertTrue(learning.api(self.repo,self.admin,'answers',payload)['preview'])
  with self.assertRaises(StateConflict):learning.api(self.repo,self.admin,'publish',{'assessment_id':a})
  learning.api(self.repo,self.admin,'answers',dict(payload,confirm=True));learning.api(self.repo,self.admin,'publish',{'assessment_id':a})
  self.assertEqual(self.c.execute('select count(*) from wrong_questions where assessment_id=?',(a,)).fetchone()[0],len(students))
  self.assertIsNone(self.c.execute('select score from student_responses where assessment_id=?',(a,)).fetchone()[0])
  with self.assertRaises(StateConflict):learning.api(self.repo,self.admin,'answers',dict(payload,confirm=True))
 def test_http_active_mode_blocks_grades_and_serves_outcomes(self):
  import tempfile,sqlite3
  from pathlib import Path
  from tests.http_support import LivePhysicsServer
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'test.sqlite3'
   with sqlite3.connect(path) as dest:self.c.backup(dest)
   with LivePhysicsServer(path,seed=False) as server:
    _,cookie,_=server.login('teacher_li','teacher123')
    status,_,body=server.request('GET','/teacher',headers={'Cookie':cookie})
    self.assertEqual(status,200);self.assertIn('教师工作台',body.decode())
    status,_,_=server.post_json('/api/teacher/grade',{'assessment_id':'assess-week-1'},cookie)
    self.assertEqual(status,400)
    _,cookie,_=server.login('stu_1001','student123')
    status,_,body=server.post_json('/api/learning/submit',dict(wrong_id=self.w['id'],answer='B',request_key='http-key',purpose='verify'),cookie)
    self.assertEqual(status,200);self.assertIsNone(json.loads(body)['result']['score'])
    status,_,body=server.request('GET','/exams?id=assess-week-1',headers={'Cookie':cookie})
    self.assertEqual(status,200);self.assertNotIn('李华',body.decode());self.assertNotIn('我的得分',body.decode())
