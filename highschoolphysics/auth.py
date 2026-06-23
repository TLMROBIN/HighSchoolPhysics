from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import uuid

from .errors import InvalidRequest, PermissionDenied, ResourceNotFound
from .security import hash_password, hash_token, new_session_token, verify_password


@dataclass
class LoginResult:
    token: str
    user: dict


def _row_to_dict(row):
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def validate_password(password):
    if len(password) < 10:
        raise InvalidRequest("Password must contain at least 10 characters")
    if not any(character.isalpha() for character in password):
        raise InvalidRequest("Password must contain at least one letter")
    if not any(character.isdigit() for character in password):
        raise InvalidRequest("Password must contain at least one digit")


class AuthService:
    def __init__(self, conn):
        self.conn = conn

    def login(self, username, password, user_agent=""):
        row = self.conn.execute(
            "select * from users where username = ? and status = 'active'",
            (username,),
        ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            self._audit(None, "login_failed", "user", username, {"username": username})
            raise ValueError("Invalid username or password")

        token = new_session_token()
        expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat(timespec="seconds")
        self.conn.execute(
            """
            insert into auth_sessions(token_hash, user_id, user_agent, expires_at)
            values(?,?,?,?)
            """,
            (hash_token(token), row["id"], user_agent, expires_at),
        )
        self._identity_audit(row["id"], "login", row["id"], {"user_agent": user_agent})
        self.conn.commit()
        user = _row_to_dict(row)
        user.pop("password_hash", None)
        return LoginResult(token=token, user=user)

    def session_for_user(self, user_id, user_agent=""):
        row = self.conn.execute(
            "select * from users where id = ? and status = 'active'",
            (user_id,),
        ).fetchone()
        if row is None:
            raise PermissionDenied("Active user is required")

        token = new_session_token()
        expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat(timespec="seconds")
        self.conn.execute(
            """
            insert into auth_sessions(token_hash, user_id, user_agent, expires_at)
            values(?,?,?,?)
            """,
            (hash_token(token), row["id"], user_agent, expires_at),
        )
        self._identity_audit(row["id"], "sso_login", row["id"], {"user_agent": user_agent})
        self.conn.commit()
        user = _row_to_dict(row)
        user.pop("password_hash", None)
        return LoginResult(token=token, user=user)

    def logout(self, token, actor_id=None):
        self.conn.execute(
            "update auth_sessions set revoked_at = current_timestamp where token_hash = ?",
            (hash_token(token),),
        )
        self._identity_audit(actor_id, "logout", actor_id, {})
        self.conn.commit()

    def user_from_token(self, token):
        if not token:
            return None
        row = self.conn.execute(
            """
            select users.*
            from auth_sessions
            join users on users.id = auth_sessions.user_id
            where auth_sessions.token_hash = ?
              and auth_sessions.revoked_at is null
              and datetime(auth_sessions.expires_at) > datetime('now')
              and users.status = 'active'
            """,
            (hash_token(token),),
        ).fetchone()
        if row is None:
            return None
        user = _row_to_dict(row)
        user.pop("password_hash", None)
        return user

    def user_by_id(self, user_id):
        row = self.conn.execute(
            "select * from users where id = ? and status = 'active'",
            (user_id,),
        ).fetchone()
        user = _row_to_dict(row)
        if user:
            user.pop("password_hash", None)
        return user

    def change_password(
        self,
        actor_id,
        user_id,
        current_password,
        new_password,
    ):
        row = self.conn.execute(
            "select * from users where id = ? and status = 'active'",
            (user_id,),
        ).fetchone()
        if row is None:
            raise ResourceNotFound("User not found")
        if actor_id != user_id or not verify_password(
            current_password,
            row["password_hash"],
        ):
            raise PermissionDenied("Current password is invalid")
        validate_password(new_password)
        self.conn.execute(
            """
            update users
            set password_hash = ?, must_change_password = 0
            where id = ?
            """,
            (hash_password(new_password), user_id),
        )
        self._identity_audit(actor_id, "password_changed", user_id, {})
        self.conn.commit()

    def reset_password(self, actor_user, target_user_id, temporary_password):
        target = self.conn.execute(
            "select * from users where id = ? and status = 'active'",
            (target_user_id,),
        ).fetchone()
        if target is None:
            raise ResourceNotFound("User not found")
        if not actor_user or actor_user["school_id"] != target["school_id"]:
            raise PermissionDenied("You cannot reset this user's password")
        if not self.can(
            actor_user,
            "reset",
            "user_password",
            target_user_id,
        ):
            raise PermissionDenied("You cannot reset this user's password")

        validate_password(temporary_password)
        self.conn.execute(
            """
            update users
            set password_hash = ?, must_change_password = 1
            where id = ?
            """,
            (hash_password(temporary_password), target_user_id),
        )
        self.conn.execute(
            """
            update auth_sessions
            set revoked_at = current_timestamp
            where user_id = ? and revoked_at is null
            """,
            (target_user_id,),
        )
        self._identity_audit(
            actor_user["id"],
            "password_reset",
            target_user_id,
            {},
        )
        self.conn.commit()

    def can_assessment(self, user, operation, assessment_id):
        return self.can(user, operation, "assessment", assessment_id)

    def can_response(self, user, operation, response_id):
        row = self.conn.execute(
            "select assessment_id from student_responses where id = ?",
            (response_id,),
        ).fetchone()
        return bool(row) and self.can(
            user,
            operation,
            "assessment",
            row["assessment_id"],
        )

    def can(self, user, operation, resource, scope_id=None):
        if not user:
            self._audit(None, "permission_denied", resource, scope_id, {"operation": operation})
            self.conn.commit()
            return False
        if user["role"] == "admin":
            return True
        allowed = False
        if user["role"] == "teacher":
            allowed = self._teacher_can(user["id"], operation, resource, scope_id)
        elif user["role"] == "student":
            allowed = self._student_can(user, operation, resource, scope_id)

        if not allowed:
            self._audit(
                user["id"],
                "permission_denied",
                resource,
                scope_id,
                {"operation": operation, "role": user["role"]},
            )
            self.conn.commit()
        return allowed

    def _teacher_can(self, teacher_id, operation, resource, scope_id):
        if resource == "user_password":
            if operation != "reset" or scope_id is None:
                return False
            return (
                self.conn.execute(
                    """
                    select 1
                    from users target
                    join users teacher on teacher.id = ?
                    join teacher_classes tc
                      on tc.teacher_id = teacher.id
                     and tc.class_id = target.class_id
                     and tc.subject = 'physics'
                    where target.id = ?
                      and target.role = 'student'
                      and target.status = 'active'
                      and target.school_id = teacher.school_id
                    """,
                    (teacher_id, scope_id),
                ).fetchone()
                is not None
            )
        if resource in ("class_wrong_questions", "assessment", "diagnostics"):
            if scope_id is None:
                return False
            allowed_operations = {
                "class_wrong_questions": ("view", "export"),
                "assessment": ("view", "review", "grade", "publish", "export"),
                "diagnostics": ("view",),
            }
            if operation not in allowed_operations[resource]:
                return False
            class_id = scope_id
            if resource in ("assessment", "diagnostics"):
                row = self.conn.execute(
                    "select class_id from assessment_sessions where id = ?",
                    (scope_id,),
                ).fetchone()
                if row is None:
                    return False
                class_id = row["class_id"]
            return (
                self.conn.execute(
                    """
                    select 1 from teacher_classes
                    where teacher_id = ? and class_id = ? and subject = 'physics'
                    """,
                    (teacher_id, class_id),
                ).fetchone()
                is not None
            )
        if resource in (
            "question",
            "question_tags",
            "knowledge_graph",
            "scan_review",
        ):
            return operation in ("view", "create", "modify", "review", "export")
        return False

    def _student_can(self, user, operation, resource, scope_id):
        if resource in ("wrong_questions", "mastery_mark", "assessment_result"):
            return scope_id in (None, user["id"]) and operation in ("view", "modify")
        if resource == "assessment" and operation in ("view", "export"):
            if scope_id is None:
                return False
            return (
                self.conn.execute(
                    """
                    select 1
                    from assessment_participants p
                    join assessment_sessions a on a.id = p.assessment_id
                    where p.assessment_id = ?
                      and p.student_id = ?
                      and a.grading_status = 'published'
                    """,
                    (scope_id, user["id"]),
                ).fetchone()
                is not None
            )
        return False

    def _audit(self, actor_id, action, resource_type, resource_id, detail):
        school_id = "school-demo"
        if actor_id:
            row = self.conn.execute("select school_id from users where id = ?", (actor_id,)).fetchone()
            if row:
                school_id = row["school_id"]
        self.conn.execute(
            """
            insert into audit_events(id, school_id, actor_id, action, resource_type, resource_id, detail_json)
            values(?,?,?,?,?,?,?)
            """,
            (
                "audit-" + uuid.uuid4().hex,
                school_id,
                actor_id,
                action,
                resource_type,
                resource_id,
                json.dumps(detail, ensure_ascii=False),
            ),
        )

    def _identity_audit(self, actor_id, action, user_id, detail):
        school_id = "school-demo"
        if user_id:
            row = self.conn.execute("select school_id from users where id = ?", (user_id,)).fetchone()
            if row:
                school_id = row["school_id"]
        self.conn.execute(
            """
            insert into identity_audit_logs(id, school_id, actor_id, action, user_id, detail_json)
            values(?,?,?,?,?,?)
            """,
            (
                "identity-audit-" + uuid.uuid4().hex,
                school_id,
                actor_id,
                action,
                user_id,
                json.dumps(detail, ensure_ascii=False),
            ),
        )
