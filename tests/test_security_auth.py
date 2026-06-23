import tempfile
import unittest
from pathlib import Path

from highschoolphysics.auth import AuthService
from highschoolphysics.db import connect, initialize_database, seed_demo_data
from highschoolphysics.errors import PermissionDenied
from highschoolphysics.repository import PhysicsRepository
from highschoolphysics.security import hash_password, mask_secret, verify_password
from tests.http_support import seed_other_class


class SecurityAndAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.sqlite3"
        self.conn = connect(self.db_path)
        initialize_database(self.conn)
        seed_demo_data(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_password_hashes_are_salted_and_verifiable(self):
        first = hash_password("physics123")
        second = hash_password("physics123")

        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("physics123", first))
        self.assertFalse(verify_password("wrong", first))

    def test_teacher_cannot_access_unassigned_class_wrong_questions(self):
        auth = AuthService(self.conn)
        teacher = auth.login("teacher_li", "teacher123", user_agent="unit-test")
        own_class = self.conn.execute(
            "select id from class_groups where name = ?", ("高二(1)班",)
        ).fetchone()["id"]
        other_class = self.conn.execute(
            "insert into class_groups(id, school_id, name, grade, school_year, status) values(?,?,?,?,?,?) returning id",
            ("class-other", "school-demo", "高二(2)班", "高二", "2025-2026", "active"),
        ).fetchone()["id"]

        self.assertTrue(auth.can(teacher.user, "view", "class_wrong_questions", own_class))
        self.assertFalse(auth.can(teacher.user, "view", "class_wrong_questions", other_class))

        denied = self.conn.execute(
            "select count(*) as count from audit_events where action = ?",
            ("permission_denied",),
        ).fetchone()["count"]
        self.assertEqual(denied, 1)

    def test_change_password_clears_required_flag_and_audits(self):
        student_id = PhysicsRepository(self.conn).import_student(
            actor_id="user-admin",
            username="stu_temp",
            display_name="临时学生",
            student_no="1999",
            class_id="class-physics-1",
            temp_password_hash=hash_password("Temp123456"),
        )
        auth = AuthService(self.conn)

        auth.change_password(
            actor_id=student_id,
            user_id=student_id,
            current_password="Temp123456",
            new_password="NewPhysics123",
        )

        row = self.conn.execute(
            "select password_hash, must_change_password from users where id = ?",
            (student_id,),
        ).fetchone()
        self.assertEqual(row["must_change_password"], 0)
        self.assertTrue(verify_password("NewPhysics123", row["password_hash"]))
        audit = self.conn.execute(
            """
            select actor_id, action, user_id, detail_json
            from identity_audit_logs
            where action = 'password_changed'
            """
        ).fetchone()
        self.assertEqual(audit["actor_id"], student_id)
        self.assertEqual(audit["user_id"], student_id)
        self.assertNotIn("NewPhysics123", audit["detail_json"])

    def test_teacher_can_only_reset_password_for_assigned_class(self):
        seed_other_class(self.conn)
        auth = AuthService(self.conn)
        teacher = auth.login("teacher_li", "teacher123", "unit-test").user
        student_session = auth.login("stu_1001", "student123", "unit-test")

        auth.reset_password(teacher, "stu-1001", "TempOne123")

        row = self.conn.execute(
            "select password_hash, must_change_password from users where id = ?",
            ("stu-1001",),
        ).fetchone()
        self.assertTrue(verify_password("TempOne123", row["password_hash"]))
        self.assertEqual(row["must_change_password"], 1)
        self.assertIsNone(auth.user_from_token(student_session.token))
        with self.assertRaises(PermissionDenied):
            auth.reset_password(teacher, "stu-2001", "TempTwo123")
        audit = self.conn.execute(
            """
            select actor_id, user_id, detail_json
            from identity_audit_logs
            where action = 'password_reset'
            """
        ).fetchone()
        self.assertEqual(audit["actor_id"], teacher["id"])
        self.assertEqual(audit["user_id"], "stu-1001")
        self.assertNotIn("TempOne123", audit["detail_json"])

    def test_mask_secret_hides_llm_key_material(self):
        self.assertEqual(mask_secret("sk-1234567890abcdef"), "sk-1********cdef")
        self.assertEqual(mask_secret("short"), "*****")

    def test_session_for_user_creates_session_for_active_account(self):
        auth = AuthService(self.conn)

        session = auth.session_for_user("stu-1001", "oidc-test")

        self.assertTrue(session.token)
        self.assertEqual(session.user["username"], "stu_1001")
        self.assertEqual(auth.user_from_token(session.token)["id"], "stu-1001")


if __name__ == "__main__":
    unittest.main()
