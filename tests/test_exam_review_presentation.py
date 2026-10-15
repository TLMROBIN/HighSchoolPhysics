import unittest
import io
import json
import zipfile

from highschoolphysics import exam_review as review
from highschoolphysics.exam_review_export import build_zip
from highschoolphysics.exam_review_presentation import build_plan, render_presentation, _chunks
from highschoolphysics.errors import InvalidRequest, StateConflict, PermissionDenied
from tests.test_exam_review import ExamReviewTests as _Fixture
from tests.http_support import LivePhysicsServer, seed_other_class


class PresentationTests(unittest.TestCase):
    setUp = _Fixture.setUp
    create = _Fixture.create
    save_answers = _Fixture.save_answers
    def edit(self, pack, **extra):
        item=pack['items'][0]
        return review.save_item(self.repo,self.admin,dict({'package_id':pack['id'],'item_id':item['id'],
            'revision':item['revision'],'included':True,'emphasis':'detail','reviewed':True},**extra))
    def plan_save(self, pack, **extra):
        return review.arrange(self.repo, self.admin, dict(pack['settings'], package_id=pack['id'],
            revision=pack['revision'], item_ids=[x['id'] for x in pack['items']], **extra))

    def test_order_is_complete_atomic_and_conflicts_do_not_overwrite(self):
        pack=review.create(self.repo,self.admin,dict(assessment_id='assess-week-1',request_key='order'))
        ids=[x['id'] for x in pack['items']]
        self.assertGreater(len(ids),1)
        for order in [ids[:-1], ids+[ids[0]], ['foreign']+ids[1:]]:
            with self.assertRaises(InvalidRequest):
                review.arrange(self.repo,self.admin,dict(pack['settings'],package_id=pack['id'],revision=1,item_ids=order))
        saved=review.arrange(self.repo,self.admin,dict(pack['settings'],package_id=pack['id'],revision=1,item_ids=ids[::-1]))
        self.assertEqual(ids[::-1],[x['id'] for x in saved['items']])
        with self.assertRaises(StateConflict): self.plan_save(pack)
        self.assertEqual(ids[::-1],[x['id'] for x in review.get(self.repo,self.admin,pack['id'])['items']])
        self.assertFalse(saved['stale'])
        with self.assertRaises(PermissionDenied):
            review.arrange(self.repo,self.student,dict(package_id=pack['id']))

    def test_slide_plan_respects_hidden_fields_and_brief_notes(self):
        pack=self.create()
        item=pack['items'][0]
        item['content'].update(solution_steps='解题公式 $F=ma$',notes='教师私有备注',teacher_errors='教师确认的原因')
        item['emphasis']='detail'
        plan=build_plan(pack)
        self.assertEqual('question',plan['slides'][0]['kind'])
        self.assertNotIn('正确率',plan['slides'][0]['markdown'])
        self.assertTrue(any('解题公式' in x['markdown'] for x in plan['slides']))
        html=render_presentation(pack)
        self.assertNotIn('教师私有备注',html)
        self.assertIn('教师私有备注',render_presentation(pack,preview=True))
        item['emphasis']='brief'
        plan=build_plan(pack)
        self.assertFalse(any(x['kind']=='explanation' for x in plan['slides']))
        self.assertIn('解题公式',plan['slides'][0]['speaker_notes'])
        pack['settings'].update(show_answer=False,show_statistics=False,show_solution=False,show_errors=False,show_knowledge=False)
        html=render_presentation(pack)
        self.assertNotIn('教师确认的原因',html)
        self.assertNotIn('正确率',html)
        self.assertNotIn('参考答案',html)
        pack['settings'].update(show_answer=True,reveal_mode='together')
        self.assertIn('参考答案',build_plan(pack)['slides'][0]['inline_answer'])

    def test_fork_recalculates_class_stats_keeps_order_and_resets_diagnoses(self):
        self.save_answers()
        seed_other_class(self.conn)
        other=self.conn.execute("select id from class_groups where id!='class-physics-1' and school_id='school-demo'").fetchone()[0]
        self.conn.execute('update users set class_id=? where id=?',(other,'stu-1002'))
        self.conn.commit()
        pack=self.create()
        self.edit(pack,content={'solution_steps':'教师思路','teacher_errors':'原班错因','possible_errors':'原班假设','notes':'原班备注'},reviewed=True)
        child=review.fork(self.repo,self.admin,dict(package_id=pack['id'],class_ids=[other],request_key='fork'))
        self.assertEqual(child['id'],review.fork(self.repo,self.admin,dict(package_id=pack['id'],class_ids=[other],request_key='fork'))['id'])
        item=child['items'][0]
        self.assertEqual(1,item['source']['statistics']['present_count'])
        self.assertEqual(0,item['source']['statistics']['correct_rate'])
        self.assertEqual('教师思路',item['content']['solution_steps'])
        for field in ['teacher_errors','possible_errors','notes']: self.assertEqual('',item['content'][field])
        self.assertFalse(item['reviewed'])
        self.assertFalse(child['stale'])
        self.assertEqual('原班错因',review.get(self.repo,self.admin,pack['id'])['items'][0]['content']['teacher_errors'])
        with self.assertRaises(InvalidRequest): review.fork(self.repo,self.admin,dict(package_id=pack['id'],class_ids=[],request_key='no'))

    def test_export_uses_identical_saved_plan_and_portable_presentations(self):
        pack=self.create()
        self.edit(pack,content={'notes':'ONLY_TEACHER_NOTE','solution_steps':'$F=ma$'})
        pack=self.plan_save(pack)
        archive=zipfile.ZipFile(io.BytesIO(build_zip(self.repo,self.admin,pack['id'],self.path)))
        plan=json.loads(archive.read('逐页安排.json'))
        self.assertEqual(build_plan(pack),plan)
        presented=archive.read('课堂演示.html').decode()
        self.assertNotIn('ONLY_TEACHER_NOTE',presented)
        self.assertIn('ONLY_TEACHER_NOTE',archive.read('备课预览.html').decode())
        self.assertIn('vendor/katex/katex.min.js',presented)
        self.assertNotIn('/api/exam-review/',presented)
        self.assertEqual('hsp-exam-review-v2',json.loads(archive.read('manifest.json'))['schema'])

    def test_pagination_preserves_long_markdown_and_math_and_escapes_html(self):
        text='开头\n\n'+'长段落。'*500+'\n\n$$\nx = a+b\n\n+y\n$$\n\n最后一段'
        chunks=_chunks(text)
        self.assertEqual(text,''.join(chunks))
        self.assertTrue(any('$$\nx = a+b\n\n+y\n$$' in x for x in chunks))
        pack=self.create()
        pack['items'][0]['source']['prompt_markdown']='<script>alert(1)</script>\n\n'+text
        rendered=render_presentation(pack)
        self.assertNotIn('<script>alert(1)',rendered)
        self.assertIn('最后一段',rendered)
        pack['items'][0]['included']=False
        self.assertIn('尚未选择讲评题目',render_presentation(pack))

    def test_http_preview_permissions_and_new_write_origin(self):
        pack=self.create()
        with LivePhysicsServer(self.path) as server:
            _,cookie,_=server.login('admin','admin123')
            _,student_cookie,_=server.login('stu_1001','student123')
            for page in ['present.html','preview.html']:
                path='/api/exam-review/'+pack['id']+'/'+page
                self.assertEqual(403,server.request('GET',path)[0])
                self.assertEqual(403,server.request('GET',path,headers={'Cookie':student_cookie})[0])
                status,_,body=server.request('GET',path,headers={'Cookie':cookie,'X-Forwarded-Prefix':'/physics'})
                self.assertEqual(200,status,body[:200])
                self.assertIn(b'data-base-path="/physics"',body)
            payload=dict(pack['settings'],package_id=pack['id'],revision=1,item_ids=[x['id'] for x in pack['items']])
            self.assertEqual(403,server.post_json('/api/learning/review-arrange',payload,cookie)[0])


del _Fixture
