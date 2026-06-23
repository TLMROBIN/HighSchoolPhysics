import json
import tempfile
import unittest
from pathlib import Path

from highschoolphysics.auth import AuthService
from highschoolphysics.db import connect
from highschoolphysics.errors import ResourceNotFound, StateConflict
from highschoolphysics.repository import PhysicsRepository
from highschoolphysics.security import hash_password
from tests.http_support import LivePhysicsServer, seed_other_class


def _conn(server):
    return connect(server.db_path)


class AdminTeacherApiTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.server_context = LivePhysicsServer(
            Path(self.tmpdir.name) / "admin-teacher.sqlite3"
        )
        self.server = self.server_context.__enter__()
        # seed_demo_data 已经放入了 class-physics-1, teacher_li -> class-physics-1
        # 我们用 seed_other_class 拿一个额外的 class-physics-2 用于替换 / 增删测试
        conn = _conn(self.server)
        try:
            seed_other_class(conn)
        finally:
            conn.close()

    def tearDown(self):
        self.server_context.__exit__(None, None, None)
        self.tmpdir.cleanup()

    # ------------------------- HTTP-level tests -------------------------

    def test_admin_can_import_teacher_and_returns_user_id(self):
        _, admin_cookie, _ = self.server.login("admin", "admin123")

        status, _, payload = self.server.post_json(
            "/api/admin/import-teacher",
            {
                "username": "teacher_zhao",
                "display_name": "赵老师",
                "temp_password": "TempTeacher123",
            },
            admin_cookie,
        )

        self.assertEqual(status, 200)
        body = json.loads(payload)
        self.assertTrue(body["ok"])
        user_id = body["user_id"]
        self.assertTrue(user_id.startswith("tea-"))

        # verify user row exists, role=teacher, must_change_password=1
        row = _conn(self.server).execute(
            "select role, status, must_change_password, password_hash "
            "from users where id = ?",
            (user_id,),
        ).fetchone()
        self.assertEqual(row["role"], "teacher")
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["must_change_password"], 1)
        self.assertTrue(hash_password and row["password_hash"].startswith("pbkdf2_sha256$"))
        self.assertTrue(_verify("TempTeacher123", row["password_hash"]))

    def test_import_teacher_rejects_duplicate_username_with_409(self):
        _, admin_cookie, _ = self.server.login("admin", "admin123")

        # 先建一次
        first, _, _ = self.server.post_json(
            "/api/admin/import-teacher",
            {
                "username": "teacher_chen",
                "display_name": "陈老师",
                "temp_password": "TempTeacher123",
            },
            admin_cookie,
        )
        self.assertEqual(first, 200)

        # 再建一次同样 username → StateConflict (status 409)
        status, _, payload = self.server.post_json(
            "/api/admin/import-teacher",
            {
                "username": "teacher_chen",
                "display_name": "陈老师2",
                "temp_password": "TempTeacher456",
            },
            admin_cookie,
        )
        self.assertEqual(status, 409)
        body = json.loads(payload)
        self.assertEqual(body["error"], "state_conflict")
        self.assertIn("Username already exists", body["message"])

    def test_import_teacher_rejects_weak_password_with_400(self):
        _, admin_cookie, _ = self.server.login("admin", "admin123")

        # 太短
        status, _, payload = self.server.post_json(
            "/api/admin/import-teacher",
            {
                "username": "teacher_qian",
                "display_name": "钱老师",
                "temp_password": "short",
            },
            admin_cookie,
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"], "invalid_request")

        # 没字母
        status, _, _ = self.server.post_json(
            "/api/admin/import-teacher",
            {
                "username": "teacher_qian2",
                "display_name": "钱老师2",
                "temp_password": "1234567890",
            },
            admin_cookie,
        )
        self.assertEqual(status, 400)

        # 没数字
        status, _, _ = self.server.post_json(
            "/api/admin/import-teacher",
            {
                "username": "teacher_qian3",
                "display_name": "钱老师3",
                "temp_password": "OnlyLetters",
            },
            admin_cookie,
        )
        self.assertEqual(status, 400)

        # 缺必填字段
        status, _, payload = self.server.post_json(
            "/api/admin/import-teacher",
            {
                "display_name": "缺用户名",
                "temp_password": "TempTeacher123",
            },
            admin_cookie,
        )
        self.assertEqual(status, 400)
        self.assertIn("Missing required field", json.loads(payload)["message"])

    def test_import_teacher_rejects_non_admin_with_403(self):
        _, teacher_cookie, _ = self.server.login("teacher_li", "teacher123")
        _, student_cookie, _ = self.server.login("stu_1001", "student123")

        status_t, _, payload_t = self.server.post_json(
            "/api/admin/import-teacher",
            {
                "username": "teacher_by_teacher",
                "display_name": "教师建的教师",
                "temp_password": "TempTeacher123",
            },
            teacher_cookie,
        )
        self.assertEqual(status_t, 403)

        status_s, _, _ = self.server.post_json(
            "/api/admin/import-teacher",
            {
                "username": "teacher_by_student",
                "display_name": "学生建的教师",
                "temp_password": "TempTeacher123",
            },
            student_cookie,
        )
        self.assertEqual(status_s, 403)

    def test_assign_classes_add_remove_replace_set_for_teacher(self):
        _, admin_cookie, _ = self.server.login("admin", "admin123")

        # 拿 teacher_li 的 id(已 seed)
        conn = _conn(self.server)
        try:
            teacher_id = conn.execute(
                "select id from users where username = 'teacher_li'"
            ).fetchone()["id"]
            # teacher_li 原本就分配到 class-physics-1
            initial = conn.execute(
                "select class_id from teacher_classes "
                "where teacher_id = ? and subject = 'physics'",
                (teacher_id,),
            ).fetchall()
            self.assertEqual(
                sorted(row["class_id"] for row in initial),
                ["class-physics-1"],
            )
        finally:
            conn.close()

        # 1) 增加 class-physics-2 → 期望 assigned=[class-physics-2], removed=[]
        status, _, payload = self.server.post_json(
            "/api/admin/teacher/%s/assign-classes" % teacher_id,
            {"class_ids": ["class-physics-1", "class-physics-2"]},
            admin_cookie,
        )
        self.assertEqual(status, 200)
        body = json.loads(payload)
        self.assertTrue(body["ok"])
        self.assertEqual(body["assigned"], ["class-physics-2"])
        self.assertEqual(body["removed"], [])

        rows = self._assigned_classes(teacher_id)
        self.assertEqual(sorted(rows), ["class-physics-1", "class-physics-2"])

        # 2) 替换为只有 class-physics-2 → 期望 assigned=[], removed=[class-physics-1]
        status, _, payload = self.server.post_json(
            "/api/admin/teacher/%s/assign-classes" % teacher_id,
            {"class_ids": ["class-physics-2"]},
            admin_cookie,
        )
        self.assertEqual(status, 200)
        body = json.loads(payload)
        self.assertEqual(body["assigned"], [])
        self.assertEqual(body["removed"], ["class-physics-1"])

        rows = self._assigned_classes(teacher_id)
        self.assertEqual(rows, ["class-physics-2"])

        # 3) 清空 (空数组)
        status, _, payload = self.server.post_json(
            "/api/admin/teacher/%s/assign-classes" % teacher_id,
            {"class_ids": []},
            admin_cookie,
        )
        self.assertEqual(status, 200)
        body = json.loads(payload)
        self.assertEqual(body["assigned"], [])
        self.assertEqual(body["removed"], ["class-physics-2"])
        self.assertEqual(self._assigned_classes(teacher_id), [])

    def test_assign_classes_accepts_single_string_payload_from_compact_form(self):
        _, admin_cookie, _ = self.server.login("admin", "admin123")
        conn = _conn(self.server)
        try:
            teacher_id = conn.execute(
                "select id from users where username = 'teacher_li'"
            ).fetchone()["id"]
        finally:
            conn.close()

        status, _, payload = self.server.post_json(
            "/api/admin/teacher/%s/assign-classes" % teacher_id,
            {"class_ids": "class-physics-2"},
            admin_cookie,
        )

        self.assertEqual(status, 200)
        body = json.loads(payload)
        self.assertTrue(body["ok"])
        self.assertEqual(self._assigned_classes(teacher_id), ["class-physics-2"])

    def test_admin_accounts_page_keeps_teacher_create_and_assignment_controls(self):
        _, admin_cookie, _ = self.server.login("admin", "admin123")

        status, _, payload = self.server.post_json(
            "/api/admin/import-teacher",
            {
                "username": "teacher_compact",
                "display_name": "紧凑页老师",
                "temp_password": "TempTeacher123",
            },
            admin_cookie,
        )
        self.assertEqual(status, 200)
        teacher_id = json.loads(payload)["user_id"]

        status, _, body = self.server.request(
            "GET",
            "/admin",
            None,
            {"Cookie": admin_cookie},
        )
        self.assertEqual(status, 200)
        html = body.decode("utf-8", errors="replace")
        self.assertIn('data-admin-tab-panel="accounts"', html)
        self.assertIn('data-admin-form="import-teacher"', html)
        self.assertIn("teacher_compact", html)
        self.assertIn('data-action="open-assign-classes"', html)
        self.assertIn('data-admin-form="assign-classes"', html)
        self.assertIn(
            'data-endpoint="/api/admin/teacher/%s/assign-classes"' % teacher_id,
            html,
        )
        self.assertIn('name="class_ids"', html)

    def test_assign_classes_subject_is_always_physics_in_db(self):
        _, admin_cookie, _ = self.server.login("admin", "admin123")
        conn = _conn(self.server)
        try:
            teacher_id = conn.execute(
                "select id from users where username = 'teacher_li'"
            ).fetchone()["id"]
        finally:
            conn.close()

        status, _, _ = self.server.post_json(
            "/api/admin/teacher/%s/assign-classes" % teacher_id,
            {"class_ids": ["class-physics-1", "class-physics-2"]},
            admin_cookie,
        )
        self.assertEqual(status, 200)

        # 数据库直查 subject 必须全部是 'physics'
        conn = _conn(self.server)
        try:
            rows = conn.execute(
                "select subject from teacher_classes where teacher_id = ?",
                (teacher_id,),
            ).fetchall()
            self.assertTrue(rows)
            self.assertTrue(
                all(row["subject"] == "physics" for row in rows),
                "expected subject='physics' for every row, got %r"
                % [row["subject"] for row in rows],
            )
        finally:
            conn.close()

    def test_assign_classes_after_change_teacher_can_only_see_newly_assigned_class(self):
        _, admin_cookie, _ = self.server.login("admin", "admin123")
        conn = _conn(self.server)
        try:
            teacher_id = conn.execute(
                "select id from users where username = 'teacher_li'"
            ).fetchone()["id"]
        finally:
            conn.close()

        # 改成只剩 class-physics-2
        status, _, _ = self.server.post_json(
            "/api/admin/teacher/%s/assign-classes" % teacher_id,
            {"class_ids": ["class-physics-2"]},
            admin_cookie,
        )
        self.assertEqual(status, 200)

        # 用 AuthService 检查教师只能访问新分配的班级
        conn = _conn(self.server)
        try:
            auth = AuthService(conn)
            teacher = auth.login("teacher_li", "teacher123", "test").user
            self.assertTrue(auth.can(teacher, "view", "class_wrong_questions", "class-physics-2"))
            self.assertFalse(auth.can(teacher, "view", "class_wrong_questions", "class-physics-1"))
        finally:
            conn.close()

    def test_assign_classes_unknown_teacher_returns_404(self):
        _, admin_cookie, _ = self.server.login("admin", "admin123")

        status, _, payload = self.server.post_json(
            "/api/admin/teacher/tea-does-not-exist/assign-classes",
            {"class_ids": ["class-physics-1"]},
            admin_cookie,
        )
        self.assertEqual(status, 404)
        body = json.loads(payload)
        self.assertEqual(body["error"], "not_found")

    def test_assign_classes_unknown_class_id_returns_400(self):
        _, admin_cookie, _ = self.server.login("admin", "admin123")
        conn = _conn(self.server)
        try:
            teacher_id = conn.execute(
                "select id from users where username = 'teacher_li'"
            ).fetchone()["id"]
        finally:
            conn.close()

        status, _, payload = self.server.post_json(
            "/api/admin/teacher/%s/assign-classes" % teacher_id,
            {"class_ids": ["class-physics-1", "class-ghost-99"]},
            admin_cookie,
        )
        self.assertEqual(status, 400)
        body = json.loads(payload)
        self.assertEqual(body["error"], "invalid_request")
        self.assertIn("class-ghost-99", body["message"])

    def test_assign_classes_non_admin_returns_403(self):
        conn = _conn(self.server)
        try:
            teacher_id = conn.execute(
                "select id from users where username = 'teacher_li'"
            ).fetchone()["id"]
        finally:
            conn.close()

        _, teacher_cookie, _ = self.server.login("teacher_li", "teacher123")

        status, _, _ = self.server.post_json(
            "/api/admin/teacher/%s/assign-classes" % teacher_id,
            {"class_ids": ["class-physics-1"]},
            teacher_cookie,
        )
        self.assertEqual(status, 403)

    # ------------------------- repository-level tests -------------------------

    def test_repository_create_teacher_raises_state_conflict_on_duplicate(self):
        repo = PhysicsRepository(_conn(self.server))
        with self.assertRaises(StateConflict):
            repo.create_teacher(
                actor_id="user-admin",
                username="teacher_li",  # 已经存在
                display_name="覆盖李老师",
                temp_password_hash=hash_password("TempTeacher123"),
            )

    def test_repository_set_teacher_classes_validates_user_role(self):
        repo = PhysicsRepository(_conn(self.server))
        # 用 student id 触发 "not a teacher"
        with self.assertRaises(Exception) as ctx:
            repo.set_teacher_classes(
                actor_id="user-admin",
                teacher_id="stu-1001",
                class_ids=["class-physics-1"],
            )
        # 应当抛 InvalidRequest 或 ResourceNotFound (因为 status='active' 且 role='student')
        self.assertIn(type(ctx.exception).__name__, ("InvalidRequest", "ResourceNotFound"))

    def test_repository_set_teacher_classes_404_on_missing_teacher(self):
        repo = PhysicsRepository(_conn(self.server))
        with self.assertRaises(ResourceNotFound):
            repo.set_teacher_classes(
                actor_id="user-admin",
                teacher_id="tea-does-not-exist",
                class_ids=["class-physics-1"],
            )

    # ------------------------- helpers -------------------------

    def _assigned_classes(self, teacher_id):
        conn = _conn(self.server)
        try:
            rows = conn.execute(
                "select class_id from teacher_classes "
                "where teacher_id = ? and subject = 'physics' "
                "order by class_id",
                (teacher_id,),
            ).fetchall()
            return [row["class_id"] for row in rows]
        finally:
            conn.close()


def _verify(password, stored_hash):
    """避免顶层 import verify_password 的循环,local helper."""
    from highschoolphysics.security import verify_password
    return verify_password(password, stored_hash)


if __name__ == "__main__":
    unittest.main()
