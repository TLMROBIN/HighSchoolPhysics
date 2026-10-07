import json
import re
import unittest

from highschoolphysics import learning, learning_views
from tests.http_support import seed_other_class
from tests import test_response_workflow as fixtures


class ExamStatisticsTests(unittest.TestCase):
    setUp = fixtures.ResponseWorkflowTests.setUp
    tearDown = fixtures.ResponseWorkflowTests.tearDown

    def make_exam(self):
        seed_other_class(self.c)
        self.c.commit()
        self.repo.confirm_question_tags(self.admin['id'], 'q-newton-1',
            knowledge_node_ids=['kn-pep2019-r1-c04-s03'], ability_tag_ids=[], literacy_tag_ids=[])
        exam = self.repo.create_assessment_from_paper(self.admin['id'], self.paper,
            'class-physics-1', '统计测试', '', '高二', '',
            class_ids=['class-physics-1', 'class-physics-2'])
        aid = exam['id']
        payload = dict(assessment_id=aid, request_key='statistics-fixture', records=[
            dict(student_id='stu-1001', number=1, answer='B'),
            dict(student_id='stu-1002', number=1, answer='A'),
            dict(student_id='stu-2001', number=1, answer=''),
        ])
        preview = learning.api(self.repo, self.admin, 'answers', payload)
        learning.api(self.repo, self.admin, 'answers', dict(payload, confirm=True,
            preview_token=preview['preview_token']))
        return aid

    def result_area(self, aid, classes=None, user=None):
        html = learning_views.exams(self.repo, user or self.admin, aid, class_ids=classes)
        return html.split('<div class="exam-results-layout">', 1)[-1]

    def test_multiclass_filter_and_missing_are_distinct_from_blank(self):
        aid = self.make_exam()
        html = self.result_area(aid)
        self.assertIn('正确 1 / 已确认 3', html)
        self.assertIn('缺失作答', html)
        self.assertIn('空白', html)
        self.assertNotIn('查看证据与判定历史', html)
        self.assertIn('href="#exam-question-1"', html)
        # No category starts open, so a complete student table is not initially visible.
        self.assertFalse(re.search(r'<details class="answer-category"[^>]*\bopen\b', html))
        first = self.result_area(aid, ['class-physics-1'])
        self.assertIn('正确 1 / 已确认 2', first)
        self.assertNotIn('赵同学', first)
        second = self.result_area(aid, ['class-physics-2'])
        self.assertIn('正确 0 / 已确认 1', second)
        self.assertNotIn(self.student['display_name'], second)
        self.assertNotIn('缺失作答', second)
        empty = self.result_area(aid, ['__none__', 'outside-exam'])
        self.assertIn('当前实到 0 人', empty)
        self.assertIn('当前范围没有实到学生', empty)
        self.assertNotIn('赵同学', empty)

    def test_pending_is_excluded_and_same_name_tags_do_not_merge(self):
        aid = self.make_exam()
        tags = [dict(tag_type=kind, tag_id=kind+'-id', name='同名标签')
                for kind in ('knowledge', 'ability', 'literacy')]
        self.c.execute('update question_version_snapshots set tag_snapshot_json=? where assessment_id=?',
                       (json.dumps(tags), aid))
        self.c.execute("update student_responses set outcome='pending' where assessment_id=? and student_id='stu-1002'", (aid,))
        self.c.commit()
        html = self.result_area(aid)
        self.assertIn('正确 1 / 已确认 2 · 待确认 1', html)
        charts = html.split('id="exam-tag-statistics"', 1)[1]
        self.assertEqual(charts.count('class="tag-chart-label">同名标签</text>'), 3)
        self.assertEqual(charts.count('50.0%（1 / 2）'), 6)  # blank and affected in each category
        self.assertEqual(charts.count('0.0%（0 / 1）'), 3)

    def test_choice_combination_and_score_only_categories(self):
        question = dict(grading_rule_json='{"type":"multiple_choice"}')
        category = learning_views._exam_answer_category
        self.assertEqual(category(question, dict(final_answer='b、a', outcome='wrong')), '选 AB')
        self.assertEqual(category(question, dict(final_answer='AB', outcome='wrong')), '选 AB')
        self.assertEqual(category(question, dict(final_answer='', outcome='wrong')), '未导入答案（仅得分）')
        self.assertEqual(category(question, dict(final_answer='', outcome='blank')), '空白')

    def test_student_filter_cannot_reveal_classmates(self):
        aid = self.make_exam()
        self.c.execute("update assessment_sessions set grading_status='published' where id=?", (aid,))
        self.c.commit()
        html = learning_views.exams(self.repo, self.student, aid, class_ids=['class-physics-2'])
        self.assertNotIn('赵同学', html)
        self.assertNotIn('exam-results-sidebar', html)
        self.assertIn(self.student['display_name'], html)

    def test_student_results_default_to_table_with_collapsed_questions(self):
        aid = self.make_exam()
        self.c.execute("update assessment_sessions set grading_status='published' where id=?", (aid,))
        self.c.commit()
        html = learning_views.exams(self.repo, self.student, aid)
        nav = html.split('<nav class="student-exam-nav"', 1)[1].split('</nav>', 1)[0]
        self.assertIn('data-outcome="correct">正确', nav)
        self.assertIn('aria-expanded="false"', nav)
        self.assertIn('aria-controls="student-exam-group-1"', nav)
        self.assertIn('id="student-exam-group-1" hidden', html)
        self.assertFalse(re.search(r'<details class="assessment-question-details"[^>]*\bopen\b', html))
        self.assertNotIn('李华', nav)

    def test_student_results_distinguish_imported_score_pending_and_missing(self):
        aid = self.make_exam()
        self.c.execute("update assessment_sessions set grading_status='published' where id=?", (aid,))
        self.c.execute('update question_version_snapshots set grading_rule_json=? where assessment_id=?', (json.dumps({'type':'fill'}), aid))
        response = self.c.execute("select id from student_responses where assessment_id=? and student_id=?", (aid, self.student['id'])).fetchone()[0]
        self.c.execute("insert into response_evidence(id,response_id,extraction_method,created_at,imported_score,imported_max_score) values('score-nav',?,'manual','2026-10-07',1.5,4)", (response,))
        self.c.execute("update student_responses set outcome='pending' where id=?", (response,))
        self.c.commit()
        html = learning_views.exams(self.repo, self.student, aid)
        nav = html.split('<nav class="student-exam-nav"', 1)[1].split('</nav>', 1)[0]
        self.assertIn('1.5 / 4 分 · 待确认', nav)
        missing = dict(self.c.execute("select * from users where id='stu-1003'").fetchone())
        nav = learning_views.exams(self.repo, missing, aid).split('<nav class="student-exam-nav"', 1)[1].split('</nav>', 1)[0]
        self.assertIn('缺失记录', nav)
        self.assertNotIn('0 / 4', nav)
