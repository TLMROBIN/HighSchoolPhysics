import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from highschoolphysics import learning, learning_views, response_scans
from highschoolphysics.db import connect
from highschoolphysics.errors import InvalidRequest, PermissionDenied
from highschoolphysics.exam_import import get_asset
from highschoolphysics.response_files import read_file
from highschoolphysics.repository import PhysicsRepository
from tests.http_support import seed_other_class
from tests import test_response_workflow as fixtures
from highschoolphysics.providers import ProviderSecretStore


class ExamWorkflowTests(unittest.TestCase):
    setUp = fixtures.ResponseWorkflowTests.setUp
    tearDown = fixtures.ResponseWorkflowTests.tearDown

    def test_whole_grade_and_multiple_classes_include_all_students(self):
        seed_other_class(self.c)
        self.c.commit()
        self.repo.confirm_question_tags(self.admin['id'],'q-newton-1',knowledge_node_ids=['kn-pep2019-r1-c04-s03'],ability_tag_ids=[],literacy_tag_ids=[])
        for selected in ([], ['class-physics-1', 'class-physics-2'], ['class-physics-2']):
            result = learning.api(self.repo, self.teacher, 'assessment', dict(
                title='年级考试', grade='高二', class_ids=selected, paper_id=self.paper))
            aid = result['url'].split('=')[1]
            exam = self.repo.assessment_detail(self.teacher['id'], aid)
            scope = json.loads(exam['scope_json'])
            ids = selected or ['class-physics-1', 'class-physics-2']
            expected = sum(len(self.repo.students_for_class(cid)) for cid in ids)
            actual = self.c.execute("select count(*) from assessment_participants where assessment_id=? and status='present'", (aid,)).fetchone()[0]
            self.assertEqual(actual, expected)
            self.assertEqual(scope['whole_grade'], not selected)
            self.assertIn(aid, [a['id'] for a in self.repo.assessment_overview(self.teacher['id'])])
            html = learning_views.exams(self.repo, self.teacher, aid)
            self.assertNotIn('核对学生范围', html)
            self.assertIn('答题卡扫描件导入', html)
            self.assertNotIn('Agent', html)
        html = learning_views.teacher(self.repo, self.teacher, {'module': ['exams']})
        self.assertIn('value="class-physics-2"', html)
        with self.assertRaises(InvalidRequest):
            learning.api(self.repo, self.teacher, 'assessment', dict(title='错误年级', grade='高三', class_ids=['class-physics-2'], paper_id=self.paper))

    def test_score_only_import_preserves_evidence_and_is_not_blank(self):
        self.c.execute("update questions set bank_type='solution' where id='q-newton-1'")
        self.c.execute("update question_version_snapshots set grading_rule_json=json_set(grading_rule_json,'$.bank_type','solution') where assessment_id=?",(self.a,))
        self.c.commit()
        p = dict(assessment_id=self.a, request_key='score-import',
                 csv='学生姓名,题号,学生答案,得分,满分\nstu_1001,1,,3.5,5\n')
        preview = learning.api(self.repo, self.admin, 'answers', p)
        self.assertEqual(preview['records'][0]['outcome'], 'wrong')
        self.assertTrue(preview['records'][0]['score_only'])
        learning.api(self.repo, self.admin, 'answers', dict(p, confirm=True, preview_token=preview['preview_token']))
        evidence = self.c.execute('select * from response_evidence where batch_id=?', (preview['preview_token'],)).fetchone()
        self.assertEqual((evidence['imported_score'], evidence['imported_max_score']), (3.5, 5))
        self.assertEqual(evidence['raw_answer'], '')
        learning.api(self.repo, self.admin, 'publish', {'assessment_id': self.a})
        self.assertIn('未导入答案（仅得分）', learning_views.exams(self.repo, self.admin, self.a))

    def test_score_only_without_maximum_can_be_saved_and_reviewed(self):
        self.c.execute("update questions set bank_type='experiment' where id='q-newton-1'")
        self.c.execute("update question_version_snapshots set grading_rule_json=json_set(grading_rule_json,'$.bank_type','experiment') where assessment_id=?",(self.a,))
        self.c.commit()
        p = dict(assessment_id=self.a, request_key='score-only', records=[dict(student_id='stu-1001', number=1, score=3)])
        preview = learning.api(self.repo, self.admin, 'answers', p)
        self.assertEqual(preview['records'][0]['category'], 'score_maximum_missing')
        learning.api(self.repo, self.admin, 'answers', dict(p, confirm=True, preview_token=preview['preview_token']))
        row = self.c.execute('select * from student_responses where assessment_id=?', (self.a,)).fetchone()
        learning.api(self.repo, self.admin, 'response-review', dict(response_id=row['id'], answer='', outcome='wrong',
                     reason='按试卷评分标准核对，满分5分', request_key='score-review', expected_decision_id=row['effective_decision_id']))
        learning.api(self.repo, self.admin, 'publish', {'assessment_id': self.a})

    def test_objective_score_only_and_invalid_scores_rejected(self):
        for score in (0, -1, 'nan', True):
            with self.assertRaises(InvalidRequest):
                learning.api(self.repo, self.admin, 'answers', dict(assessment_id=self.a, request_key='invalid',
                    records=[dict(student_id='stu-1001', number=1, score=score)]))

    def test_only_system_scan_results_can_use_vision_auto_grading(self):
        from highschoolphysics.response_workflow import _prepare
        self.c.execute("update question_version_snapshots set grading_rule_json=json_set(grading_rule_json,'$.type','structured') where assessment_id=?", (self.a,))
        self.c.commit()
        p = dict(assessment_id=self.a, request_key='vision-trust', source_type='external',
                 source_name='Agent', source_reason='外部结果核对', records=[dict(student_id='stu-1001',
                 number=1, answer='物理解答', supplied_outcome='correct', extraction_method='ocr_vision', extraction_confidence=.99)])
        external = learning.api(self.repo, self.admin, 'answers', p)
        self.assertEqual(external['records'][0]['outcome'], 'pending')
        self.assertEqual(external['records'][0]['extraction_method'], 'agent_import')
        internal = _prepare(self.repo, self.admin, p, trusted_scan=True)[-1][0]
        self.assertEqual(internal['outcome'], 'correct')
        self.assertEqual(internal['method'], 'vision_grade')

    def test_csv_and_xlsx_preserve_empty_answers(self):
        raw = '学生姓名,题号,学生答案,得分\n张三,1,,3\n'.encode('gb18030')
        self.assertIn('张三,1,,3', read_file(dict(file_name='a.csv', file_data=base64.b64encode(raw).decode())))
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="数据" sheetId="1" r:id="r1"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="inlineStr"><is><t>学生姓名</t></is></c><c r="B1" t="inlineStr"><is><t>题号</t></is></c><c r="C1" t="inlineStr"><is><t>学生答案</t></is></c><c r="D1" t="inlineStr"><is><t>得分</t></is></c></row><row><c r="A2" t="inlineStr"><is><t>张三</t></is></c><c r="B2"><v>1</v></c><c r="D2"><v>3</v></c></row></sheetData></worksheet>')
        self.assertIn('张三,1,,3', read_file(dict(file_name='a.xlsx', file_data=base64.b64encode(archive.getvalue()).decode())))

    def test_scan_worker_preview_confirmation_and_student_media_boundary(self):
        self.repo.save_provider_config(actor_id=self.admin['id'], provider_kind='llm', provider_name='fixture',
              model_name='vision', secret='private-unit-test-key', api_endpoint='https://llm.example.test/v1', enabled=True, daily_call_limit=10)
        snapshot = self.c.execute('select id from question_version_snapshots where assessment_id=?', (self.a,)).fetchone()[0]
        # A synthetic PNG signature is sufficient here because page conversion is independently exercised.
        image = b'\x89PNG\r\n\x1a\nfixture'
        upload = dict(assessment_id=self.a, request_key='scan-test', files=[dict(name='card.png', data=base64.b64encode(image).decode())])
        job = learning.api(self.repo, self.admin, 'scan-upload', upload)
        self.assertEqual(job['job_id'], learning.api(self.repo, self.admin, 'scan-upload', upload)['job_id'])
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'school.sqlite3'
            target = connect(database)
            self.c.backup(target)
            target.close()
            page = Path(directory) / 'page.png'
            page.write_bytes(image)
            vision_result = {'records': [dict(student=self.student['display_name'], snapshot_id=snapshot,
                answer='B', supplied_outcome='correct', confidence=.99)]}
            with patch.object(ProviderSecretStore, 'for_connection', return_value=ProviderSecretStore.for_connection(self.c)), patch.object(response_scans, '_pages', return_value=[page]), patch.object(response_scans, '_vision', return_value=vision_result), patch('highschoolphysics.ocr.run_paddleocr', return_value=[{'text': 'B'}]):
                result = response_scans.run_once(database)
            self.assertEqual(result['status'], 'completed')
            conn = connect(database)
            try:
                repo = PhysicsRepository(conn)
                status = learning.api(repo, self.admin, 'scan-status', dict(assessment_id=self.a, job_id=job['job_id']))
                self.assertEqual(status['records'][0]['outcome'], 'correct')
                learning.api(repo, self.admin, 'answers', dict(assessment_id=self.a, scan_job_id=job['job_id'], confirm=True, preview_token=status['preview_token']))
                learning.api(repo, self.admin, 'publish', {'assessment_id': self.a})
                asset = status['records'][0]['source_asset_id']
                self.assertEqual(get_asset(repo, self.admin['id'], asset), image)
                with self.assertRaises(PermissionDenied):
                    get_asset(repo, self.student['id'], asset)
            finally:
                conn.close()
