import json
import unittest
from datetime import date
from highschoolphysics.db import connect,initialize_database,seed_demo_data
from highschoolphysics.repository import PhysicsRepository
from highschoolphysics import learning,learning_views
from highschoolphysics.errors import StateConflict,PermissionDenied,InvalidRequest
from highschoolphysics.document_models import canonical_sha256

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
  self.assertEqual(learning.check({'type':'experiment','answer':'读数正确'},'读数为2.0 cm'),'pending')
  self.assertEqual(learning.check({'type':'fill','answer':'2'},''),'blank')
 def test_views_hide_solution_and_other_student(self):
  user=dict(self.c.execute("select * from users where id='stu-1001'").fetchone())
  out=learning_views.student(self.repo,user,{'practice':[self.w['id']]})
  self.assertNotIn('李华',out);self.assertIn('首次作答记录',out);self.assertNotIn('我的得分',out)
  self.assertNotIn('correct_answer_json',out)
  teacher_html=learning_views.teacher(self.repo,self.admin,{})
  self.assertIn('教师工作台',teacher_html)
  self.assertIn('data-question-pagination',teacher_html)
  self.assertIn('展开题干与全部小问',teacher_html)
  with self.assertRaises(PermissionDenied):learning.submit(self.repo,'stu-1002',dict(wrong_id=self.w['id'],request_key='x',answer='B'))
 def test_fill_wrong_view_explains_parent_question_context(self):
  self.assertIn('完整填空题',learning_views.fill_question_context({'question_type':'fill'}))
  self.assertEqual('',learning_views.fill_question_context({'question_type':'single_choice'}))
 def test_create_import_preview_publish_without_scores(self):
  p={'stem':'一个新的多选题','question_type':'multiple_choice','answer':'BD','options':'甲\n乙\n丙\n丁','knowledge':'kn-pep2019-r1-c04-s03','ability':'ab-model-construction','literacy':self.repo.literacy_tags()[0]['id']}
  learning.api(self.repo,self.admin,'question',p)
  q=self.c.execute('select id from questions where stem=?',(p['stem'],)).fetchone()[0]
  a=learning.api(self.repo,self.admin,'assessment',dict(title='无分数周测',class_id='class-physics-1',questions=[q]))['url'].split('=')[1]
  students=self.c.execute('select u.username from users u join assessment_participants p on p.student_id=u.id where p.assessment_id=?',(a,)).fetchall()
  csv='学生,题号,作答,结果\n'+'\n'.join(r[0]+',1,B,' for r in students)
  payload=dict(assessment_id=a,csv=csv,request_key='test-import-1')
  preview=learning.api(self.repo,self.admin,'answers',payload)
  self.assertTrue(preview['preview'])
  payload['preview_token']=preview['preview_token']
  with self.assertRaises(StateConflict):learning.api(self.repo,self.admin,'publish',{'assessment_id':a})
  learning.api(self.repo,self.admin,'answers',dict(payload,confirm=True));learning.api(self.repo,self.admin,'publish',{'assessment_id':a})
  self.assertEqual(self.c.execute('select count(*) from wrong_questions where assessment_id=?',(a,)).fetchone()[0],len(students))
  self.assertIsNone(self.c.execute('select score from student_responses where assessment_id=?',(a,)).fetchone()[0])
  self.assertTrue(learning.api(self.repo,self.admin,'answers',dict(payload,confirm=True))['already_saved'])
  with self.assertRaises(StateConflict):learning.api(self.repo,self.admin,'answers',dict(payload,request_key='new-import',confirm=True))
 def test_numeric_fill_rule_survives_snapshot_import_publish_and_practice(self):
  p=dict(stem='速度的单位换算',question_type='fill',answer='10',fill_match='numeric_quantity',unit='m/s',unit_required='on',allow_unit_conversion='on',absolute_tolerance='0.01',significant_figures='3',knowledge='kn-pep2019-r1-c04-s03',ability='ab-model-construction',literacy=self.repo.literacy_tags()[0]['id'])
  learning.api(self.repo,self.admin,'question',p)
  q=self.c.execute('select id from questions where stem=?',(p['stem'],)).fetchone()[0]
  a=learning.api(self.repo,self.admin,'assessment',dict(title='数值规则验收',class_id='class-physics-1',questions=[q]))['url'].split('=')[1]
  s=self.c.execute('select * from question_version_snapshots where assessment_id=?',(a,)).fetchone()
  rule=json.loads(s['grading_rule_json']);self.assertEqual(rule['unit'],'m/s');self.assertTrue(rule['unit_required']);self.assertEqual(rule['significant_figures'],3)
  users=self.c.execute("select u.id,u.username from users u join assessment_participants p on p.student_id=u.id where p.assessment_id=? order by u.id",(a,)).fetchall()
  csv='学生,题号,作答,结果\n'+'\n'.join(u['username']+',1,'+('-10.0 m/s' if i==0 else '36.0 km/h')+',' for i,u in enumerate(users))
  payload=dict(assessment_id=a,csv=csv,request_key='numeric-import')
  preview=learning.api(self.repo,self.admin,'answers',payload)
  self.assertEqual(preview['records'][0]['outcome'],'wrong');self.assertTrue(all(r['outcome']=='correct' for r in preview['records'][1:]))
  learning.api(self.repo,self.admin,'answers',dict(payload,confirm=True,preview_token=preview['preview_token']))
  learning.api(self.repo,self.admin,'publish',dict(assessment_id=a))
  w=self.c.execute('select * from wrong_questions where assessment_id=?',(a,)).fetchone()
  attempt=learning.submit(self.repo,users[0]['id'],dict(wrong_id=w['id'],answer='36.0 km/h',request_key='numeric-practice'))
  self.assertEqual(attempt['outcome'],'correct')
  self.assertEqual(self.c.execute('select initial_answer from student_responses where assessment_id=? and student_id=?',(a,users[0]['id'])).fetchone()[0],'-10.0 m/s')
  self.assertFalse(self.c.execute('pragma foreign_key_check').fetchall())
 def test_invalid_numeric_configuration_does_not_create_question(self):
  before=self.c.execute('select count(*) from questions').fetchone()[0]
  p=dict(stem='无效容差',question_type='fill',answer='10',fill_match='numeric_quantity',unit='m',absolute_tolerance='-1',knowledge='kn-pep2019-r1-c04-s03',ability='ab-model-construction',literacy=self.repo.literacy_tags()[0]['id'])
  with self.assertRaises(InvalidRequest):learning.api(self.repo,self.admin,'question',p)
  self.assertEqual(self.c.execute('select count(*) from questions').fetchone()[0],before)
 def test_experiment_group_is_selected_and_rendered_as_one_complete_question(self):
  question_ids=[]
  for label,stem,answer in [('1','记录小车运动位置','由纸带读取位置'),('2','求小车加速度','根据位移数据计算')]:
   question=self.repo.create_question(self.admin['id'],stem,{}, {'answer':answer},'', 'experiment','实验题入库','高三','','medium')
   self.repo.confirm_question_tags(
    self.admin['id'],question['id'],
    knowledge_node_ids=['kn-pep2019-r1-c04-s03'],
    ability_tag_ids=['ab-model-construction'],
    literacy_tag_ids=[self.repo.literacy_tags()[0]['id']],
   )
   question_ids.append(question['id'])
  document={
   'schema_version':1,'number':'5','kind':'experiment','stem_md':'研究小车的匀变速直线运动。',
   'options':[],'answer_md':'','analysis_md':'','answer_state':'verified','grading_rule':None,
   'children':[
    {'key':'part-1','label':'1','kind':'experiment','stem_md':'记录小车运动位置','answer_md':'由纸带读取位置','analysis_md':'','answer_state':'verified','options':[],'source_spans':[]},
    {'key':'part-2','label':'2','kind':'experiment','stem_md':'求小车加速度','answer_md':'根据位移数据计算','analysis_md':'','answer_state':'verified','options':[],'source_spans':[]},
   ],'source_spans':[],'asset_refs':[],'issues':[],
  }
  group_id='content-test-complete-experiment';revision_id='revision-test-complete-experiment'
  self.c.execute('insert into question_content_groups(id,school_id,original_paper_id,source_item_id,current_revision_id,created_by) values(?,?,?,?,NULL,?)',(group_id,self.admin['school_id'],None,None,self.admin['id']))
  self.c.execute('insert into question_content_revisions(id,group_id,revision_no,schema_version,document_json,content_sha256,review_state,answer_state,created_by,change_reason) values(?,?,1,1,?,?,?,?,?,?)',(revision_id,group_id,json.dumps(document,ensure_ascii=False,sort_keys=True),canonical_sha256(document),'verified','verified',self.admin['id'],'unit fixture'))
  self.c.execute('update question_content_groups set current_revision_id=? where id=?',(revision_id,group_id))
  for question_id,child_key in zip(question_ids,['part-1','part-2']):
   self.c.execute('insert into question_content_bindings(question_id,group_id,child_key) values(?,?,?)',(question_id,group_id,child_key))
  self.c.commit()
  with self.assertRaisesRegex(InvalidRequest,'整道大题'):
   learning.api(self.repo,self.admin,'assessment',dict(title='实验题小问保护',class_id='class-physics-1',questions=[question_ids[0]]))
  assessment_url=learning.api(self.repo,self.admin,'assessment',dict(title='完整实验题',class_id='class-physics-1',questions=question_ids))['url']
  assessment_id=assessment_url.split('=')[1]
  snapshot=self.c.execute('select * from question_version_snapshots where assessment_id=? order by position limit 1',(assessment_id,)).fetchone()
  self.assertEqual(learning.outcome_for_snapshot(self.c,snapshot,self.admin['school_id'],'读数为2.0 cm'),'pending')
  exam_html=learning_views.exams(self.repo,self.admin,assessment_id)
  self.assertEqual(exam_html.count('研究小车的匀变速直线运动。'),1)
  self.assertIn('第5题 · 2个小问',exam_html)
  self.assertIn('小问 1 · 作答序号 1',exam_html)
  self.assertIn('小问 2 · 作答序号 2',exam_html)
  teacher_html=learning_views.teacher(self.repo,self.admin,{})
  self.assertIn('data-selectable="true"',teacher_html)
  template=json.loads(self.c.execute('select template_json from answer_card_templates where id=(select answer_card_template_id from assessment_sessions where id=?)',(assessment_id,)).fetchone()[0])
  self.assertEqual([region['locator'] for region in template['regions']],['第5题（1）','第5题（2）'])
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
