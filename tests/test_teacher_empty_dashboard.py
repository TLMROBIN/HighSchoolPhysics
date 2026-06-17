"""回归测试:admin 刚导入 / 刚分配班级的教师(0 个测评)访问 /teacher 不应 500。

背景:
  上轮发现管理员创建新教师 + 分配班级 + 改密登录后,/teacher 页面 HTTP 500
  + {"error":"internal_error"}。根因是 render_teacher_app 第 1034 行
  `assessment = dashboard["assessments"][0]` 在零 assessment 时 IndexError。
  repository.teacher_dashboard 已经为 0 assessment 做了空结构兜底
  (assessments=[], 空 diagnostics, 空 mastery_analytics, 空 students),
  server.py 必须能渲染这个空状态,而不是 500。

这个测试必须在 admin → import-teacher → assign-classes → 新教师登录
→ GET /teacher 全链路上,验证 status=200 且页面包含「暂无测评」空状态文案。
"""
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

from tests.http_support import LivePhysicsServer, seed_other_class


def _conn(server):
    from highschoolphysics.db import connect
    return connect(server.db_path)


class TeacherEmptyDashboardTests(unittest.TestCase):
    """覆盖 admin 刚 import-teacher + 分配班级后,新教师 GET /teacher 不 500。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.server_context = LivePhysicsServer(
            Path(self.tmpdir.name) / "teacher-empty.sqlite3"
        )
        self.server = self.server_context.__enter__()
        # seed_other_class 拿一个 class-physics-2 用于分配
        conn = _conn(self.server)
        try:
            seed_other_class(conn)
        finally:
            conn.close()

    def tearDown(self):
        self.server_context.__exit__(None, None, None)
        self.tmpdir.cleanup()

    # -------------------- 主回归: /teacher 200 + 含空状态文案 --------------------

    def test_teacher_with_zero_assessments_does_not_500(self):
        """新教师 0 测评,GET /teacher 应返回 200 而不是 500。"""
        # 1) admin 登录
        _, admin_cookie, _ = self.server.login("admin", "admin123")

        # 2) admin 创建一个新教师
        status, _, payload = self.server.post_json(
            "/api/admin/import-teacher",
            {
                "username": "teacher_empty",
                "display_name": "空测评老师",
                "temp_password": "TempTeacher123",
            },
            admin_cookie,
        )
        self.assertEqual(status, 200, "import-teacher should succeed")
        body = json.loads(payload)
        self.assertTrue(body["ok"])
        teacher_id = body["user_id"]
        self.assertTrue(teacher_id.startswith("tea-"))

        # 3) admin 把 class-physics-1 分配给该教师
        status, _, payload = self.server.post_json(
            "/api/admin/teacher/%s/assign-classes" % teacher_id,
            {"class_ids": ["class-physics-1"]},
            admin_cookie,
        )
        self.assertEqual(status, 200, "assign-classes should succeed")

        # 4) 模拟教师首次登录 + 改密(must_change_password=1 → /change-password)
        status, change_cookie, _ = self.server.login(
            "teacher_empty", "TempTeacher123"
        )
        # 登录应 303 重定向到 /change-password(SEE_OTHER 状态)
        self.assertIn(status, (200, 303))

        # 5) 教师改密
        change_body = urlencode({
            "current_password": "TempTeacher123",
            "new_password": "NewTeacher123",
            "confirm_password": "NewTeacher123",
        })
        status, _, _ = self.server.request(
            "POST",
            "/change-password",
            change_body.encode("utf-8"),
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": change_cookie,
            },
        )
        self.assertEqual(status, 303, "change-password should redirect")

        # 6) 教师再次登录(改密后)拿新 cookie
        status, teacher_cookie, _ = self.server.login(
            "teacher_empty", "NewTeacher123"
        )
        self.assertIn(status, (200, 303))
        self.assertTrue(teacher_cookie, "teacher should have a session cookie")

        # 7) GET /teacher —— 关键断言:200 而非 500
        status, _, body = self.server.request(
            "GET",
            "/teacher",
            None,
            {"Cookie": teacher_cookie},
        )
        self.assertNotEqual(
            status, 500,
            "GET /teacher must not 500 even with 0 assessments. "
            "Got status=%s" % status,
        )
        self.assertEqual(
            status, 200,
            "GET /teacher should return 200 with empty state. Got %s" % status,
        )

        # 8) 页面必须包含空状态文案(「暂无测评」/「empty-state」)
        text = body.decode("utf-8", errors="replace")
        self.assertIn(
            "暂无测评",
            text,
            "Empty state must include the 暂无测评 prompt",
        )
        self.assertIn(
            "data-empty-state=\"no-assessment\"",
            text,
            "Empty state must carry data-empty-state=\"no-assessment\" marker",
        )
        # 同时不该出现 500 错误 JSON
        self.assertNotIn("internal_error", text)
        # 标题仍然是「教师端 - 高中物理闭环系统」
        self.assertIn("教师端", text)
        # 退出按钮仍然在
        self.assertIn("/logout", text)

    # -------------------- 辅助:直接验证 render_teacher_app 空状态 --------------------

    def test_render_teacher_app_empty_dashboard_does_not_raise(self):
        """不依赖 LivePhysicsServer,直接测 render_teacher_app 纯函数。"""
        from highschoolphysics.server import render_teacher_app
        dashboard = {
            "assessments": [],
            "pending_candidates": [],
            "review_items": [],
            "knowledge_nodes": [],
            "knowledge_edges": [],
            "ability_tags": [],
            "literacy_tags": [],
            "question_bank": [],
            "parse_tasks": [],
            "parsed_items": [],
            "diagnostics": {
                "knowledge_error_rates": [],
                "ability_error_rates": [],
                "grade_average": {
                    "grade": "高二",
                    "student_count": 0,
                    "average_score_rate": 0.0,
                },
            },
            "mastery_analytics": {
                "assessment": None,
                "class": None,
                "knowledge": [],
                "ability": [],
                "literacy": [],
                "grade_comparison": {
                    "grade": "",
                    "knowledge": [],
                    "ability": [],
                    "literacy": [],
                },
            },
            "classes": [
                {"id": "class-physics-1", "name": "高二(1)班"},
            ],
            "students": [],
        }
        user = {
            "id": "tea-fake",
            "role": "teacher",
            "display_name": "空测评老师",
            "username": "teacher_empty",
        }
        # 关键断言:不抛 IndexError / KeyError
        html = render_teacher_app(user, dashboard)
        self.assertIsInstance(html, str)
        self.assertIn("暂无测评", html)
        self.assertIn("data-empty-state=\"no-assessment\"", html)
        # 不应出现 5xx
        self.assertNotIn("internal_error", html)

    # -------------------- 辅助:teacher_dashboard 零 assessment 时返回结构正确 --------------------

    def test_teacher_dashboard_zero_assessments_returns_empty_lists(self):
        """repository 端:teacher_dashboard 在 0 assessment 时返回空结构。"""
        from highschoolphysics.db import connect
        from highschoolphysics.repository import PhysicsRepository

        # 建一个新教师 + 分配班级,但不创建任何 assessment
        _, admin_cookie, _ = self.server.login("admin", "admin123")
        status, _, payload = self.server.post_json(
            "/api/admin/import-teacher",
            {
                "username": "teacher_zero",
                "display_name": "零测评老师",
                "temp_password": "TempTeacher123",
            },
            admin_cookie,
        )
        self.assertEqual(status, 200)
        teacher_id = json.loads(payload)["user_id"]
        status, _, _ = self.server.post_json(
            "/api/admin/teacher/%s/assign-classes" % teacher_id,
            {"class_ids": ["class-physics-1"]},
            admin_cookie,
        )
        self.assertEqual(status, 200)

        # repository.teacher_dashboard 必须返回 assessments=[], 不抛
        conn = connect(self.server.db_path)
        try:
            repo = PhysicsRepository(conn)
            dashboard = repo.teacher_dashboard(teacher_id)
        finally:
            conn.close()

        self.assertEqual(dashboard["assessments"], [])
        # diagnostics / mastery_analytics / students 都已经兜底成空结构
        self.assertEqual(dashboard["students"], [])
        self.assertIsNone(dashboard["diagnostics"].get("assessment"))
        self.assertIsNone(dashboard["mastery_analytics"].get("assessment"))


if __name__ == "__main__":
    unittest.main()
