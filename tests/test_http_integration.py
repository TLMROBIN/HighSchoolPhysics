import http.client
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch

from highschoolphysics.db import connect
from highschoolphysics.auth import AuthService
from highschoolphysics.repository import PhysicsRepository
from highschoolphysics.security import hash_password
from tests.http_support import LivePhysicsServer, seed_other_class


class HttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.server_context = LivePhysicsServer(
            Path(self.tmpdir.name) / "http.sqlite3"
        )
        self.server = self.server_context.__enter__()

    def tearDown(self):
        self.server_context.__exit__(None, None, None)
        self.tmpdir.cleanup()

    def _request_or_fail(self, *args, **kwargs):
        try:
            return self.server.request(*args, **kwargs)
        except (http.client.RemoteDisconnected, ConnectionResetError) as error:
            self.fail("server closed the connection without a response: %s" % error)

    def _seed_other_class(self):
        conn = connect(self.server.db_path)
        try:
            seed_other_class(conn)
        finally:
            conn.close()

    def _import_temp_student(self):
        conn = connect(self.server.db_path)
        try:
            return PhysicsRepository(conn).import_student(
                actor_id="user-admin",
                username="stu_temp",
                display_name="临时学生",
                student_no="1999",
                class_id="class-physics-1",
                temp_password_hash=hash_password("Temp123456"),
            )
        finally:
            conn.close()

    def _publish_demo_assessment(self):
        conn = connect(self.server.db_path)
        try:
            repo = PhysicsRepository(conn)
            teacher = AuthService(conn).login(
                "teacher_li",
                "teacher123",
                "http-test-setup",
            ).user
            repo.resolve_review_item(
                teacher["id"],
                "resp-1001-q2",
                "C",
                "测试复核",
            )
            repo.grade_assessment(
                teacher["id"],
                "assess-week-1",
                publish=True,
            )
        finally:
            conn.close()

    def test_missing_required_field_returns_structured_400(self):
        _, cookie, _ = self.server.login("admin", "admin123")
        try:
            status, _, payload = self.server.post_json(
                "/api/admin/knowledge-node",
                {"name": "缺少编码"},
                cookie,
            )
        except (http.client.RemoteDisconnected, ConnectionResetError) as error:
            self.fail("server closed the connection without a response: %s" % error)

        self.assertEqual(status, 400)
        self.assertEqual(
            json.loads(payload),
            {
                "error": "invalid_request",
                "message": "Missing required field: stable_code",
            },
        )

    def test_malformed_json_returns_structured_400(self):
        _, cookie, _ = self.server.login("admin", "admin123")
        status, _, payload = self._request_or_fail(
            "POST",
            "/api/admin/knowledge-node",
            b"{",
            {"Content-Type": "application/json", "Cookie": cookie},
        )

        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"], "invalid_json")

    def test_http_login_hides_demo_credentials_when_disabled(self):
        with LivePhysicsServer(
            Path(self.tmpdir.name) / "non-demo-login.sqlite3",
            demo_mode=False,
            seed=True,
        ) as server:
            status, _, payload = server.request("GET", "/login")

        self.assertEqual(status, 200)
        self.assertNotIn("teacher123", payload.decode("utf-8"))

    def test_http_login_shows_demo_credentials_when_enabled(self):
        status, _, payload = self.server.request("GET", "/login")

        self.assertEqual(status, 200)
        self.assertIn("teacher_li / teacher123", payload.decode("utf-8"))

    def test_unknown_candidate_returns_structured_404(self):
        _, cookie, _ = self.server.login("teacher_li", "teacher123")
        try:
            status, _, payload = self.server.post_json(
                "/api/teacher/approve-candidate",
                {"candidate_id": "missing-candidate"},
                cookie,
            )
        except (http.client.RemoteDisconnected, ConnectionResetError) as error:
            self.fail("server closed the connection without a response: %s" % error)

        self.assertEqual(status, 404)
        self.assertEqual(json.loads(payload)["error"], "not_found")

    def test_question_bank_routes_require_teacher_or_admin_and_save_question(self):
        teacher_status, teacher_cookie, _ = self.server.login(
            "teacher_li",
            "teacher123",
        )
        student_status, student_cookie, _ = self.server.login(
            "stu_1001",
            "student123",
        )

        status, _, payload = self.server.post_json(
            "/api/teacher/question",
            {
                "stem": "测试题干",
                "options": {},
                "answer": {"type": "short_answer", "answer": "测试答案"},
                "analysis": "测试解析",
                "question_type": "short_answer",
                "source": "HTTP测试",
                "grade": "高二",
                "chapter": "运动的描述",
                "difficulty": "easy",
            },
            teacher_cookie,
        )
        forbidden, _, _ = self.server.post_json(
            "/api/teacher/question",
            {"stem": "学生不应创建"},
            student_cookie,
        )

        self.assertEqual(teacher_status, 303)
        self.assertEqual(student_status, 303)
        self.assertEqual(status, 200)
        self.assertIn(b"question", payload)
        self.assertEqual(forbidden, 404)

    def test_parse_task_routes_create_run_and_save_item(self):
        _, cookie, _ = self.server.login("teacher_li", "teacher123")

        status, _, payload = self.server.post_json(
            "/api/teacher/parse-task",
            {
                "paper_title": "HTTP解析样卷",
                "document_name": "http-sample.txt",
                "source_text": "1. 测试题干\nA. 1\nB. 2\n答案：B",
                "parser_mode": "deterministic_text",
                "source_school": "校内命题",
                "source_publisher": "高二物理备课组",
                "exam_type": "weekly_quiz",
                "grade": "高二",
                "term": "2025-2026下",
            },
            cookie,
        )
        self.assertEqual(status, 200)
        created = json.loads(payload)
        task_id = created["task"]["id"]

        status, _, payload = self.server.post_json(
            "/api/teacher/parse-task/run",
            {"task_id": task_id},
            cookie,
        )
        self.assertEqual(status, 200)
        parsed = json.loads(payload)
        item_id = parsed["result"]["items"][0]["id"]

        status, _, payload = self.server.post_json(
            "/api/teacher/parsed-question/save",
            {
                "parsed_item_id": item_id,
                "chapter": "运动的描述",
                "difficulty": "easy",
            },
            cookie,
        )

        self.assertEqual(status, 200)
        self.assertIn(b"question", payload)

    def test_default_taxonomy_install_is_admin_only_and_idempotent(self):
        anonymous_status, _, anonymous_payload = self.server.post_json(
            "/api/admin/taxonomy/install",
            {"publish": True},
            "",
        )
        _, teacher_cookie, _ = self.server.login(
            "teacher_li",
            "teacher123",
        )
        teacher_status, _, teacher_payload = self.server.post_json(
            "/api/admin/taxonomy/install",
            {"publish": True},
            teacher_cookie,
        )
        _, admin_cookie, _ = self.server.login("admin", "admin123")
        first_status, _, first_payload = self.server.post_json(
            "/api/admin/taxonomy/install",
            {"publish": True},
            admin_cookie,
        )
        second_status, _, second_payload = self.server.post_json(
            "/api/admin/taxonomy/install",
            {"publish": True},
            admin_cookie,
        )

        self.assertEqual(anonymous_status, 401)
        self.assertEqual(
            json.loads(anonymous_payload)["error"],
            "unauthorized",
        )
        self.assertEqual(teacher_status, 403)
        self.assertEqual(
            json.loads(teacher_payload)["error"],
            "forbidden",
        )
        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        first = json.loads(first_payload)
        second = json.loads(second_payload)
        self.assertTrue(first["ok"])
        self.assertEqual(
            first["result"]["version"],
            "pep-2019-physics-v1",
        )
        self.assertEqual(first["result"]["knowledge"]["created"], 0)
        self.assertEqual(second["result"]["knowledge"]["created"], 0)

    def test_literacy_endpoints_create_update_disable_and_enforce_admin(self):
        _, teacher_cookie, _ = self.server.login(
            "teacher_li",
            "teacher123",
        )
        teacher_status, _, teacher_payload = self.server.post_json(
            "/api/admin/literacy-tag",
            {
                "stable_code": "L.HTTP",
                "name": "HTTP 测试素养",
            },
            teacher_cookie,
        )
        _, admin_cookie, _ = self.server.login("admin", "admin123")
        create_status, _, create_payload = self.server.post_json(
            "/api/admin/literacy-tag",
            {
                "stable_code": "L.HTTP",
                "name": "HTTP 测试素养",
                "description": "接口创建",
                "source": "测试",
                "change_note": "首次创建",
                "enabled": True,
            },
            admin_cookie,
        )
        created = json.loads(create_payload)["result"]
        update_status, _, update_payload = self.server.post_json(
            "/api/admin/literacy-tag/update",
            {
                "literacy_id": created["id"],
                "name": "HTTP 测试素养修订",
                "description": "接口更新",
                "source": "测试审定",
                "change_note": "停用等待复核",
                "enabled": False,
            },
            admin_cookie,
        )
        updated = json.loads(update_payload)["result"]

        self.assertEqual(teacher_status, 403)
        self.assertEqual(
            json.loads(teacher_payload)["error"],
            "forbidden",
        )
        self.assertEqual(create_status, 200)
        self.assertEqual(created["level"], 1)
        self.assertEqual(update_status, 200)
        self.assertEqual(updated["name"], "HTTP 测试素养修订")
        self.assertEqual(updated["enabled"], 0)
        self.assertEqual(updated["change_note"], "停用等待复核")

    def test_literacy_endpoint_missing_field_returns_structured_400(self):
        _, admin_cookie, _ = self.server.login("admin", "admin123")

        status, _, payload = self.server.post_json(
            "/api/admin/literacy-tag",
            {"name": "缺少编码"},
            admin_cookie,
        )

        self.assertEqual(status, 400)
        self.assertEqual(
            json.loads(payload),
            {
                "error": "invalid_request",
                "message": "Missing required field: stable_code",
            },
        )

    def test_teacher_page_excludes_unassigned_class(self):
        self._seed_other_class()
        _, cookie, _ = self.server.login("teacher_li", "teacher123")

        status, _, payload = self.server.request(
            "GET",
            "/teacher",
            headers={"Cookie": cookie},
        )

        html = payload.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertNotIn("高二二班权限测试", html)
        self.assertNotIn("赵同学", html)

    def test_teacher_cannot_export_unassigned_class(self):
        self._seed_other_class()
        _, cookie, _ = self.server.login("teacher_li", "teacher123")

        status, _, payload = self.server.request(
            "GET",
            "/export/wrong-book/assess-week-2",
            headers={"Cookie": cookie},
        )

        self.assertEqual(status, 403)
        self.assertEqual(json.loads(payload)["error"], "forbidden")

    def test_teacher_cannot_grade_unassigned_class(self):
        self._seed_other_class()
        _, cookie, _ = self.server.login("teacher_li", "teacher123")

        status, _, payload = self.server.post_json(
            "/api/teacher/grade",
            {"assessment_id": "assess-week-2", "publish": True},
            cookie,
        )

        self.assertEqual(status, 403)
        self.assertEqual(json.loads(payload)["error"], "forbidden")

    def test_teacher_cannot_review_unassigned_class_response(self):
        self._seed_other_class()
        _, cookie, _ = self.server.login("teacher_li", "teacher123")

        status, _, payload = self.server.post_json(
            "/api/teacher/resolve-review",
            {
                "response_id": "resp-2001-q1",
                "corrected_answer": "B",
                "reason": "越权尝试",
            },
            cookie,
        )

        self.assertEqual(status, 403)
        self.assertEqual(json.loads(payload)["error"], "forbidden")

    def test_student_export_is_forced_to_authenticated_student(self):
        self._publish_demo_assessment()
        _, cookie, _ = self.server.login("stu_1001", "student123")

        status, _, payload = self.server.request(
            "GET",
            (
                "/export/wrong-book/assess-week-1"
                "?class_id=class-physics-1&student_id=stu-1002"
            ),
            headers={"Cookie": cookie},
        )

        html = payload.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("张明", html)
        self.assertNotIn("李华", html)

    def test_student_cannot_mark_another_students_wrong_question(self):
        self._publish_demo_assessment()
        conn = connect(self.server.db_path)
        try:
            wrong = PhysicsRepository(conn).list_wrong_questions_for_student(
                "stu-1002"
            )[0]
        finally:
            conn.close()
        _, cookie, _ = self.server.login("stu_1001", "student123")

        status, _, payload = self.server.post_json(
            "/api/student/mastery",
            {
                "wrong_question_id": wrong["id"],
                "level": "已掌握",
            },
            cookie,
        )

        self.assertEqual(status, 403)
        self.assertEqual(json.loads(payload)["error"], "forbidden")

    def test_student_page_hides_unpublished_results(self):
        conn = connect(self.server.db_path)
        try:
            repo = PhysicsRepository(conn)
            teacher = AuthService(conn).login(
                "teacher_li",
                "teacher123",
                "http-test-setup",
            ).user
            repo.resolve_review_item(
                teacher["id"],
                "resp-1001-q2",
                "C",
                "测试复核",
            )
            repo.grade_assessment(
                teacher["id"],
                "assess-week-1",
                publish=False,
            )
        finally:
            conn.close()
        _, cookie, _ = self.server.login("stu_1001", "student123")

        status, _, payload = self.server.request(
            "GET",
            "/app",
            headers={"Cookie": cookie},
        )

        html = payload.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertNotIn("6/10", html)
        self.assertNotIn("一个物体在水平面上受恒定合外力作用", html)

    def test_regrading_published_assessment_returns_409(self):
        self._publish_demo_assessment()
        _, cookie, _ = self.server.login("teacher_li", "teacher123")

        status, _, payload = self.server.post_json(
            "/api/teacher/grade",
            {"assessment_id": "assess-week-1", "publish": True},
            cookie,
        )

        self.assertEqual(status, 409)
        self.assertEqual(
            json.loads(payload),
            {
                "error": "state_conflict",
                "message": (
                    "Published assessments require an explicit revision"
                ),
            },
        )

    def test_teacher_can_apply_explicit_grading_revision_after_publication(self):
        self._publish_demo_assessment()
        _, cookie, _ = self.server.login("teacher_li", "teacher123")

        status, _, payload = self.server.post_json(
            "/api/teacher/grading-revision",
            {
                "assessment_id": "assess-week-1",
                "reason": "发布后复查",
                "items": [
                    {
                        "response_id": "resp-1001-q1",
                        "revised_answer": "B",
                        "revised_score": 0,
                        "max_score": 4,
                        "reason": "学生实际选择 B",
                    }
                ],
            },
            cookie,
        )

        self.assertEqual(status, 200)
        body = json.loads(payload)
        self.assertEqual(body["revision"]["status"], "applied")

    def test_student_redo_route_and_teacher_error_tag_route(self):
        self._publish_demo_assessment()
        conn = connect(self.server.db_path)
        try:
            wrong = PhysicsRepository(conn).list_wrong_questions_for_student(
                "stu-1001"
            )[0]
        finally:
            conn.close()
        _, teacher_cookie, _ = self.server.login("teacher_li", "teacher123")

        status, _, payload = self.server.post_json(
            "/api/teacher/error-reason-tag",
            {
                "code": "concept-force",
                "name": "概念混淆",
                "description": "力与运动关系理解错误",
            },
            teacher_cookie,
        )
        self.assertEqual(status, 200)
        tag = json.loads(payload)["tag"]

        status, _, payload = self.server.post_json(
            "/api/teacher/wrong-question/error-tags",
            {
                "wrong_question_id": wrong["id"],
                "tag_ids": [tag["id"]],
                "note": "把合力方向和速度方向混淆",
            },
            teacher_cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(payload)["wrong_question"]["error_reason_tags"][0]["name"],
            "概念混淆",
        )

        _, student_cookie, _ = self.server.login("stu_1001", "student123")
        status, _, payload = self.server.post_json(
            "/api/student/redo-attempt",
            {
                "wrong_question_id": wrong["id"],
                "answer": "C",
            },
            student_cookie,
        )
        self.assertEqual(status, 200)
        attempt = json.loads(payload)["attempt"]
        self.assertEqual(attempt["status"], "submitted")

        status, _, payload = self.server.post_json(
            "/api/teacher/redo-attempt/review",
            {
                "attempt_id": attempt["id"],
                "score": wrong["max_score"],
                "feedback": "重做正确",
            },
            teacher_cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["attempt"]["status"], "done")

        conn = connect(self.server.db_path)
        try:
            updated_wrong = PhysicsRepository(conn).list_wrong_questions_for_student(
                "stu-1001"
            )[0]
        finally:
            conn.close()
        self.assertEqual(updated_wrong["latest_redo_status"], "done")
        self.assertEqual(updated_wrong["error_reason_tags"][0]["name"], "概念混淆")
        self.assertEqual(updated_wrong["redo_attempts"][0]["feedback"], "重做正确")

    def test_student_can_clear_wrong_and_knowledge_mastery_for_undo(self):
        self._publish_demo_assessment()
        conn = connect(self.server.db_path)
        try:
            wrong = PhysicsRepository(conn).list_wrong_questions_for_student(
                "stu-1001"
            )[0]
        finally:
            conn.close()
        _, student_cookie, _ = self.server.login("stu_1001", "student123")

        status, _, _ = self.server.post_json(
            "/api/student/mastery",
            {
                "wrong_question_id": wrong["id"],
                "level": "基本掌握",
                "note": "HTTP 标记",
            },
            student_cookie,
        )
        self.assertEqual(status, 200)
        status, _, payload = self.server.post_json(
            "/api/student/mastery",
            {"wrong_question_id": wrong["id"], "clear": True},
            student_cookie,
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["result"]["cleared"])

        status, _, _ = self.server.post_json(
            "/api/student/knowledge-mastery",
            {
                "knowledge_node_id": "kn-pep2019-r1-c04-s03",
                "level": "需教师讲解",
                "note": "HTTP 标记",
            },
            student_cookie,
        )
        self.assertEqual(status, 200)
        status, _, payload = self.server.post_json(
            "/api/student/knowledge-mastery",
            {
                "knowledge_node_id": "kn-pep2019-r1-c04-s03",
                "clear": True,
            },
            student_cookie,
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["result"]["cleared"])

    def test_phase_2d_routes_execute_teacher_student_loop(self):
        _, teacher_cookie, _ = self.server.login("teacher_li", "teacher123")
        status, _, payload = self.server.post_json(
            "/api/teacher/paper-assembly",
            {
                "title": "HTTP Phase 2D 小测",
                "source": "HTTP",
                "question_items": [
                    {"question_id": "q-newton-1", "points": 4},
                    {"question_id": "q-newton-2", "points": 6},
                ],
            },
            teacher_cookie,
        )
        self.assertEqual(status, 200)
        paper_id = json.loads(payload)["result"]["paper"]["id"]

        status, _, payload = self.server.post_json(
            "/api/teacher/assessment-from-paper",
            {
                "paper_id": paper_id,
                "class_id": "class-physics-1",
                "title": "HTTP Phase 2D 小测",
                "term": "2025-2026下",
                "grade": "高二",
                "scheduled_at": "2026-06-12 08:00:00",
            },
            teacher_cookie,
        )
        self.assertEqual(status, 200)
        assessment_id = json.loads(payload)["assessment"]["id"]

        status, _, payload = self.server.post_json(
            "/api/teacher/ocr-import",
            {
                "assessment_id": assessment_id,
                "source_name": "HTTP OCR",
                "recognizer": "PaddleOCR",
                "recognizer_version": "reserved-local-v2",
                "items": [
                    {
                        "student_id": "stu-1001",
                        "question_id": "q-newton-1",
                        "answer": "A",
                        "confidence": 0.96,
                    },
                    {
                        "student_id": "stu-1001",
                        "question_id": "q-newton-2",
                        "answer": "D",
                        "confidence": 0.41,
                    },
                ],
            },
            teacher_cookie,
        )
        self.assertEqual(status, 200)
        scan_body = json.loads(payload)
        self.assertEqual(scan_body["scan"]["low_confidence_count"], 1)
        review_response_id = scan_body["scan"]["responses"][1]["id"]

        status, _, _ = self.server.post_json(
            "/api/teacher/resolve-review",
            {
                "response_id": review_response_id,
                "corrected_answer": "C",
                "reason": "HTTP OCR 复核",
            },
            teacher_cookie,
        )
        self.assertEqual(status, 200)

        status, _, payload = self.server.post_json(
            "/api/teacher/grade",
            {"assessment_id": assessment_id, "publish": True},
            teacher_cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["result"]["status"], "published")

        status, _, payload = self.server.post_json(
            "/api/teacher/grading-revision",
            {
                "assessment_id": assessment_id,
                "reason": "HTTP 发布后复查",
                "items": [
                    {
                        "response_id": scan_body["scan"]["responses"][0]["id"],
                        "revised_answer": "B",
                        "revised_score": 0,
                        "max_score": 4,
                        "reason": "学生实际选择 B",
                    }
                ],
            },
            teacher_cookie,
        )
        self.assertEqual(status, 200)

        status, _, payload = self.server.post_json(
            "/api/teacher/error-reason-tag",
            {
                "code": "http-concept-force",
                "name": "HTTP 概念混淆",
                "description": "HTTP 路由验收错因",
            },
            teacher_cookie,
        )
        self.assertEqual(status, 200)
        tag_id = json.loads(payload)["tag"]["id"]

        conn = connect(self.server.db_path)
        try:
            wrong = PhysicsRepository(conn).list_wrong_questions_for_student(
                "stu-1001"
            )[0]
        finally:
            conn.close()

        status, _, _ = self.server.post_json(
            "/api/teacher/wrong-question/error-tags",
            {
                "wrong_question_id": wrong["id"],
                "tag_ids": [tag_id],
                "note": "HTTP 归因",
            },
            teacher_cookie,
        )
        self.assertEqual(status, 200)

        _, student_cookie, _ = self.server.login("stu_1001", "student123")
        status, _, payload = self.server.post_json(
            "/api/student/redo-attempt",
            {"wrong_question_id": wrong["id"], "answer": "C"},
            student_cookie,
        )
        self.assertEqual(status, 200)
        attempt_id = json.loads(payload)["attempt"]["id"]

        status, _, payload = self.server.post_json(
            "/api/teacher/redo-attempt/review",
            {
                "attempt_id": attempt_id,
                "score": wrong["max_score"],
                "feedback": "重做正确",
            },
            teacher_cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["attempt"]["status"], "done")

    def test_admin_can_save_wrong_book_export_profile(self):
        _, cookie, _ = self.server.login("admin", "admin123")

        status, _, payload = self.server.post_json(
            "/api/admin/export-profile",
            {
                "name": "默认错题本",
                "options": {
                    "include_answers": False,
                    "include_analysis": False,
                    "include_error_reasons": True,
                    "include_redo_history": True,
                    "page_break": "student",
                },
            },
            cookie,
        )

        self.assertEqual(status, 200)
        profile = json.loads(payload)["profile"]
        self.assertEqual(profile["name"], "默认错题本")
        self.assertFalse(profile["options"]["include_answers"])
        self.assertTrue(profile["options"]["include_redo_history"])

    def test_runtime_check_endpoint_is_admin_only(self):
        admin_status, admin_cookie, _ = self.server.login("admin", "admin123")
        teacher_status, teacher_cookie, _ = self.server.login(
            "teacher_li",
            "teacher123",
        )
        self.assertEqual(admin_status, 303)
        self.assertEqual(teacher_status, 303)

        status, _, _ = self.server.post_json(
            "/api/admin/runtime-check",
            {},
            teacher_cookie,
        )
        self.assertEqual(status, 403)

        status, _, payload = self.server.post_json(
            "/api/admin/runtime-check",
            {},
            admin_cookie,
        )
        self.assertEqual(status, 200)
        body = json.loads(payload)
        self.assertIn("checks", body)
        self.assertTrue(
            {item["capability_id"] for item in body["checks"]}.issuperset(
                {"paddleocr", "markitdown", "mineru-local", "playwright-pdf"}
            )
        )

    def test_provider_operations_endpoints_are_admin_only_and_mask_secrets(self):
        _, admin_cookie, _ = self.server.login("admin", "admin123")
        _, teacher_cookie, _ = self.server.login("teacher_li", "teacher123")

        status, _, _ = self.server.post_json(
            "/api/admin/provider-config",
            {
                "provider_kind": "llm",
                "provider_name": "Teacher Attempt",
                "model_name": "model",
                "secret": "sk-teacher",
            },
            teacher_cookie,
        )
        self.assertEqual(status, 403)

        status, _, payload = self.server.post_json(
            "/api/admin/provider-config",
            {
                "provider_kind": "llm",
                "provider_name": "OpenAI Compatible",
                "model_name": "gpt-4.1-mini",
                "secret": "sk-http-secret",
                "api_endpoint": "https://api.example.test/v1",
                "enabled": True,
                "daily_call_limit": 10,
                "monthly_budget_cents": 100,
                "per_call_max_cents": 25,
                "input_cost_per_1k_cents": 2,
                "output_cost_per_1k_cents": 8,
            },
            admin_cookie,
        )
        self.assertEqual(status, 200)
        body = json.loads(payload)
        self.assertEqual(body["config"]["provider_kind"], "llm")
        self.assertIn("••••", body["config"]["secret_masked"])
        self.assertNotIn("secret_ciphertext", body["config"])
        self.assertNotIn("sk-http-secret", payload.decode("utf-8"))

        status, _, payload = self.server.post_json(
            "/api/admin/provider-test",
            {"provider_config_id": body["config"]["id"]},
            admin_cookie,
        )
        self.assertEqual(status, 200)
        tested = json.loads(payload)["config"]
        self.assertEqual(tested["last_test_status"], "ready")
        self.assertNotIn("sk-http-secret", payload.decode("utf-8"))

    def test_oidc_provider_endpoint_is_admin_only_and_masks_secret(self):
        _, admin_cookie, _ = self.server.login("admin", "admin123")
        _, teacher_cookie, _ = self.server.login("teacher_li", "teacher123")

        status, _, _ = self.server.post_json(
            "/api/admin/oidc-provider",
            {
                "provider_name": "Teacher OIDC",
                "issuer": "https://idp.example.test",
                "client_id": "teacher-client",
                "client_secret": "teacher-secret",
                "authorization_endpoint": "https://idp.example.test/authorize",
            },
            teacher_cookie,
        )
        self.assertEqual(status, 403)

        status, _, payload = self.server.post_json(
            "/api/admin/oidc-provider",
            {
                "provider_name": "School OIDC",
                "issuer": "https://idp.example.test",
                "client_id": "physics-client",
                "client_secret": "oidc-http-secret",
                "authorization_endpoint": "https://idp.example.test/authorize",
                "token_endpoint": "https://idp.example.test/token",
                "userinfo_endpoint": "https://idp.example.test/userinfo",
                "enabled": True,
            },
            admin_cookie,
        )
        self.assertEqual(status, 200)
        body = json.loads(payload)
        self.assertEqual(body["config"]["provider_name"], "School OIDC")
        self.assertNotIn("secret_ciphertext", body["config"])
        self.assertNotIn("oidc-http-secret", payload.decode("utf-8"))

    def test_teacher_can_generate_wrong_book_pdf_via_endpoint(self):
        self._publish_demo_assessment()
        _, cookie, _ = self.server.login("teacher_li", "teacher123")

        def fake_write_pdf_artifact(html, output_path, engine=None, options=None):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"%PDF-1.4\nhttp\n")
            return {
                "output_path": str(output_path),
                "file_name": Path(output_path).name,
                "content_type": "application/pdf",
                "byte_size": len(b"%PDF-1.4\nhttp\n"),
                "engine_version": "fake-http-pdf",
            }

        with patch(
            "highschoolphysics.repository.write_pdf_artifact",
            side_effect=fake_write_pdf_artifact,
        ):
            status, _, payload = self.server.post_json(
                "/api/teacher/wrong-book-pdf",
                {"assessment_id": "assess-week-1"},
                cookie,
            )

        self.assertEqual(status, 200)
        task = json.loads(payload)["task"]
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["content_type"], "application/pdf")
        self.assertEqual(task["engine_version"], "fake-http-pdf")

    def test_temporary_password_login_is_forced_through_change_page(self):
        self._import_temp_student()

        status, cookie, _ = self.server.login("stu_temp", "Temp123456")

        self.assertEqual(status, 303)
        status, headers, _ = self.server.request(
            "GET",
            "/app",
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/change-password")
        status, _, payload = self.server.request(
            "GET",
            "/change-password",
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 200)
        self.assertIn("首次登录修改密码", payload.decode("utf-8"))

    def test_temporary_password_can_be_changed_with_form(self):
        self._import_temp_student()
        _, cookie, _ = self.server.login("stu_temp", "Temp123456")

        status, headers, _ = self.server.request(
            "POST",
            "/change-password",
            urlencode(
                {
                    "current_password": "Temp123456",
                    "new_password": "NewPhysics123",
                    "confirm_password": "NewPhysics123",
                }
            ),
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": cookie,
            },
        )

        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/app")
        old_status, _, _ = self.server.login("stu_temp", "Temp123456")
        new_status, _, _ = self.server.login("stu_temp", "NewPhysics123")
        self.assertEqual(old_status, 401)
        self.assertEqual(new_status, 303)

    def test_temporary_password_session_cannot_call_other_apis(self):
        self._import_temp_student()
        _, cookie, _ = self.server.login("stu_temp", "Temp123456")

        status, _, payload = self.server.post_json(
            "/api/student/knowledge-mastery",
            {
                "knowledge_node_id": "kn-newton",
                "level": "基本掌握",
            },
            cookie,
        )

        self.assertEqual(status, 409)
        self.assertEqual(
            json.loads(payload),
            {
                "error": "password_change_required",
                "message": (
                    "You must change your temporary password before continuing"
                ),
            },
        )

    def test_teacher_reset_is_scoped_to_assigned_students(self):
        self._seed_other_class()
        _, cookie, _ = self.server.login("teacher_li", "teacher123")

        own_status, _, _ = self.server.post_json(
            "/api/password/reset",
            {
                "target_user_id": "stu-1001",
                "temporary_password": "TempOne123",
            },
            cookie,
        )
        other_status, _, payload = self.server.post_json(
            "/api/password/reset",
            {
                "target_user_id": "stu-2001",
                "temporary_password": "TempTwo123",
            },
            cookie,
        )

        self.assertEqual(own_status, 200)
        self.assertEqual(other_status, 403)
        self.assertEqual(json.loads(payload)["error"], "forbidden")

    def test_admin_can_reset_other_class_user_password(self):
        self._seed_other_class()
        _, cookie, _ = self.server.login("admin", "admin123")

        status, _, payload = self.server.post_json(
            "/api/password/reset",
            {
                "target_user_id": "stu-2001",
                "temporary_password": "TempAdmin123",
            },
            cookie,
        )

        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])


if __name__ == "__main__":
    unittest.main()
