import copy
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from highschoolphysics import exam_review as review, exam_review_export as exporting, exam_review_worker as worker, learning, learning_views
from highschoolphysics.db import connect, initialize_database, seed_demo_data
from highschoolphysics.errors import InvalidRequest, PermissionDenied, StateConflict, ResourceNotFound
from highschoolphysics.exam_statistics import question_statistics
from highschoolphysics.repository import PhysicsRepository
from highschoolphysics.question_export import QuestionExportError
from tests.http_support import LivePhysicsServer, seed_other_class
from tests.test_document_models import sample_question
from tests.test_question_export import PNG


def response(student, answer='', outcome='wrong', **extra):
    return dict(student_id=student, final_answer=answer, outcome=outcome, review_status='reviewed', **extra)


class ExamStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.question = dict(options={'A': '甲', 'B': '乙', 'C': '丙'}, grading_rule={'type': 'multiple_choice', 'answer': 'AB'})

    def test_multiple_choice_marginals_combinations_and_unknown_coverage(self):
        rows = [response('1', 'AB', 'correct'), response('2', 'A'), response('3', '', 'blank'),
                response('4', '', 'correct'), response('5', 'C', 'pending'), response('6', 'Z')]
        stats = question_statistics(self.question, rows, 7)
        self.assertEqual((5, 2, 1, 1), (stats['confirmed_count'], stats['correct_count'], stats['pending_count'], stats['missing_count']))
        self.assertEqual((3, 2), (stats['option_denominator'], stats['option_unknown_count']))
        self.assertEqual(.4, stats['correct_rate'])
        self.assertEqual([2/3, 1/3, 0], [x['rate'] for x in stats['options']])
        self.assertEqual({'AB', 'A', '空白'}, {x['answer'] for x in stats['combinations']})
        all_selected = question_statistics(self.question, [response('a', 'ABC'), response('b', 'ABC')], 2)
        self.assertEqual(3, sum(x['rate'] for x in all_selected['options']))

    def test_empty_data_and_pending_never_become_zero_or_blank(self):
        stats = question_statistics(self.question, [], 2)
        self.assertIsNone(stats['correct_rate'])
        self.assertIsNone(stats['options'][0]['rate'])
        self.assertEqual((2, 0), (stats['missing_count'], stats['blank_count']))
        stats = question_statistics(self.question, [response('1', 'A', 'correct', **{}) | {'review_status': 'pending'}], 2)
        self.assertEqual((0, 1, 1), (stats['confirmed_count'], stats['pending_count'], stats['missing_count']))

    def test_single_choice_invalid_combo_and_join_fanout(self):
        self.question['grading_rule']['type'] = 'single_choice'
        row = response('1', 'AB')
        stats = question_statistics(self.question, [row, row, response('2', 'B', 'correct')], 2)
        self.assertEqual((2, 1, 1), (stats['recorded_count'], stats['option_denominator'], stats['option_unknown_count']))

    def test_score_rate_uses_only_effective_finite_complete_scores(self):
        rows = [response('1', '', 'correct', imported_score=5, imported_max_score=5),
                response('2', '', 'wrong', imported_score=1, imported_max_score=5),
                response('3', '', 'pending', imported_score=5, imported_max_score=5),
                response('4', '', 'wrong', imported_score=float('nan'), imported_max_score=5),
                response('5', '', 'wrong', imported_score=3, imported_max_score=None)]
        stats = question_statistics(self.question, rows, 5)
        self.assertEqual((2, .6), (stats['score_count'], stats['score_rate']))
        self.assertEqual(0, stats['option_denominator'])


class ExamReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'school.sqlite3'
        self.conn = connect(self.path)
        self.addCleanup(self.conn.close)
        initialize_database(self.conn)
        seed_demo_data(self.conn)
        self.repo = PhysicsRepository(self.conn)
        self.repo.resolve_review_item('user-teacher-li', 'resp-1001-q2', 'C', '核对')
        self.repo.grade_assessment('user-teacher-li', 'assess-week-1', publish=True)
        learning.migrate(self.conn)
        self.admin = dict(self.conn.execute("select * from users where id='user-admin'").fetchone())
        self.student = dict(self.conn.execute("select * from users where id='stu-1001'").fetchone())
        paper = self.repo.assemble_paper(self.admin['id'], '讲评测试', 'fixture', [{'question_id': 'q-newton-1', 'points': 0}])['paper']['id']
        self.aid = self.repo.create_assessment_from_paper(self.admin['id'], paper, 'class-physics-1', '讲评测试', '', '高二', '')['id']
        self.sid = self.conn.execute('select id from question_version_snapshots where assessment_id=?', (self.aid,)).fetchone()[0]

    def save_answers(self, csv='学生,题号,作答\nstu_1001,1,B\nstu_1002,1,A\nstu_1003,1,\n'):
        p = dict(assessment_id=self.aid, csv=csv, request_key='answers')
        preview = learning.api(self.repo, self.admin, 'answers', p)
        return learning.api(self.repo, self.admin, 'answers', dict(p, confirm=True, preview_token=preview['preview_token']))

    def create(self, **extra):
        return review.create(self.repo, self.admin, dict(assessment_id=self.aid, request_key='review-create', **extra))

    def edit(self, package, **extra):
        item = package['items'][0]
        return review.save_item(self.repo, self.admin, dict(package_id=package['id'], item_id=item['id'],
            revision=item['revision'], content={'solution_steps': '教师补充 $F=ma$'}, included=True, emphasis='detail', reviewed=True, **extra))

    def configure(self):
        return self.repo.save_provider_config(actor_id=self.admin['id'], provider_kind='llm', provider_name='review-test',
            model_name='test-model', secret='unit-test-not-a-real-secret', api_endpoint='https://llm.example.test/v1', enabled=True, daily_call_limit=100)

    def test_generation_is_idempotent_deterministic_and_does_not_alter_answers(self):
        self.save_answers()
        before = [dict(r) for r in self.conn.execute('select * from student_responses')]
        package = self.create()
        self.assertEqual(package['id'], self.create()['id'])
        with self.assertRaises(StateConflict):
            self.create(minutes=20)
        self.assertFalse(package['stale'])
        stats = package['items'][0]['source']['statistics']
        self.assertEqual(['B'],package['items'][0]['source']['correct_options'])
        self.assertEqual((3, 3, 1, 1), (stats['present_count'], stats['confirmed_count'], stats['correct_count'], stats['blank_count']))
        self.assertAlmostEqual(1/3, stats['correct_rate'])
        self.assertEqual(before, [dict(r) for r in self.conn.execute('select * from student_responses')])
        self.assertEqual(exporting.build_zip(self.repo, self.admin, package['id'], self.path), exporting.build_zip(self.repo, self.admin, package['id'], self.path))

    def test_roles_school_scope_and_payload_validation(self):
        with self.assertRaises(PermissionDenied):
            review.create(self.repo, self.student, dict(assessment_id=self.aid, request_key='denied'))
        with self.assertRaises(InvalidRequest): self.create(class_ids=['other-class'])
        with self.assertRaises(InvalidRequest): self.create(class_ids=[])
        with self.assertRaises(InvalidRequest): self.create(minutes=0)
        package = self.create()
        with self.assertRaises(ResourceNotFound): review.get(self.repo, dict(self.admin, school_id='other-school'), package['id'])
        with self.assertRaises(PermissionDenied): review.get(self.repo, self.student, package['id'])
        with self.assertRaises(InvalidRequest):
            review.save_item(self.repo, self.admin, dict(package_id=package['id'], content={'statistics': 'tampered'}))
        seed_other_class(self.conn)
        outsider = dict(self.conn.execute("select * from users where id='user-teacher-wang'").fetchone())
        with self.assertRaises(PermissionDenied): review.get(self.repo, outsider, package['id'])

    def test_edit_conflict_and_new_version_keep_previous_teacher_work(self):
        package = self.create()
        edited = self.edit(package)
        with self.assertRaises(StateConflict): self.edit(package)
        self.assertEqual('teacher', edited['field_sources']['solution_steps'])
        self.save_answers()
        self.assertTrue(review.get(self.repo, self.admin, package['id'])['stale'])
        new = review.create(self.repo, self.admin, dict(assessment_id=self.aid, request_key='new-version'))
        self.assertNotEqual(package['id'], new['id'])
        old = review.get(self.repo, self.admin, package['id'])
        self.assertEqual('教师补充 $F=ma$', old['items'][0]['content']['solution_steps'])
        self.assertEqual(0, old['items'][0]['source']['statistics']['confirmed_count'])
        self.assertEqual(3, new['items'][0]['source']['statistics']['confirmed_count'])

    def test_effective_evidence_scores_not_latest_unused_evidence(self):
        self.save_answers()
        row = self.conn.execute('select * from student_responses where assessment_id=? limit 1', (self.aid,)).fetchone()
        self.conn.execute('''insert into response_evidence(id,response_id,raw_answer,normalized_answer,extraction_method,created_at,
            imported_score,imported_max_score) values('score-effective',?,'B','B','test','2026-01-01',2,5)''', (row['id'],))
        from highschoolphysics.response_workflow import _decision
        _decision(self.conn,self.admin,row['id'],'score-effective',row['outcome'],row['final_answer'],row['outcome'],None,
                  'teacher_correction','score_test','核对',self.sid,row['effective_decision_id'])
        self.conn.execute('''insert into response_evidence(id,response_id,raw_answer,normalized_answer,extraction_method,created_at,
            imported_score,imported_max_score) values('unused',?,'A','A','test','2099-01-01',5,5)''', (row['id'],))
        self.conn.commit()
        self.assertEqual(.4, self.create()['items'][0]['source']['statistics']['score_rate'])

    def test_merged_classes_are_weighted_and_excluded_participants_do_not_count(self):
        self.save_answers()
        seed_other_class(self.conn)
        self.conn.execute("update users set class_id='class-physics-2' where id='stu-1001'")
        self.conn.commit()
        package = self.create()
        source = package['items'][0]['source']
        self.assertEqual(2, len(source['classes']))
        self.assertAlmostEqual(1/3, source['statistics']['correct_rate'])
        self.conn.execute("update assessment_participants set status='absent' where assessment_id=? and student_id='stu-1001'", (self.aid,))
        self.conn.commit()
        new = review.create(self.repo, self.admin, dict(assessment_id=self.aid, request_key='new-population'))
        self.assertEqual((2, 0), (new['items'][0]['source']['statistics']['present_count'], new['items'][0]['source']['statistics']['correct_count']))
        self.assertTrue(review.get(self.repo, self.admin, package['id'])['stale'])

    def test_teacher_tags_and_student_self_reports_keep_sources_and_clear_events(self):
        self.save_answers()
        learning.api(self.repo, self.admin, 'publish', dict(assessment_id=self.aid))
        wrong = self.conn.execute("select * from wrong_questions where assessment_id=? and student_id='stu-1002'", (self.aid,)).fetchone()
        self.conn.execute("insert into error_reason_tags(id,school_id,code,name) values('review-cause','school-demo','review','模型选择不当')")
        self.conn.execute("insert into wrong_question_error_tags(wrong_question_id,error_reason_tag_id,tagged_by,note) values(?,'review-cause',?,'private student identity')", (wrong['id'], self.admin['id']))
        sources = json.dumps([[wrong['id'], wrong['question_id'], self.sid]])
        self.conn.execute('insert into student_wrong_cause_events values(?,?,?,?,?,?,?,?,?)',
            ('stu-1002','school-demo','scope-test','group',sources,1,'["calculation"]','cause-1','2026-01-01'))
        self.conn.commit()
        source = self.create()['items'][0]['source']
        self.assertEqual([{'label': '模型选择不当', 'count': 1, 'source': 'teacher_tag'}], source['error_evidence'])
        self.assertEqual('whole_question', source['student_reports'][0]['scope'])
        self.assertEqual(1, source['student_reports'][0]['count'])
        self.conn.execute('insert into student_wrong_cause_events values(?,?,?,?,?,?,?,?,?)',
            ('stu-1002','school-demo','scope-test','group',sources,2,'[]','cause-2','2026-01-02'))
        self.conn.commit()
        new = review.create(self.repo, self.admin, dict(assessment_id=self.aid, request_key='cleared'))
        self.assertEqual([], new['items'][0]['source']['student_reports'])

    def test_publication_automatically_creates_review_and_isolates_review_failure(self):
        self.save_answers()
        with patch.object(review, 'create', side_effect=RuntimeError('review unavailable')):
            result = learning.api(self.repo, self.admin, 'publish', dict(assessment_id=self.aid))
        self.assertIn('未自动建立',result['message'])
        self.assertEqual('published',self.conn.execute('select grading_status from assessment_sessions where id=?',(self.aid,)).fetchone()[0])
        result = self.create()
        self.assertEqual(1, result['total'])

    def test_successful_publication_creates_review(self):
        self.save_answers()
        result = learning.api(self.repo,self.admin,'publish',dict(assessment_id=self.aid))
        self.assertEqual(1, review.get(self.repo, self.admin, result['review_package_id'])['total'])

    def test_model_receives_no_student_records_and_cannot_return_fact_fields(self):
        self.configure()
        self.save_answers()
        package = self.create()
        source = package['items'][0]['source']
        generated = {key:'可能需要结合本题条件分析' for key in review.AI_FIELDS}
        raw = json.dumps({'choices':[{'message':{'content':json.dumps(generated)}}],'usage':{'prompt_tokens':30,'completion_tokens':40}}).encode()
        class Reply:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,*args): return raw
        with patch.object(worker.request,'urlopen',return_value=Reply()) as mocked:
            self.assertEqual(generated,worker.generate(self.repo,review.provider(self.conn,self.admin['school_id']),source,self.admin,self.path))
        body = json.loads(mocked.call_args.args[0].data)
        text = json.dumps(body,ensure_ascii=False)
        for private in ['stu-1001','student_id','correct_count','option_denominator','unit-test-not-a-real-secret']:
            self.assertNotIn(private,text)
        self.assertIn('不得编造人数',text)
        with self.assertRaises(InvalidRequest): worker.validate_content(dict(generated,correct_rate=.9))

    def test_zip_is_anonymous_offline_and_integrity_checked(self):
        self.save_answers()
        self.conn.execute("insert into exam_assets(id,school_id,question_id,png) values('review-figure','school-demo','q-newton-1',?)", (PNG,))
        self.conn.commit()
        package = self.create()
        with zipfile.ZipFile(io.BytesIO(exporting.build_zip(self.repo, self.admin, package['id'], self.path))) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read('manifest.json'))
            self.assertTrue(manifest['anonymous'])
            self.assertEqual('draft', manifest['review_state'])
            self.assertIn('vendor/katex/katex.min.js', names)
            self.assertIn('assets/', archive.read('预览.html').decode())
            for entry in manifest['files']:
                self.assertEqual(entry['sha256'], hashlib.sha256(archive.read(entry['path'])).hexdigest())
            for name in ['讲评总稿.md','逐题数据.json','manifest.json','统计表.csv']:
                data = archive.read(name).decode('utf-8-sig')
                for private in ['stu-1001', 'stu_1001', '张小明', '李小红', '王小刚', 'private student identity']:
                    self.assertNotIn(private, data)
            for match in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', archive.read('questions/001.md').decode()):
                self.assertIn(match.replace('../', ''), names)
        self.conn.execute("update exam_assets set png=? where id='review-figure'", (b'changed',))
        self.conn.commit()
        with self.assertRaises(QuestionExportError): exporting.build_zip(self.repo, self.admin, package['id'], self.path)

    def test_inclusion_and_display_settings_affect_draft_and_slide_plan(self):
        package = self.create(show_answer=False, show_statistics=False, show_solution=False, show_errors=False)
        with zipfile.ZipFile(io.BytesIO(exporting.build_zip(self.repo, self.admin, package['id'], self.path))) as archive:
            md = archive.read('讲评总稿.md').decode()
            plan = json.loads(archive.read('逐页安排.json'))
            self.assertNotIn('## 参考答案', md)
            self.assertNotIn('## 作答统计', md)
            self.assertNotIn('answer_statistics', str(plan))
            self.assertIn('source', json.loads(archive.read('逐题数据.json'))[0])
        item = package['items'][0]
        review.save_item(self.repo, self.admin, dict(package_id=package['id'], item_id=item['id'], revision=item['revision'],
                         content={}, included=False, reviewed=False, emphasis='brief'))
        with self.assertRaises(QuestionExportError): exporting.build_zip(self.repo, self.admin, package['id'], self.path)

    def test_snapshot_child_uses_frozen_parent_and_analysis_not_live_bank(self):
        doc = sample_question()
        doc['stem_md'] = '公共题干 $T=2\\pi/\\omega$'
        self.conn.execute("insert into question_content_groups(id,school_id,created_by) values('review-group','school-demo',?)", (self.admin['id'],))
        self.conn.execute('''insert into question_content_revisions(id,group_id,revision_no,schema_version,document_json,
            content_sha256,review_state,answer_state,created_by,change_reason) values(?,?,?,?,?,?,?,?,?,?)''',
            ('review-revision','review-group',1,1,json.dumps(doc), 'a'*64,'verified','verified',self.admin['id'],'test'))
        self.conn.execute("insert into snapshot_content_bindings values(?, 'review-revision', 'part_1')", (self.sid,))
        self.conn.execute("update questions set stem='mutated live bank',analysis='wrong live analysis' where id='q-newton-1'")
        self.conn.commit()
        source = self.create()['items'][0]['source']
        self.assertIn('公共题干', source['markdown'])
        self.assertIn('周期为', source['markdown'])
        self.assertNotIn('说明误差来源', source['markdown'])
        self.assertNotIn('mutated', source['markdown'])
        self.assertEqual('由周期定义得到。\n\n将数据代入周期公式。', source['analysis'])

    def test_worker_preserves_teacher_overrides_and_only_retries_failed_item(self):
        self.configure()
        package = self.create()
        self.assertEqual('queued', package['items'][0]['status'])
        edited = self.edit(package)
        generated = {key: '可能需结合本题条件核对' for key in review.AI_FIELDS}
        result = worker.run_once(self.path, generator=lambda *args: generated)
        self.assertEqual('ready', result['status'])
        current = review.get(self.repo, self.admin, package['id'])['items'][0]
        self.assertEqual('教师补充 $F=ma$', current['content']['solution_steps'])
        self.assertEqual('teacher', current['field_sources']['solution_steps'])
        self.assertEqual('ai', current['field_sources']['possible_errors'])
        self.assertFalse(current['reviewed'])  # New AI content still needs a fresh teacher review.
        self.assertIsNone(worker.run_once(self.path, generator=lambda *args: generated))
        with self.assertRaises(StateConflict): review.retry_item(self.repo, self.admin, dict(package_id=package['id'], item_id=current['id']))

    def test_worker_failure_lease_recovery_and_manual_retry(self):
        self.configure()
        package = self.create()
        def fail(*args): raise RuntimeError('upstream secret must never persist')
        worker.run_once(self.path, generator=fail)
        current = review.get(self.repo, self.admin, package['id'])['items'][0]
        self.assertEqual('failed', current['status'])
        self.assertNotIn('upstream', current['error'])
        review.retry_item(self.repo, self.admin, dict(package_id=package['id'], item_id=current['id']))
        self.conn.execute("update exam_review_items set status='running',lease_until='2000-01-01',attempts=3 where id=?", (current['id'],))
        self.conn.commit()
        self.assertIsNone(worker.run_once(self.path))
        self.assertEqual('failed', review.get(self.repo, self.admin, package['id'])['items'][0]['status'])

    def test_unverified_answer_is_not_sent_to_model(self):
        self.configure()
        self.conn.execute("update question_version_snapshots set grading_rule_json='{}' where id=?", (self.sid,))
        self.conn.commit()
        package = self.create()
        self.assertEqual('missing', package['items'][0]['source']['answer_state'])
        self.assertEqual('reference_only', package['items'][0]['status'])
        with self.assertRaises(StateConflict): review.retry_item(self.repo, self.admin, dict(package_id=package['id'], item_id=package['items'][0]['id']))

    def test_http_entry_page_export_origin_and_access_boundaries(self):
        self.save_answers()
        with LivePhysicsServer(self.path, seed=False) as server:
            _, cookie, _ = server.login('teacher_li', 'teacher123')
            origin = 'http://%s:%s' % server.address
            payload = dict(assessment_id=self.aid, request_key='http-create')
            self.assertEqual(403, server.post_json('/api/learning/review-create', payload, cookie)[0])
            status, _, body = server.request('POST','/api/learning/review-create',json.dumps(payload).encode(),
                {'Cookie':cookie, 'Origin':origin, 'Content-Type':'application/json'})
            self.assertEqual(200, status, body)
            pid = json.loads(body)['result']['id']
            status, _, body = server.request('GET','/exam-review?id='+pid,headers={'Cookie':cookie,'X-Forwarded-Prefix':'/physics'})
            self.assertEqual(200, status)
            self.assertIn(b'data-base-path="/physics"', body)
            self.assertIn(b'exam-review.js', body)
            self.assertNotIn(b'<script>alert', body)
            status, headers, body = server.request('GET','/api/exam-review/'+pid+'/export.zip',headers={'Cookie':cookie})
            self.assertEqual(200, status, body[:200])
            self.assertEqual('application/zip', headers['Content-Type'])
            self.assertTrue(zipfile.is_zipfile(io.BytesIO(body)))
            _, student_cookie, _ = server.login('stu_1001','student123')
            for route in ['/exam-review?id='+pid, '/api/exam-review/'+pid, '/api/exam-review/'+pid+'/export.zip']:
                self.assertEqual(403, server.request('GET',route,headers={'Cookie':student_cookie})[0])
            page = learning_views.exams(self.repo,self.admin,self.aid)
            self.assertIn('生成讲评资料草稿', page)
            self.assertIn('选项统计分母 3 人', page)


if __name__ == '__main__':
    unittest.main()
