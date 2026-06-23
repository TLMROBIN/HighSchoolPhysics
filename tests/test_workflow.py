import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from highschoolphysics.auth import AuthService
from highschoolphysics.db import connect, initialize_database, seed_demo_data
from highschoolphysics.errors import PermissionDenied, StateConflict
from highschoolphysics.exporting import build_wrong_book_html
from highschoolphysics.repository import PhysicsRepository
from tests.http_support import seed_other_class


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "workflow.sqlite3"
        self.conn = connect(self.db_path)
        initialize_database(self.conn)
        seed_demo_data(self.conn)
        self.auth = AuthService(self.conn)
        self.repo = PhysicsRepository(self.conn)
        self.teacher = self.auth.login("teacher_li", "teacher123", user_agent="unit-test")
        self.student = self.auth.login("stu_1001", "student123", user_agent="unit-test")

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _publish_demo_assessment(self):
        self.repo.resolve_review_item(
            actor_id=self.teacher.user["id"],
            response_id="resp-1001-q2",
            corrected_answer="C",
            reason="测试复核",
        )
        return self.repo.grade_assessment(
            actor_id=self.teacher.user["id"],
            assessment_id="assess-week-1",
            publish=True,
        )

    def test_llm_candidate_review_is_required_before_formal_question_tags(self):
        question_id = "q-newton-1"

        candidate = self.repo.generate_llm_candidates(
            actor_id=self.teacher.user["id"],
            question_id=question_id,
        )
        formal_before = self.repo.get_question_tags(question_id)

        self.assertEqual(formal_before, [])
        self.assertEqual(candidate["status"], "pending_review")
        self.assertGreaterEqual(candidate["knowledge_tags"][0]["confidence"], 0.5)

        self.repo.approve_candidate_tags(
            actor_id=self.teacher.user["id"],
            candidate_id=candidate["id"],
            knowledge_node_ids=["kn-pep2019-r1-c04-s03"],
            ability_tag_ids=[
                "ab-context-modeling",
                "ab-equation-building",
            ],
        )
        formal_after = self.repo.get_question_tags(question_id)

        self.assertEqual(
            {(tag["tag_type"], tag["tag_id"]) for tag in formal_after},
            {
                ("knowledge", "kn-pep2019-r1-c04-s03"),
                ("ability", "ab-context-modeling"),
                ("ability", "ab-equation-building"),
            },
        )
        audit_count = self.conn.execute(
            "select count(*) as count from audit_events where action = ?",
            ("question_tag_approved",),
        ).fetchone()["count"]
        self.assertEqual(audit_count, 1)

    def test_candidate_generation_and_confirmation_supports_three_tag_families(self):
        question = self.repo.create_question(
            actor_id=self.teacher.user["id"],
            stem="研究光电效应实验中遏止电压与入射光频率的关系。",
            options={},
            answer={"type": "short_answer", "answer": "频率越高遏止电压越大"},
            analysis="考查证据、模型与能量观念。",
            question_type="short_answer",
            source="校本题库",
            grade="高二",
            chapter="原子结构和波粒二象性",
            difficulty="hard",
        )
        candidate = self.repo.generate_llm_candidates(
            actor_id=self.teacher.user["id"],
            question_id=question["id"],
        )
        self.assertIn("literacy_tags", candidate)
        self.assertGreaterEqual(len(candidate["literacy_tags"]), 1)

        confirmed = self.repo.confirm_question_tags(
            actor_id=self.teacher.user["id"],
            question_id=question["id"],
            candidate_id=candidate["id"],
            knowledge_node_ids=["kn-pep2019-e3-c04-s02"],
            ability_tag_ids=[
                "ab-data-processing",
                "ab-reasoning-argumentation",
            ],
            literacy_tag_ids=[
                "lit-inquiry-evidence",
                "lit-thinking-model",
            ],
        )
        self.assertEqual(
            {(tag["tag_type"], tag["tag_id"]) for tag in confirmed},
            {
                ("knowledge", "kn-pep2019-e3-c04-s02"),
                ("ability", "ab-data-processing"),
                ("ability", "ab-reasoning-argumentation"),
                ("literacy", "lit-inquiry-evidence"),
                ("literacy", "lit-thinking-model"),
            },
        )

    def test_confirm_question_tags_rejects_more_than_three_per_family(self):
        candidate = self.repo.generate_llm_candidates(
            actor_id=self.teacher.user["id"],
            question_id="q-newton-1",
        )
        with self.assertRaisesRegex(ValueError, "At most 3 knowledge tags"):
            self.repo.confirm_question_tags(
                actor_id=self.teacher.user["id"],
                question_id="q-newton-1",
                candidate_id=candidate["id"],
                knowledge_node_ids=[
                    "kn-pep2019-r1-c01",
                    "kn-pep2019-r1-c02",
                    "kn-pep2019-r1-c03",
                    "kn-pep2019-r1-c04",
                ],
                ability_tag_ids=[],
                literacy_tag_ids=[],
            )

    def test_related_questions_and_filters_cover_ability_and_literacy(self):
        candidate = self.repo.generate_llm_candidates(
            actor_id=self.teacher.user["id"],
            question_id="q-newton-1",
        )
        self.repo.confirm_question_tags(
            actor_id=self.teacher.user["id"],
            question_id="q-newton-1",
            candidate_id=candidate["id"],
            knowledge_node_ids=["kn-pep2019-r1-c04-s03"],
            ability_tag_ids=["ab-force-analysis"],
            literacy_tag_ids=["lit-thinking-model"],
        )

        ability_related = self.repo.related_questions_for_ability(
            "ab-force-analysis",
        )
        literacy_related = self.repo.related_questions_for_literacy(
            "lit-thinking-model",
        )
        filtered = self.repo.search_questions(
            actor_id=self.teacher.user["id"],
            filters={
                "tag_type": "literacy",
                "tag_id": "lit-thinking-model",
            },
        )

        self.assertTrue(
            any(item["id"] == "q-newton-1" for item in ability_related)
        )
        self.assertTrue(
            any(item["id"] == "q-newton-1" for item in literacy_related)
        )
        self.assertTrue(any(item["id"] == "q-newton-1" for item in filtered))

    def test_teacher_can_create_edit_and_filter_question_bank_item(self):
        question = self.repo.create_question(
            actor_id=self.teacher.user["id"],
            stem="一辆小车做匀变速直线运动，求加速度。",
            options={},
            answer={"type": "short_answer", "answer": "a=(v-v0)/t"},
            analysis="由速度变化率定义。",
            question_type="short_answer",
            source="校本题库",
            grade="高二",
            chapter="运动的描述",
            difficulty="medium",
            source_school="校内命题",
            source_publisher="高二物理备课组",
            exam_type="daily_practice",
        )
        updated = self.repo.update_question(
            actor_id=self.teacher.user["id"],
            question_id=question["id"],
            stem="一辆小车做匀变速直线运动，已知初末速度和时间，求加速度。",
            options={},
            answer={"type": "short_answer", "answer": "a=(v-v0)/t"},
            analysis="由加速度定义式得到。",
            question_type="short_answer",
            source="校本题库",
            grade="高二",
            chapter="运动的描述",
            difficulty="medium",
            quality_status="reviewed",
        )
        results = self.repo.search_questions(
            actor_id=self.teacher.user["id"],
            filters={
                "grade": "高二",
                "chapter": "运动的描述",
                "quality_status": "reviewed",
            },
        )
        self.assertEqual(updated["version"], question["version"] + 1)
        self.assertTrue(any(item["id"] == question["id"] for item in results))

    def test_parse_task_saves_reviewed_question_with_original_provenance(self):
        task = self.repo.create_parse_task(
            actor_id=self.teacher.user["id"],
            paper_title="Phase 2C 解析样卷",
            document_name="phase2c-sample.txt",
            source_text=(
                "1. 质量为2kg的物体受到6N合外力，2s末速度是多少？\n"
                "A. 1m/s\nB. 2m/s\nC. 6m/s\nD. 12m/s\n答案：C"
            ),
            parser_mode="deterministic_text",
            source_school="校内命题",
            source_publisher="高二物理备课组",
            exam_type="weekly_quiz",
            grade="高二",
            term="2025-2026下",
        )
        parsed = self.repo.run_parse_task(self.teacher.user["id"], task["id"])
        item = parsed["items"][0]
        saved = self.repo.save_parsed_question(
            actor_id=self.teacher.user["id"],
            parsed_item_id=item["id"],
            overrides={"chapter": "运动和力的关系", "difficulty": "medium"},
        )
        self.assertEqual(saved["original_paper_title"], "Phase 2C 解析样卷")
        self.assertEqual(saved["original_question_number"], "1")
        self.assertEqual(saved["source_confidence"], item["confidence"])
        stored_item = self.conn.execute(
            """
            select review_status, saved_question_id
            from parsed_question_items
            where id = ?
            """,
            (item["id"],),
        ).fetchone()
        self.assertEqual(stored_item["review_status"], "saved")
        self.assertEqual(stored_item["saved_question_id"], saved["id"])

    def test_teacher_assembles_paper_and_creates_answer_card_template(self):
        assembled = self.repo.assemble_paper(
            actor_id=self.teacher.user["id"],
            title="Phase 2D 力学小测",
            source="校本组卷",
            question_items=[
                {"question_id": "q-newton-1", "points": 4},
                {"question_id": "q-newton-2", "points": 6},
            ],
        )
        assessment = self.repo.create_assessment_from_paper(
            actor_id=self.teacher.user["id"],
            paper_id=assembled["paper"]["id"],
            class_id="class-physics-1",
            title="Phase 2D 力学小测",
            term="2025-2026下",
            grade="高二",
            scheduled_at="2026-06-12 08:00:00",
        )

        self.assertEqual(assembled["paper"]["status"], "reviewed")
        self.assertEqual(len(assembled["questions"]), 2)
        self.assertEqual(assessment["full_score"], 10)
        self.assertTrue(
            assessment["answer_card_template_id"].startswith("card-")
        )
        snapshots = self.conn.execute(
            """
            select count(*) as count
            from question_version_snapshots
            where assessment_id = ?
            """,
            (assessment["id"],),
        ).fetchone()["count"]
        self.assertEqual(snapshots, 2)

    def test_teacher_imports_ocr_payload_and_reviews_low_confidence_items(self):
        task = self.repo.import_ocr_responses(
            actor_id=self.teacher.user["id"],
            assessment_id="assess-week-1",
            source_name="Phase 2D PaddleOCR 样例",
            recognizer="PaddleOCR",
            recognizer_version="reserved-local-v2",
            items=[
                {
                    "student_id": "stu-1001",
                    "question_id": "q-newton-1",
                    "answer": "A",
                    "confidence": 0.95,
                },
                {
                    "student_id": "stu-1001",
                    "question_id": "q-newton-2",
                    "answer": "D",
                    "confidence": 0.41,
                },
            ],
        )
        self.assertEqual(task["low_confidence_count"], 1)
        blocked = self.repo.grade_assessment(
            actor_id=self.teacher.user["id"],
            assessment_id="assess-week-1",
            publish=False,
        )
        self.assertEqual(blocked["status"], "blocked_for_review")
        self.repo.resolve_review_item(
            actor_id=self.teacher.user["id"],
            response_id=task["responses"][1]["id"],
            corrected_answer="C",
            reason="Phase 2D OCR 复核",
        )
        stored = self.conn.execute(
            """
            select review_status, reviewed_by, reviewed_at
            from student_responses
            where id = ?
            """,
            (task["responses"][1]["id"],),
        ).fetchone()
        self.assertEqual(stored["review_status"], "resolved")
        self.assertEqual(stored["reviewed_by"], self.teacher.user["id"])
        self.assertIsNotNone(stored["reviewed_at"])

    def test_assessment_grading_generates_wrong_questions_and_diagnostics(self):
        assessment_id = "assess-week-1"

        blocked = self.repo.grade_assessment(
            actor_id=self.teacher.user["id"],
            assessment_id=assessment_id,
            publish=False,
        )
        self.assertEqual(blocked["status"], "blocked_for_review")
        self.assertEqual(blocked["review_required"], 1)

        self.repo.resolve_review_item(
            actor_id=self.teacher.user["id"],
            response_id="resp-1001-q2",
            corrected_answer="C",
            reason="教师复核低置信涂卡，确认学生选择 C",
        )
        graded = self.repo.grade_assessment(
            actor_id=self.teacher.user["id"],
            assessment_id=assessment_id,
            publish=True,
        )

        self.assertEqual(graded["status"], "published")
        self.assertEqual(graded["student_count"], 3)
        self.assertGreaterEqual(graded["wrong_question_count"], 2)

        wrongs = self.repo.list_wrong_questions_for_student(self.student.user["id"])
        self.assertGreaterEqual(len(wrongs), 1)
        self.assertIn("assessment_title", wrongs[0])
        self.assertIn("knowledge_tags", wrongs[0])

        diagnostics = self.repo.class_diagnostics(
            actor_id=self.teacher.user["id"],
            assessment_id=assessment_id,
        )
        self.assertIn("knowledge_error_rates", diagnostics)
        self.assertIn("ability_error_rates", diagnostics)
        self.assertIn("high_frequency_wrong_questions", diagnostics)

    def test_student_mastery_mark_and_a4_export_are_persisted(self):
        assessment_id = "assess-week-1"
        self.repo.resolve_review_item(
            actor_id=self.teacher.user["id"],
            response_id="resp-1001-q2",
            corrected_answer="C",
            reason="教师复核低置信涂卡，确认学生选择 C",
        )
        self.repo.grade_assessment(
            actor_id=self.teacher.user["id"],
            assessment_id=assessment_id,
            publish=True,
        )
        wrong = self.repo.list_wrong_questions_for_student(self.student.user["id"])[0]

        mark = self.repo.set_mastery_mark(
            actor_id=self.student.user["id"],
            wrong_question_id=wrong["id"],
            level="基本掌握",
            note="已完成原题重做",
        )
        self.assertEqual(mark["level"], "基本掌握")

        export_html = build_wrong_book_html(
            self.repo,
            actor_id=self.teacher.user["id"],
            assessment_id=assessment_id,
        )
        self.assertIn("@page", export_html)
        self.assertIn("高二(1)班", export_html)
        self.assertIn("page-break-after", export_html)
        self.assertIn("基本掌握", export_html)
        self.assertIn("知识点路径", export_html)
        self.assertIn("学科能力", export_html)
        self.assertNotIn("解析：", export_html)
        self.assertNotIn("正确答案：", export_html)

        student_only = build_wrong_book_html(
            self.repo,
            actor_id=self.teacher.user["id"],
            assessment_id=assessment_id,
            student_id=self.student.user["id"],
        )
        self.assertIn("张明", student_only)
        self.assertNotIn("李华", student_only)

    def test_wrong_book_export_profile_controls_answers_analysis_and_redo_history(self):
        self._publish_demo_assessment()
        wrong = self.repo.list_wrong_questions_for_student(self.student.user["id"])[0]
        tag = self.repo.create_error_reason_tag(
            actor_id=self.teacher.user["id"],
            code="concept-force",
            name="概念混淆",
            description="力与运动关系理解错误",
        )
        self.repo.tag_wrong_question_error(
            actor_id=self.teacher.user["id"],
            wrong_question_id=wrong["id"],
            tag_ids=[tag["id"]],
            note="把合力方向和速度方向混淆",
        )
        attempt = self.repo.submit_redo_attempt(
            actor_id=self.student.user["id"],
            wrong_question_id=wrong["id"],
            answer="C",
        )
        self.repo.review_redo_attempt(
            actor_id=self.teacher.user["id"],
            attempt_id=attempt["id"],
            score=wrong["max_score"],
            feedback="重做正确",
        )

        hidden = build_wrong_book_html(
            self.repo,
            actor_id=self.teacher.user["id"],
            assessment_id="assess-week-1",
            options={
                "include_answers": False,
                "include_analysis": False,
                "include_error_reasons": True,
                "include_redo_history": True,
            },
        )
        shown = build_wrong_book_html(
            self.repo,
            actor_id=self.teacher.user["id"],
            assessment_id="assess-week-1",
            options={
                "include_answers": True,
                "include_analysis": True,
                "include_error_reasons": True,
                "include_redo_history": True,
            },
        )
        terse = build_wrong_book_html(
            self.repo,
            actor_id=self.teacher.user["id"],
            assessment_id="assess-week-1",
            options={
                "include_error_reasons": False,
                "include_redo_history": False,
            },
        )

        self.assertNotIn("正确答案：", hidden)
        self.assertNotIn("解析：", hidden)
        self.assertIn("错因：", hidden)
        self.assertIn("概念混淆", hidden)
        self.assertIn("重做记录", hidden)
        self.assertIn("重做正确", hidden)
        self.assertIn("正确答案：", shown)
        self.assertIn("解析：", shown)
        self.assertNotIn("概念混淆", terse)
        self.assertNotIn("重做记录", terse)

    def test_published_assessment_cannot_be_regraded_and_mastery_survives(self):
        self._publish_demo_assessment()
        wrong = self.repo.list_wrong_questions_for_student(self.student.user["id"])[0]
        mark = self.repo.set_mastery_mark(
            actor_id=self.student.user["id"],
            wrong_question_id=wrong["id"],
            level="基本掌握",
            note="发布后标记",
        )

        with self.assertRaises(StateConflict):
            self.repo.grade_assessment(
                actor_id=self.teacher.user["id"],
                assessment_id="assess-week-1",
                publish=True,
            )

        stored = self.conn.execute(
            "select level from mastery_marks where id = ?",
            (mark["id"],),
        ).fetchone()
        self.assertEqual(stored["level"], "基本掌握")

    def test_teacher_applies_explicit_grading_revision_after_publication(self):
        self._publish_demo_assessment()
        response = self.conn.execute(
            "select * from student_responses where id = 'resp-1001-q1'"
        ).fetchone()

        revision = self.repo.apply_grading_revision(
            actor_id=self.teacher.user["id"],
            assessment_id="assess-week-1",
            reason="答题卡复查发现 Q1 误判",
            items=[
                {
                    "response_id": response["id"],
                    "revised_answer": "B",
                    "revised_score": 0,
                    "max_score": response["max_score"],
                    "reason": "学生实际选择 B",
                }
            ],
        )

        self.assertEqual(revision["status"], "applied")
        revised = self.conn.execute(
            """
            select final_answer, score, overridden_by, override_reason
            from student_responses
            where id = ?
            """,
            (response["id"],),
        ).fetchone()
        self.assertEqual(revised["final_answer"], "B")
        self.assertEqual(revised["score"], 0)
        self.assertEqual(revised["overridden_by"], self.teacher.user["id"])
        self.assertEqual(revised["override_reason"], "学生实际选择 B")
        wrong = self.conn.execute(
            "select * from wrong_questions where response_id = ?",
            (response["id"],),
        ).fetchone()
        self.assertIsNotNone(wrong)

    def test_student_submits_redo_and_teacher_tags_error_reason(self):
        self._publish_demo_assessment()
        wrong = self.repo.list_wrong_questions_for_student(
            self.student.user["id"]
        )[0]
        tag = self.repo.create_error_reason_tag(
            actor_id=self.teacher.user["id"],
            code="concept-force",
            name="概念混淆",
            description="力与运动关系理解错误",
        )
        self.repo.tag_wrong_question_error(
            actor_id=self.teacher.user["id"],
            wrong_question_id=wrong["id"],
            tag_ids=[tag["id"]],
            note="把合力方向和速度方向混淆",
        )
        attempt = self.repo.submit_redo_attempt(
            actor_id=self.student.user["id"],
            wrong_question_id=wrong["id"],
            answer="C",
        )
        reviewed = self.repo.review_redo_attempt(
            actor_id=self.teacher.user["id"],
            attempt_id=attempt["id"],
            score=wrong["max_score"],
            feedback="重做正确",
        )

        self.assertEqual(reviewed["status"], "done")
        updated_wrong = self.repo.list_wrong_questions_for_student(
            self.student.user["id"]
        )[0]
        self.assertEqual(updated_wrong["latest_redo_status"], "done")
        self.assertEqual(updated_wrong["error_reason_tags"][0]["name"], "概念混淆")
        self.assertEqual(updated_wrong["redo_attempts"][0]["feedback"], "重做正确")

    def test_mastery_metrics_update_from_published_assessment_and_reviewed_redo(self):
        self._publish_demo_assessment()

        stu1001_knowledge = {
            row["tag_id"]: row
            for row in self.repo.student_mastery_metrics(
                actor_id="stu-1001",
                tag_type="knowledge",
            )
        }
        self.assertEqual(
            stu1001_knowledge["kn-pep2019-r1-c04-s03"]["assessment_attempts"],
            1,
        )
        self.assertEqual(
            stu1001_knowledge["kn-pep2019-r1-c04-s03"]["assessment_correct"],
            1,
        )
        self.assertEqual(
            stu1001_knowledge["kn-pep2019-r1-c04-s03"]["redo_attempts"],
            0,
        )
        self.assertEqual(
            stu1001_knowledge["kn-pep2019-r1-c04-s03"]["correct_rate"],
            1.0,
        )
        self.assertEqual(
            stu1001_knowledge["kn-pep2019-r1-c04-s03"]["mastery_state"],
            "已掌握",
        )

        stu1002_knowledge = {
            row["tag_id"]: row
            for row in self.repo.student_mastery_metrics(
                actor_id="stu-1002",
                tag_type="knowledge",
            )
        }
        self.assertEqual(
            stu1002_knowledge["kn-pep2019-r1-c02"]["assessment_blank"],
            0,
        )
        self.assertEqual(
            stu1002_knowledge["kn-pep2019-r1-c02"]["assessment_wrong"],
            1,
        )
        self.assertEqual(
            stu1002_knowledge["kn-pep2019-r1-c02"]["mastery_state"],
            "未掌握",
        )

        stu1003_knowledge = {
            row["tag_id"]: row
            for row in self.repo.student_mastery_metrics(
                actor_id="stu-1003",
                tag_type="knowledge",
            )
        }
        self.assertEqual(
            stu1003_knowledge["kn-pep2019-r1-c02"]["assessment_blank"],
            1,
        )
        self.assertEqual(
            stu1003_knowledge["kn-pep2019-r1-c02"]["assessment_wrong"],
            0,
        )
        self.assertEqual(
            stu1003_knowledge["kn-pep2019-r1-c02"]["mastery_state"],
            "未掌握",
        )

        stu1002_abilities = {
            row["tag_id"]
            for row in self.repo.student_mastery_metrics(
                actor_id="stu-1002",
                tag_type="ability",
            )
        }
        self.assertIn("ab-force-analysis", stu1002_abilities)
        self.assertIn("ab-calculation", stu1002_abilities)

        wrong = next(
            item
            for item in self.repo.list_wrong_questions_for_student("stu-1002")
            if item["question_id"] == "q-fill-1"
        )
        attempt = self.repo.submit_redo_attempt(
            actor_id="stu-1002",
            wrong_question_id=wrong["id"],
            answer="9.8",
        )
        self.repo.review_redo_attempt(
            actor_id=self.teacher.user["id"],
            attempt_id=attempt["id"],
            score=wrong["max_score"],
            feedback="重做正确",
        )

        updated = {
            row["tag_id"]: row
            for row in self.repo.student_mastery_metrics(
                actor_id="stu-1002",
                tag_type="knowledge",
            )
        }["kn-pep2019-r1-c02"]
        self.assertEqual(updated["assessment_attempts"], 1)
        self.assertEqual(updated["redo_attempts"], 1)
        self.assertEqual(updated["correct_count"], 1)
        self.assertEqual(updated["eligible_attempts"], 2)
        self.assertEqual(updated["correct_rate"], 0.5)
        self.assertEqual(updated["mastery_state"], "有困难")

    def test_student_navigation_modules_respect_published_visibility(self):
        candidate = self.repo.generate_llm_candidates(
            actor_id=self.teacher.user["id"],
            question_id="q-newton-1",
        )
        self.repo.confirm_question_tags(
            actor_id=self.teacher.user["id"],
            question_id="q-newton-1",
            candidate_id=candidate["id"],
            knowledge_node_ids=["kn-pep2019-r1-c04-s03"],
            ability_tag_ids=["ab-force-analysis"],
            literacy_tag_ids=["lit-thinking-model"],
        )
        self._publish_demo_assessment()
        unpublished = self.repo.create_question(
            actor_id=self.teacher.user["id"],
            stem="未发布教师草稿题不能出现在学生导航。",
            options={"A": "是", "B": "否"},
            answer={"type": "single_choice", "answer": "A"},
            analysis="草稿题。",
            question_type="single_choice",
            source="校本题库",
            grade="高二",
            chapter="牛顿运动定律",
            difficulty="基础",
        )
        draft_candidate = self.repo.generate_llm_candidates(
            actor_id=self.teacher.user["id"],
            question_id=unpublished["id"],
        )
        self.repo.confirm_question_tags(
            actor_id=self.teacher.user["id"],
            question_id=unpublished["id"],
            candidate_id=draft_candidate["id"],
            knowledge_node_ids=["kn-pep2019-r1-c04-s03"],
            ability_tag_ids=["ab-force-analysis"],
            literacy_tag_ids=["lit-thinking-model"],
        )

        dashboard = self.repo.student_dashboard("stu-1001")

        knowledge = next(
            item for item in dashboard["knowledge_navigation"]
            if item["tag_id"] == "kn-pep2019-r1-c04-s03"
        )
        ability = next(
            item for item in dashboard["ability_navigation"]
            if item["tag_id"] == "ab-force-analysis"
        )
        literacy = next(
            item for item in dashboard["literacy_navigation"]
            if item["tag_id"] == "lit-thinking-model"
        )

        self.assertGreaterEqual(len(knowledge["related_questions"]), 1)
        self.assertGreaterEqual(len(knowledge["wrong_questions"]), 1)
        self.assertGreaterEqual(len(knowledge["redo_tasks"]), 1)
        self.assertGreaterEqual(len(ability["related_questions"]), 1)
        self.assertGreaterEqual(len(ability["wrong_questions"]), 1)
        self.assertGreaterEqual(len(literacy["related_questions"]), 1)
        self.assertGreaterEqual(len(literacy["wrong_questions"]), 1)
        self.assertIn("mastery_evidence_text", ability)
        for module in (knowledge, ability, literacy):
            self.assertNotIn(
                unpublished["id"],
                {question["id"] for question in module["related_questions"]},
            )

    def test_student_can_mark_knowledge_mastery_and_see_related_questions(self):
        mark = self.repo.set_knowledge_mastery_mark(
            actor_id=self.student.user["id"],
            student_id=self.student.user["id"],
            knowledge_node_id="kn-pep2019-r1-c04-s03",
            level="基本掌握",
            note="模块图谱中标记",
        )
        self.assertEqual(mark["level"], "基本掌握")

        dashboard = self.repo.student_dashboard(self.student.user["id"])
        node = next(
            item
            for item in dashboard["knowledge_tree"]
            if item["id"] == "kn-pep2019-r1-c04-s03"
        )
        self.assertEqual(node["mastery_level"], "基本掌握")
        self.assertGreaterEqual(len(node["related_questions"]), 1)

    def test_student_cannot_mark_another_students_wrong_question(self):
        self._publish_demo_assessment()
        other_wrong = self.repo.list_wrong_questions_for_student("stu-1002")[0]

        with self.assertRaises(PermissionDenied):
            self.repo.set_mastery_mark(
                actor_id="stu-1001",
                wrong_question_id=other_wrong["id"],
                level="已掌握",
            )

    def test_unpublished_results_are_hidden_from_student(self):
        self.repo.resolve_review_item(
            actor_id=self.teacher.user["id"],
            response_id="resp-1001-q2",
            corrected_answer="C",
            reason="测试复核",
        )
        self.repo.grade_assessment(
            actor_id=self.teacher.user["id"],
            assessment_id="assess-week-1",
            publish=False,
        )

        dashboard = self.repo.student_dashboard("stu-1001")

        self.assertEqual(dashboard["assessments"], [])
        self.assertEqual(dashboard["wrong_questions"], [])

    def test_teacher_diagnostics_include_grade_average_scope(self):
        self.repo.resolve_review_item(
            actor_id=self.teacher.user["id"],
            response_id="resp-1001-q2",
            corrected_answer="C",
            reason="教师复核低置信涂卡，确认学生选择 C",
        )
        self.repo.grade_assessment(
            actor_id=self.teacher.user["id"],
            assessment_id="assess-week-1",
            publish=True,
        )

        diagnostics = self.repo.class_diagnostics(
            actor_id=self.teacher.user["id"],
            assessment_id="assess-week-1",
        )
        self.assertIn("grade_average", diagnostics)
        self.assertEqual(diagnostics["grade_average"]["grade"], "高二")
        self.assertIn("average_score_rate", diagnostics["grade_average"])

    def test_class_mastery_analytics_use_tagged_attempt_denominator_and_drilldown_scope(self):
        row = self.conn.execute(
            """
            select id, tag_snapshot_json
            from question_version_snapshots
            where assessment_id = ? and question_id = ?
            """,
            ("assess-week-1", "q-newton-1"),
        ).fetchone()
        tags = json.loads(row["tag_snapshot_json"])
        tags.append(
            {
                "tag_type": "literacy",
                "tag_id": "lit-thinking-model",
                "name": "模型建构",
            }
        )
        self.conn.execute(
            """
            update question_version_snapshots
            set tag_snapshot_json = ?
            where id = ?
            """,
            (json.dumps(tags, ensure_ascii=False), row["id"]),
        )
        self.conn.commit()
        self._publish_demo_assessment()
        redo_wrong = next(
            item
            for item in self.repo.list_wrong_questions_for_student("stu-1002")
            if item["question_id"] == "q-fill-1"
        )
        redo = self.repo.submit_redo_attempt(
            actor_id="stu-1002",
            wrong_question_id=redo_wrong["id"],
            answer="9.8",
        )
        self.repo.review_redo_attempt(
            actor_id=self.teacher.user["id"],
            attempt_id=redo["id"],
            score=redo_wrong["max_score"],
            feedback="重做正确",
        )

        analytics = self.repo.class_mastery_analytics(
            actor_id=self.teacher.user["id"],
            assessment_id="assess-week-1",
        )

        self.assertEqual(analytics["assessment"]["id"], "assess-week-1")
        self.assertEqual(analytics["class"]["id"], "class-physics-1")
        self.assertEqual(analytics["grade_comparison"]["grade"], "高二")
        self.assertNotIn("students", analytics["grade_comparison"])

        knowledge = next(
            item for item in analytics["knowledge"]
            if item["tag_id"] == "kn-pep2019-r1-c02"
        )
        self.assertEqual(knowledge["eligible_attempts"], 4)
        self.assertEqual(knowledge["correct_count"], 2)
        self.assertEqual(knowledge["wrong_count"], 1)
        self.assertEqual(knowledge["blank_count"], 1)
        self.assertEqual(knowledge["error_rate"], 0.25)
        self.assertEqual(knowledge["blank_rate"], 0.25)
        self.assertEqual(
            knowledge["state_counts"],
            {"未练习": 0, "未掌握": 1, "有困难": 1, "不熟练": 0, "已掌握": 1},
        )
        self.assertEqual(
            {student["student_id"] for student in knowledge["students"]},
            {"stu-1001", "stu-1002", "stu-1003"},
        )

        ability = next(
            item for item in analytics["ability"]
            if item["tag_id"] == "ab-calculation"
        )
        self.assertEqual(ability["eligible_attempts"], 4)
        self.assertEqual(ability["error_rate"], 0.25)

        literacy = next(
            item for item in analytics["literacy"]
            if item["tag_id"] == "lit-thinking-model"
        )
        self.assertIn("students", literacy)

    def test_teacher_mastery_analytics_reject_another_class_and_grade_comparison_is_aggregate_only(self):
        seed_other_class(self.conn)
        self._publish_demo_assessment()

        with self.assertRaises(PermissionDenied):
            self.repo.class_mastery_analytics(
                actor_id=self.teacher.user["id"],
                assessment_id="assess-week-2",
            )

        analytics = self.repo.class_mastery_analytics(
            actor_id=self.teacher.user["id"],
            assessment_id="assess-week-1",
        )
        self.assertNotIn("student_breakdowns", analytics["grade_comparison"])
        for section in ("knowledge", "ability", "literacy"):
            for item in analytics["grade_comparison"][section]:
                self.assertNotIn("students", item)

    def test_admin_mastery_analytics_are_grade_level_and_aggregate_only(self):
        self._publish_demo_assessment()
        admin = self.auth.login("admin", "admin123", user_agent="unit-test").user

        analytics = self.repo.admin_mastery_analytics(admin["id"])

        grade = next(item for item in analytics["grades"] if item["grade"] == "高二")
        self.assertGreaterEqual(grade["student_count"], 1)
        self.assertIn("knowledge", grade)
        self.assertIn("ability", grade)
        self.assertIn("literacy", grade)
        for section in ("knowledge", "ability", "literacy"):
            for item in grade[section]:
                self.assertNotIn("students", item)
        self.assertGreaterEqual(len(analytics["trends"]), 1)
        self.assertIn("average_score_rate", analytics["trends"][0])

    def test_teacher_dashboard_only_contains_assigned_class(self):
        seed_other_class(self.conn)

        dashboard = self.repo.teacher_dashboard(self.teacher.user["id"])

        self.assertEqual(
            {item["class_id"] for item in dashboard["assessments"]},
            {"class-physics-1"},
        )
        self.assertNotIn(
            "stu-2001",
            {item["id"] for item in dashboard["students"]},
        )

    def test_teacher_cannot_grade_another_class(self):
        seed_other_class(self.conn)

        with self.assertRaises(PermissionDenied):
            self.repo.grade_assessment(
                self.teacher.user["id"],
                "assess-week-2",
                publish=True,
            )

    def test_teacher_cannot_review_another_class_response(self):
        seed_other_class(self.conn)

        with self.assertRaises(PermissionDenied):
            self.repo.resolve_review_item(
                self.teacher.user["id"],
                "resp-2001-q1",
                "B",
                "越权尝试",
            )

    def test_teacher_cannot_view_another_class_diagnostics(self):
        seed_other_class(self.conn)

        with self.assertRaises(PermissionDenied):
            self.repo.class_diagnostics(
                self.teacher.user["id"],
                "assess-week-2",
            )

    def test_admin_can_manage_ontology_nodes_edges_abilities_and_release_version(self):
        admin = self.auth.login("admin", "admin123", user_agent="unit-test").user

        created_node = self.repo.create_knowledge_node(
            actor_id=admin["id"],
            stable_code="M.N.3",
            name="超重与失重",
            parent_id="kn-pep2019-r1-c04",
            aliases="超重,失重",
            source="教师校本",
            change_note="加入课堂高频易错点",
        )
        self.assertEqual(created_node["level"], 3)
        self.assertEqual(created_node["enabled"], 1)

        renamed_node = self.repo.update_knowledge_node(
            actor_id=admin["id"],
            node_id=created_node["id"],
            name="超重、失重与视重",
            aliases="超重,失重,视重",
            source="备课组审定",
            change_note="补充视重表述",
        )
        self.assertEqual(renamed_node["version"], 2)
        self.assertEqual(renamed_node["name"], "超重、失重与视重")

        edge = self.repo.create_knowledge_edge(
            actor_id=admin["id"],
            source_node_id="kn-pep2019-r1-c04-s03",
            target_node_id=created_node["id"],
            relation_type="迁移",
            bidirectional=True,
            rationale="牛顿第二定律可迁移解释视重变化",
        )
        self.assertEqual(edge["relation_type"], "迁移")
        self.assertEqual(edge["bidirectional"], 1)

        ability = self.repo.create_ability_tag(
            actor_id=admin["id"],
            stable_code="A.GRAPH",
            name="图像转化",
            description="把物理过程转换为图像并读取斜率、面积或截距",
            source="课标/高考评价体系",
        )
        self.assertEqual(ability["enabled"], 1)

        disabled_node = self.repo.set_knowledge_node_enabled(
            actor_id=admin["id"],
            node_id=created_node["id"],
            enabled=False,
            change_note="等待备课组复核后再启用",
        )
        disabled_ability = self.repo.set_ability_tag_enabled(
            actor_id=admin["id"],
            ability_tag_id=ability["id"],
            enabled=False,
            change_note="暂不纳入正式标签",
        )
        self.assertEqual(disabled_node["enabled"], 0)
        self.assertEqual(disabled_ability["enabled"], 0)

        draft = self.repo.create_ontology_draft(
            actor_id=admin["id"],
            version_label="2026校本物理知识图谱v2",
            source_summary="v1 基础上加入备课组 Phase 2A 修订",
        )
        self.assertEqual(draft["status"], "draft")
        review = self.repo.submit_ontology_for_review(admin["id"], draft["id"])
        self.assertEqual(review["status"], "review")
        active = self.repo.publish_ontology_version(admin["id"], draft["id"])
        self.assertEqual(active["status"], "active")

        previous = self.conn.execute(
            "select status from knowledge_ontology_versions where id = ?",
            ("onto-pep2019-v1",),
        ).fetchone()
        self.assertEqual(previous["status"], "archived")

        dashboard = self.repo.admin_dashboard()
        dashboard_node = next(item for item in dashboard["knowledge_nodes"] if item["id"] == created_node["id"])
        dashboard_ability = next(item for item in dashboard["ability_tags"] if item["id"] == ability["id"])
        self.assertEqual(dashboard_node["enabled"], 0)
        self.assertEqual(dashboard_ability["enabled"], 0)
        self.assertEqual(dashboard["active_ontology_version"]["id"], draft["id"])
        self.assertEqual(
            {
                row["ontology_version_id"]
                for row in self.conn.execute(
                    "select ontology_version_id from literacy_tags"
                ).fetchall()
            },
            {draft["id"]},
        )

        actions = {
            row["action"]
            for row in self.conn.execute("select action from audit_events").fetchall()
        }
        self.assertTrue(
            {
                "knowledge_node_created",
                "knowledge_node_updated",
                "knowledge_node_disabled",
                "knowledge_edge_created",
                "ability_tag_created",
                "ability_tag_disabled",
                "ontology_version_drafted",
                "ontology_version_submitted",
                "ontology_version_published",
            }.issubset(actions)
        )

    def test_admin_can_manage_literacy_and_disabled_items_stay_admin_visible(self):
        admin = self.auth.login(
            "admin",
            "admin123",
            user_agent="unit-test",
        ).user

        dimension = self.repo.create_literacy_tag(
            actor_id=admin["id"],
            stable_code="L.CUSTOM",
            name="校本素养",
            description="校本一级素养维度",
            source="备课组",
            change_note="新增校本维度",
        )
        element = self.repo.create_literacy_tag(
            actor_id=admin["id"],
            stable_code="L.CUSTOM.CHECK",
            name="结果检验",
            parent_id=dimension["id"],
            description="检验结论的量纲、边界和合理性",
            source="备课组",
            change_note="新增校本要素",
        )
        updated = self.repo.update_literacy_tag(
            actor_id=admin["id"],
            literacy_id=element["id"],
            name="结果检验与反思",
            description="检验结论并反思模型适用范围",
            source="备课组审定",
            change_note="补充反思要求",
        )
        disabled = self.repo.set_literacy_tag_enabled(
            actor_id=admin["id"],
            literacy_id=element["id"],
            enabled=False,
            change_note="等待教研组复核",
        )

        self.assertEqual(dimension["level"], 1)
        self.assertEqual(element["level"], 2)
        self.assertEqual(updated["version"], 2)
        self.assertEqual(disabled["enabled"], 0)
        self.assertNotIn(
            element["id"],
            {item["id"] for item in self.repo.literacy_tags()},
        )
        admin_row = next(
            item
            for item in self.repo.all_literacy_tags()
            if item["id"] == element["id"]
        )
        self.assertEqual(admin_row["parent_name"], "校本素养")
        self.assertEqual(admin_row["change_note"], "等待教研组复核")
        self.assertIn(
            element["id"],
            {
                item["id"]
                for item in self.repo.admin_dashboard()["literacy_tags"]
            },
        )
        actions = {
            row["action"]
            for row in self.conn.execute(
                """
                select action from audit_events
                where resource_type = 'literacy_tag'
                """
            ).fetchall()
        }
        self.assertTrue(
            {
                "literacy_tag_created",
                "literacy_tag_updated",
                "literacy_tag_disabled",
            }.issubset(actions)
        )

    def test_taxonomy_summary_and_install_require_admin(self):
        admin = self.auth.login(
            "admin",
            "admin123",
            user_agent="unit-test",
        ).user
        summary = self.repo.taxonomy_summary()

        self.assertEqual(summary["version"], "pep-2019-physics-v1")
        self.assertTrue(summary["installed"])
        self.assertEqual(
            summary["knowledge"],
            {"total": 158, "active": 158},
        )
        self.assertEqual(
            summary["abilities"],
            {"total": 15, "active": 15},
        )
        self.assertEqual(
            summary["literacy"],
            {"total": 18, "active": 18},
        )
        self.assertEqual(len(summary["sources"]), 9)

        with self.assertRaises(PermissionDenied):
            self.repo.install_default_taxonomy(
                actor_id=self.teacher.user["id"],
            )

        result = self.repo.install_default_taxonomy(
            actor_id=admin["id"],
            publish=True,
        )
        self.assertEqual(result["knowledge"]["created"], 0)
        self.assertEqual(result["abilities"]["created"], 0)
        self.assertEqual(result["literacy"]["created"], 0)

    def test_disabled_tags_cannot_be_approved_for_new_question_tags(self):
        admin = self.auth.login(
            "admin",
            "admin123",
            user_agent="unit-test",
        ).user
        candidate = self.repo.generate_llm_candidates(
            actor_id=self.teacher.user["id"],
            question_id="q-newton-1",
        )
        self.repo.set_ability_tag_enabled(
            actor_id=admin["id"],
            ability_tag_id="ab-equation-building",
            enabled=False,
            change_note="暂缓用于新题",
        )

        self.assertNotIn(
            "ab-equation-building",
            {item["id"] for item in self.repo.ability_tags()},
        )
        with self.assertRaisesRegex(ValueError, "not active"):
            self.repo.approve_candidate_tags(
                actor_id=self.teacher.user["id"],
                candidate_id=candidate["id"],
                knowledge_node_ids=["kn-pep2019-r1-c04-s03"],
                ability_tag_ids=["ab-equation-building"],
            )

    def test_disabled_entities_leave_operational_tags_and_edges(self):
        admin = self.auth.login(
            "admin",
            "admin123",
            user_agent="unit-test",
        ).user
        self.repo.set_knowledge_node_enabled(
            actor_id=admin["id"],
            node_id="kn-pep2019-r1-c04-s03",
            enabled=False,
            change_note="暂缓用于新业务",
        )
        self.repo.set_ability_tag_enabled(
            actor_id=admin["id"],
            ability_tag_id="ab-equation-building",
            enabled=False,
            change_note="暂缓用于新业务",
        )

        current_tags = {
            (item["tag_type"], item["tag_id"])
            for item in self.repo.get_question_tags("q-newton-2")
        }
        operational_edges = {
            item["id"] for item in self.repo.knowledge_edges()
        }
        admin_edges = {
            item["id"] for item in self.repo.all_knowledge_edges()
        }

        self.assertNotIn(
            ("knowledge", "kn-pep2019-r1-c04-s03"),
            current_tags,
        )
        self.assertNotIn(
            ("ability", "ab-equation-building"),
            current_tags,
        )
        self.assertNotIn("edge-kin-newton", operational_edges)
        self.assertNotIn("edge-newton-work", operational_edges)
        self.assertIn("edge-kin-newton", admin_edges)
        snapshot = self.conn.execute(
            """
            select tag_snapshot_json from question_version_snapshots
            where id = 'snap-q2'
            """
        ).fetchone()["tag_snapshot_json"]
        self.assertIn("kn-pep2019-r1-c04-s03", snapshot)
        self.assertIn("ab-equation-building", snapshot)

    def test_backup_export_contains_core_assets(self):
        backup = self.repo.export_backup(actor_id="user-admin")

        self.assertIn("questions", backup)
        self.assertIn("knowledge_nodes", backup)
        self.assertIn("literacy_tags", backup)
        self.assertIn("taxonomy_sources", backup)
        self.assertIn("assessment_sessions", backup)
        self.assertIn("student_responses", backup)
        self.assertIn("audit_events", backup)

    def test_backup_restore_into_fresh_database_preserves_core_history(self):
        self._publish_demo_assessment()
        backup = self.repo.export_backup(actor_id="user-admin")

        restored_conn = connect(Path(self.tmpdir.name) / "restored.sqlite3")
        initialize_database(restored_conn)
        restored_repo = PhysicsRepository(restored_conn)
        summary = restored_repo.restore_backup(backup)
        checks = restored_repo.consistency_check()

        self.assertGreater(summary["restored_rows"], 0)
        self.assertEqual(checks["status"], "ok")
        self.assertEqual(checks["issues"], [])
        self.assertIsNotNone(
            restored_conn.execute(
                "select 1 from assessment_sessions where id = ?",
                ("assess-week-1",),
            ).fetchone()
        )
        self.assertIsNotNone(
            restored_conn.execute(
                "select 1 from student_mastery_metrics where student_id = ?",
                ("stu-1001",),
            ).fetchone()
        )
        restored_conn.close()

    def test_admin_records_and_reads_runtime_capability_checks(self):
        result = self.repo.record_runtime_capability_checks(
            actor_id="user-admin",
            checks=[
                {
                    "capability_id": "markitdown",
                    "status": "missing_dependency",
                    "label": "MarkItDown",
                    "detail": "Python package markitdown is not importable",
                    "version": "",
                }
            ],
        )
        self.assertEqual(result[0]["capability_id"], "markitdown")
        self.assertEqual(result[0]["status"], "missing_dependency")

        dashboard = self.repo.production_readiness_dashboard("user-admin")
        check = next(
            item
            for item in dashboard["runtime_checks"]
            if item["capability_id"] == "markitdown"
        )
        self.assertEqual(check["status"], "missing_dependency")
        self.assertIn("markitdown", check["detail"])

    def test_runtime_capability_checks_require_admin(self):
        with self.assertRaises(PermissionDenied):
            self.repo.record_runtime_capability_checks(
                actor_id="user-teacher-li",
                checks=[
                    {
                        "capability_id": "paddleocr",
                        "status": "ready",
                        "label": "PaddleOCR",
                        "detail": "ok",
                        "version": "3.0.0",
                    }
                ],
            )

    def test_runtime_readiness_reports_configured_mineru_api_provider(self):
        self.repo.save_provider_config(
            actor_id="user-admin",
            provider_kind="mineru_api",
            provider_name="MinerU API",
            model_name="pipeline",
            secret="mineru-runtime-secret",
            api_endpoint="https://mineru.example.test/parse",
            enabled=True,
        )

        dashboard = self.repo.production_readiness_dashboard("user-admin")
        mineru_api = next(
            item
            for item in dashboard["runtime_checks"]
            if item["capability_id"] == "mineru-api"
        )

        self.assertEqual(mineru_api["status"], "ready")
        self.assertIn("configured", mineru_api["detail"])
        self.assertNotIn("mineru-runtime-secret", json.dumps(mineru_api))

        recorded = self.repo.record_runtime_capability_checks("user-admin")
        recorded_mineru = next(
            item
            for item in recorded
            if item["capability_id"] == "mineru-api"
        )
        self.assertEqual(recorded_mineru["status"], "ready")

    def test_admin_saves_provider_config_encrypted_and_masks_secret(self):
        config = self.repo.save_provider_config(
            actor_id="user-admin",
            provider_kind="llm",
            provider_name="OpenAI Compatible",
            model_name="gpt-4.1-mini",
            secret="sk-live-secret",
            api_endpoint="https://api.example.test/v1",
            enabled=True,
            daily_call_limit=20,
            monthly_budget_cents=500,
            per_call_max_cents=50,
            input_cost_per_1k_cents=2,
            output_cost_per_1k_cents=8,
        )

        self.assertEqual(config["provider_kind"], "llm")
        self.assertIn("••••", config["secret_masked"])
        self.assertNotIn("sk-live-secret", json.dumps(config, ensure_ascii=False))
        row = self.conn.execute(
            "select * from provider_configs where id = ?",
            (config["id"],),
        ).fetchone()
        self.assertNotIn("sk-live-secret", row["secret_ciphertext"])

        tested = self.repo.test_provider_connection(
            actor_id="user-admin",
            provider_config_id=config["id"],
        )
        self.assertEqual(tested["last_test_status"], "ready")
        self.assertNotIn("sk-live-secret", json.dumps(tested, ensure_ascii=False))

    def test_provider_budget_blocks_daily_and_monthly_limits(self):
        config = self.repo.save_provider_config(
            actor_id="user-admin",
            provider_kind="mineru_api",
            provider_name="MinerU API",
            model_name="pipeline",
            secret="mineru-token",
            api_endpoint="https://mineru.example.test",
            enabled=True,
            daily_call_limit=1,
            monthly_budget_cents=5,
            input_cost_per_1k_cents=2,
            output_cost_per_1k_cents=8,
        )
        self.repo.record_provider_usage(
            actor_id="user-admin",
            provider_config_id=config["id"],
            request_type="parse_pdf",
            prompt_version="mineru-pipeline-v1",
            input_units=500,
            output_units=250,
            page_count=2,
            outcome="success",
        )

        self.assertEqual(
            self.repo.provider_budget_status(
                actor_id="user-admin",
                provider_config_id=config["id"],
                input_units=10,
                output_units=10,
            ),
            {
                "allowed": False,
                "reason": "daily_call_limit_exceeded",
                "estimated_cost_cents": 0.1,
                "current_daily_calls": 1,
                "current_monthly_cost_cents": 3.0,
            },
        )

        monthly = self.repo.save_provider_config(
            actor_id="user-admin",
            provider_kind="llm",
            provider_name="Budgeted LLM",
            model_name="budget-model",
            secret="budget-secret",
            enabled=True,
            daily_call_limit=10,
            monthly_budget_cents=5,
            input_cost_per_1k_cents=2,
            output_cost_per_1k_cents=8,
        )
        self.repo.record_provider_usage(
            actor_id="user-admin",
            provider_config_id=monthly["id"],
            request_type="tagging",
            prompt_version="tag-v1",
            input_units=500,
            output_units=500,
            outcome="success",
        )
        self.assertEqual(
            self.repo.provider_budget_status(
                actor_id="user-admin",
                provider_config_id=monthly["id"],
                input_units=0,
                output_units=1,
            )["reason"],
            "monthly_budget_exceeded",
        )

    def test_provider_operations_require_admin(self):
        with self.assertRaises(PermissionDenied):
            self.repo.save_provider_config(
                actor_id="user-teacher-li",
                provider_kind="llm",
                provider_name="Teacher Key",
                model_name="model",
                secret="sk-teacher",
            )

    def test_mineru_api_parse_task_uses_encrypted_provider_credentials_and_records_usage(self):
        self.repo.save_provider_config(
            actor_id="user-admin",
            provider_kind="mineru_api",
            provider_name="MinerU API",
            model_name="pipeline",
            secret="mineru-secret",
            api_endpoint="https://mineru.example.test/parse",
            enabled=True,
            daily_call_limit=10,
            monthly_budget_cents=100,
            input_cost_per_1k_cents=1,
            output_cost_per_1k_cents=2,
        )
        task = self.repo.create_parse_task(
            actor_id=self.teacher.user["id"],
            paper_title="MinerU API 原卷",
            document_name="mineru.pdf",
            source_text="1. API 解析题干\n答案：A",
            parser_mode="mineru_api",
        )
        captured = {}

        def fake_run_parser(parser_mode, source_text, parser_version, config, fallback_policy):
            captured.update(config)
            return {
                "parser_name": "mineru_api",
                "parser_version": "mineru-api-test",
                "items": [
                    {
                        "item_index": 1,
                        "question_number": "1",
                        "stem": "API 解析题干",
                        "answer": {"type": "short_answer", "answer": "A"},
                        "confidence": 0.9,
                    }
                ],
            }

        with patch("highschoolphysics.repository.run_parser", side_effect=fake_run_parser):
            result = self.repo.run_parse_task(
                actor_id=self.teacher.user["id"],
                task_id=task["id"],
            )

        self.assertEqual(result["status"], "parsed")
        self.assertEqual(captured["api_endpoint"], "https://mineru.example.test/parse")
        self.assertEqual(captured["api_token"], "mineru-secret")
        usage = self.conn.execute(
            """
            select *
            from provider_usage_events
            where request_type = 'document_parse'
              and provider_kind = 'mineru_api'
            """
        ).fetchone()
        self.assertIsNotNone(usage)
        self.assertEqual(usage["outcome"], "success")
        self.assertNotIn("mineru-secret", usage["detail_json"])

    def test_teacher_imports_paddleocr_scan_payload_into_review_flow(self):
        batch = self.repo.import_paddleocr_scan(
            actor_id=self.teacher.user["id"],
            assessment_id="assess-week-1",
            source_name="PaddleOCR 本地扫描",
            image_paths=["scan-1.png"],
            ocr_runner=lambda paths: {
                "scan-1.png": [
                    {
                        "student_id": "stu-1001",
                        "question_id": "q-newton-1",
                        "text": "C",
                        "confidence": 0.91,
                        "bbox": [10, 20, 80, 44],
                    },
                    {
                        "student_id": "stu-1001",
                        "question_id": "q-newton-2",
                        "text": "B",
                        "confidence": 0.42,
                        "bbox": [10, 60, 80, 90],
                    },
                ]
            },
        )

        self.assertEqual(batch["recognizer"], "PaddleOCR")
        self.assertEqual(batch["low_confidence_count"], 1)
        response = self.conn.execute(
            """
            select *
            from student_responses
            where assessment_id = 'assess-week-1'
              and student_id = 'stu-1001'
              and question_id = 'q-newton-2'
            """
        ).fetchone()
        self.assertEqual(response["review_status"], "required")
        payload = json.loads(response["ocr_payload_json"])
        self.assertEqual(payload["bbox"], [10, 60, 80, 90])
        self.assertEqual(payload["raw_paddleocr"]["text"], "B")

    def test_teacher_generates_wrong_book_pdf_artifact(self):
        self._publish_demo_assessment()
        output_dir = Path(self.tmpdir.name) / "exports"

        task = self.repo.generate_wrong_book_pdf(
            actor_id=self.teacher.user["id"],
            assessment_id="assess-week-1",
            output_dir=output_dir,
            engine=lambda html, options: {
                "pdf_bytes": b"%PDF-1.4\nwrong-book\n",
                "engine_version": "fake-pdf-v1",
            },
        )

        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["content_type"], "application/pdf")
        self.assertTrue(Path(task["output_path"]).exists())
        self.assertEqual(Path(task["output_path"]).read_bytes(), b"%PDF-1.4\nwrong-book\n")
        generated = self.conn.execute(
            "select * from generated_export_files where export_task_id = ?",
            (task["id"],),
        ).fetchone()
        self.assertEqual(generated["byte_size"], len(b"%PDF-1.4\nwrong-book\n"))
        self.assertEqual(generated["engine_version"], "fake-pdf-v1")

    def test_oidc_sso_config_redirect_and_existing_user_binding(self):
        config = self.repo.save_oidc_provider_config(
            actor_id="user-admin",
            provider_name="School OIDC",
            issuer="https://idp.example.test",
            client_id="physics-client",
            client_secret="oidc-secret",
            authorization_endpoint="https://idp.example.test/authorize",
            token_endpoint="https://idp.example.test/token",
            userinfo_endpoint="https://idp.example.test/userinfo",
            enabled=True,
            binding_policy="existing_user_only",
        )
        self.assertNotIn("oidc-secret", json.dumps(config, ensure_ascii=False))
        row = self.conn.execute(
            "select * from auth_provider_configs where id = ?",
            (config["id"],),
        ).fetchone()
        self.assertNotIn("oidc-secret", row["secret_ciphertext"])

        login = self.repo.start_sso_login(
            provider_config_id=config["id"],
            redirect_uri="https://school.example.test/sso/callback",
        )
        self.assertIn("state=%s" % login["state"], login["authorization_url"])
        self.assertIn("code_challenge=", login["authorization_url"])

        result = self.repo.complete_sso_callback(
            state=login["state"],
            claims={
                "iss": "https://idp.example.test",
                "sub": "teacher-001",
                "preferred_username": "teacher_li",
                "email": "teacher@example.test",
                "name": "李老师",
            },
        )
        self.assertEqual(result["user"]["id"], "user-teacher-li")
        binding = self.conn.execute(
            """
            select *
            from identity_accounts
            where provider = 'oidc'
              and issuer = 'https://idp.example.test'
              and subject = 'teacher-001'
            """
        ).fetchone()
        self.assertEqual(binding["user_id"], "user-teacher-li")
        with self.assertRaises(StateConflict):
            self.repo.complete_sso_callback(
                state=login["state"],
                claims={
                    "iss": "https://idp.example.test",
                    "sub": "teacher-001",
                    "preferred_username": "teacher_li",
                },
            )


if __name__ == "__main__":
    unittest.main()
