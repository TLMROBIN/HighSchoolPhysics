# Phase 2A.1 Security And Data Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the known authorization, publication-boundary, regrading, request-handling, password-lifecycle, SQLite concurrency, and student-navigation gaps before Phase 2B expands the dataset and user scope.

**Architecture:** Add a small domain-error layer shared by repository and HTTP code. `AuthService` remains the policy authority, HTTP handlers resolve resources and enforce route permissions, and repository methods independently enforce sensitive invariants. Real `ThreadingHTTPServer` integration tests become the primary proof for route behavior; existing repository and rendering tests remain focused unit-level evidence.

**Tech Stack:** Python 3.9+ standard library, SQLite, `http.server`, `http.client`, HTML/CSS/JavaScript, `unittest`.

---

## File Structure

- Create `highschoolphysics/errors.py`: typed domain errors with stable HTTP-facing codes.
- Create `tests/http_support.py`: reusable live-server, login, request, and second-class fixture helpers.
- Create `tests/test_http_integration.py`: real route, cookie, authorization, publication, validation, and password tests.
- Create `tests/test_database.py`: WAL, busy-timeout, and concurrent-write tests.
- Modify `highschoolphysics/auth.py`: resource policy, password change/reset, session revocation, timezone-aware expiry.
- Modify `highschoolphysics/db.py`: WAL/busy-timeout setup and first-admin bootstrap.
- Modify `highschoolphysics/repository.py`: actor-scoped queries, ownership invariants, published-state guards, and public candidate lookup.
- Modify `highschoolphysics/server.py`: structured error boundary, route authorization, password pages/routes, demo-mode login rendering.
- Modify `highschoolphysics/exporting.py`: require an already-authorized export scope and avoid diagnostics side effects for export.
- Modify `highschoolphysics/assets/app.js`: related-question tab activation and password-reset actions.
- Modify `highschoolphysics/assets/app.css`: password form and unique redo-card state styling.
- Modify `tests/test_security_auth.py`: policy and password lifecycle tests.
- Modify `tests/test_workflow.py`: repository ownership, publication visibility, and immutable-published-grading tests.
- Modify `tests/test_server.py`: demo-mode login and unique redo markup tests.
- Modify `README.md`: demo-mode startup and Phase 2A.1 behavior.

### Task 1: Establish Domain Errors And Live HTTP Test Harness

**Files:**
- Create: `highschoolphysics/errors.py`
- Create: `tests/__init__.py`
- Create: `tests/http_support.py`
- Create: `tests/test_http_integration.py`
- Modify: `highschoolphysics/server.py`
- Modify: `highschoolphysics/repository.py`

- [x] **Step 1: Write failing HTTP tests for malformed and missing payloads**

Create `tests/http_support.py` with a context-managed server using an isolated database and an ephemeral port:

```python
import http.client
import json
from pathlib import Path
import threading
from urllib.parse import urlencode

from highschoolphysics.db import initialize_database, connect, seed_demo_data
from highschoolphysics.server import PhysicsHandler
from http.server import ThreadingHTTPServer


class LivePhysicsServer:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        conn = connect(self.db_path)
        initialize_database(conn)
        seed_demo_data(conn)
        conn.close()
        handler = type("TestPhysicsHandler", (PhysicsHandler,), {"db_path": self.db_path})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def address(self):
        return self.server.server_address

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection(*self.address, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), payload
        conn.close()
        return result

    def login(self, username, password):
        body = urlencode({"username": username, "password": password})
        status, headers, payload = self.request(
            "POST",
            "/login",
            body,
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        return status, cookie, payload

    def post_json(self, path, payload, cookie):
        return self.request(
            "POST",
            path,
            json.dumps(payload).encode("utf-8"),
            {"Content-Type": "application/json", "Cookie": cookie},
        )
```

Create initial tests in `tests/test_http_integration.py`:

```python
def test_missing_required_field_returns_structured_400(self):
    status, cookie, _ = self.server.login("admin", "admin123")
    status, _, payload = self.server.post_json(
        "/api/admin/knowledge-node",
        {"name": "缺少编码"},
        cookie,
    )
    self.assertEqual(status, 400)
    self.assertEqual(json.loads(payload), {
        "error": "invalid_request",
        "message": "Missing required field: stable_code",
    })

def test_malformed_json_returns_structured_400(self):
    status, cookie, _ = self.server.login("admin", "admin123")
    status, _, payload = self.server.request(
        "POST",
        "/api/admin/knowledge-node",
        b"{",
        {"Content-Type": "application/json", "Cookie": cookie},
    )
    self.assertEqual(status, 400)
    self.assertEqual(json.loads(payload)["error"], "invalid_json")

def test_unknown_candidate_returns_structured_404(self):
    status, cookie, _ = self.server.login("teacher_li", "teacher123")
    status, _, payload = self.server.post_json(
        "/api/teacher/approve-candidate",
        {"candidate_id": "missing-candidate"},
        cookie,
    )
    self.assertEqual(status, 404)
    self.assertEqual(json.loads(payload)["error"], "not_found")
```

- [x] **Step 2: Run the HTTP tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_http_integration -v
```

Expected: both tests fail because malformed input currently raises uncaught exceptions or closes the connection.

- [x] **Step 3: Add typed domain errors**

Create `highschoolphysics/errors.py`:

```python
class DomainError(Exception):
    status = 400
    code = "invalid_request"

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class InvalidRequest(DomainError):
    status = 400
    code = "invalid_request"


class PermissionDenied(DomainError):
    status = 403
    code = "forbidden"


class ResourceNotFound(DomainError):
    status = 404
    code = "not_found"


class StateConflict(DomainError):
    status = 409
    code = "state_conflict"
```

Add a `required(payload, field)` helper and one API error boundary in `PhysicsHandler`:

```python
def required(payload, field):
    value = payload.get(field)
    if value is None or value == "":
        raise InvalidRequest("Missing required field: %s" % field)
    return value

def _send_domain_error(self, error):
    self._send_json(
        {"error": error.code, "message": error.message},
        status=error.status,
    )
```

Wrap API payload parsing and route execution so `json.JSONDecodeError` maps to `invalid_json`, `DomainError` maps to its declared status, and unexpected exceptions roll back, log through `log_error`, and return:

```json
{"error": "internal_error", "message": "Internal server error"}
```

Do not include exception strings in unexpected 500 responses.

Add public repository lookup and stop calling `_candidate_payload()` from the handler:

```python
def get_candidate(self, candidate_id):
    row = self.conn.execute(
        "select * from question_tag_candidates where id = ?",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise ResourceNotFound("Candidate not found: %s" % candidate_id)
    return self._candidate_payload(row)
```

- [x] **Step 4: Run the focused and full tests**

Run:

```bash
python3 -m unittest tests.test_http_integration -v
python3 -m unittest discover -s tests -v
```

Expected: HTTP error tests pass and all existing tests remain green.

- [x] **Step 5: Commit**

```bash
git add highschoolphysics/errors.py highschoolphysics/repository.py highschoolphysics/server.py tests/__init__.py tests/http_support.py tests/test_http_integration.py
git commit -m "test: add live HTTP error contract"
```

### Task 2: Enforce Teacher Class Scope At HTTP And Repository Boundaries

**Files:**
- Modify: `highschoolphysics/auth.py`
- Modify: `highschoolphysics/repository.py`
- Modify: `highschoolphysics/server.py`
- Modify: `highschoolphysics/exporting.py`
- Modify: `tests/http_support.py`
- Modify: `tests/test_security_auth.py`
- Modify: `tests/test_http_integration.py`
- Modify: `tests/test_workflow.py`

- [x] **Step 1: Add a second-class fixture and failing scope tests**

Add `seed_other_class(conn)` to `tests/http_support.py`. It must insert:

- `class-physics-2`
- teacher `user-teacher-wang` / `teacher_wang` / `teacher123`
- student `stu-2001`
- paper, assessment `assess-week-2`, snapshots, scan batch, and responses owned by class 2

Use the same question bank and ontology records; do not duplicate school-level questions or tags.

```python
def seed_other_class(conn):
    password_hash = hash_password("teacher123")
    conn.execute(
        "insert into class_groups(id, school_id, name, grade, school_year, status) values(?,?,?,?,?,?)",
        ("class-physics-2", "school-demo", "高二(2)班", "高二", "2025-2026", "active"),
    )
    conn.executemany(
        """
        insert into users(
            id, school_id, username, display_name, role, class_id, student_no,
            enrollment_year, status, password_hash, must_change_password
        ) values(?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                "user-teacher-wang", "school-demo", "teacher_wang", "王老师",
                "teacher", None, None, None, "active", password_hash, 0,
            ),
            (
                "stu-2001", "school-demo", "stu_2001", "赵同学",
                "student", "class-physics-2", "2001", "2024",
                "active", hash_password("student123"), 0,
            ),
        ],
    )
    conn.execute(
        "insert into teacher_classes(teacher_id, class_id, subject) values(?,?,?)",
        ("user-teacher-wang", "class-physics-2", "physics"),
    )
    conn.execute(
        """
        insert into assessment_sessions(
            id, school_id, title, term, grade, class_id, scheduled_at, source,
            full_score, paper_id, answer_card_template_id, ontology_version_id,
            mastery_inference_version_id, status, grading_status, statistics_status
        ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "assess-week-2", "school-demo", "高二二班权限测试", "2025-2026下",
            "高二", "class-physics-2", "2026-06-06 08:00:00", "周测",
            4, "paper-week-1", "card-template-1", "onto-2026-v1",
            "mastery-manual-v1", "待复核", "待复核", "not_started",
        ),
    )
    conn.execute(
        "insert into assessment_participants(assessment_id, student_id, status) values(?,?,?)",
        ("assess-week-2", "stu-2001", "present"),
    )
    question = conn.execute(
        "select * from questions where id = 'q-newton-1'"
    ).fetchone()
    conn.execute(
        """
        insert into question_version_snapshots(
            id, assessment_id, question_id, position, points, stem, options_json,
            answer_json, grading_rule_json, tag_snapshot_json, question_version,
            ontology_version_id
        ) values(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "snap-2-q1", "assess-week-2", "q-newton-1", 1, 4,
            question["stem"], question["options_json"], question["answer_json"],
            json.dumps({"type": "single_choice", "answer": "B", "points": 4}),
            "[]", question["version"], "onto-2026-v1",
        ),
    )
    conn.execute(
        """
        insert into scan_batches(
            id, school_id, assessment_id, source_name, recognizer,
            recognizer_version, status, low_confidence_count
        ) values(?,?,?,?,?,?,?,?)
        """,
        (
            "scan-week-2", "school-demo", "assess-week-2", "二班答题卡",
            "PaddleOCR", "reserved-local-v1", "待复核", 1,
        ),
    )
    conn.execute(
        """
        insert into student_responses(
            id, school_id, assessment_id, scan_batch_id, student_id, question_id,
            snapshot_id, raw_answer, final_answer, original_confidence,
            review_status, review_reason
        ) values(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "resp-2001-q1", "school-demo", "assess-week-2", "scan-week-2",
            "stu-2001", "q-newton-1", "snap-2-q1", "A", "A", 0.42,
            "required", "low_confidence",
        ),
    )
    conn.commit()
```

Add repository tests:

```python
def test_teacher_dashboard_only_contains_assigned_class(self):
    seed_other_class(self.conn)
    dashboard = self.repo.teacher_dashboard(self.teacher.user["id"])
    self.assertEqual(
        {item["class_id"] for item in dashboard["assessments"]},
        {"class-physics-1"},
    )

def test_teacher_cannot_grade_or_review_another_class(self):
    seed_other_class(self.conn)
    with self.assertRaises(PermissionDenied):
        self.repo.grade_assessment(
            self.teacher.user["id"],
            "assess-week-2",
            publish=True,
        )
    with self.assertRaises(PermissionDenied):
        self.repo.resolve_review_item(
            self.teacher.user["id"],
            "resp-2001-q1",
            "B",
            "越权尝试",
        )
```

Add this helper to `WorkflowTests` before the tests that need published data:

```python
def _publish_demo_assessment(self):
    self.repo.resolve_review_item(
        self.teacher.user["id"],
        "resp-1001-q2",
        "C",
        "测试复核",
    )
    return self.repo.grade_assessment(
        self.teacher.user["id"],
        "assess-week-1",
        publish=True,
    )
```

Add HTTP tests proving teacher Li receives `403` for:

- `/export/wrong-book/assess-week-2`
- `/api/teacher/grade`
- `/api/teacher/resolve-review`

Also assert `GET /teacher` does not contain the second class assessment title or student name.

- [x] **Step 2: Run the scope tests and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_security_auth \
  tests.test_workflow.WorkflowTests.test_teacher_dashboard_only_contains_assigned_class \
  tests.test_workflow.WorkflowTests.test_teacher_cannot_grade_or_review_another_class \
  tests.test_http_integration -v
```

Expected: tests fail because repository and routes currently trust role alone.

- [x] **Step 3: Complete policy resources in `AuthService`**

Add helpers:

```python
def user_by_id(self, user_id):
    row = self.conn.execute(
        "select * from users where id = ? and status = 'active'",
        (user_id,),
    ).fetchone()
    user = _row_to_dict(row)
    if user:
        user.pop("password_hash", None)
    return user

def can_assessment(self, user, operation, assessment_id):
    return self.can(user, operation, "assessment", assessment_id)

def can_response(self, user, operation, response_id):
    row = self.conn.execute(
        "select assessment_id from student_responses where id = ?",
        (response_id,),
    ).fetchone()
    return bool(row) and self.can(user, operation, "assessment", row["assessment_id"])
```

Limit assessment operations explicitly:

```python
if resource == "assessment":
    return operation in ("view", "review", "grade", "publish", "export") and assigned
```

Do not treat the generic `export` resource as globally teacher-accessible.

- [x] **Step 4: Add repository permission guards and scoped queries**

Add:

```python
def _actor(self, actor_id):
    user = AuthService(self.conn).user_by_id(actor_id)
    if user is None:
        raise PermissionDenied("Authentication required")
    return user

def _require(self, actor_id, operation, resource, scope_id):
    user = self._actor(actor_id)
    if not AuthService(self.conn).can(user, operation, resource, scope_id):
        raise PermissionDenied("You do not have access to this resource")
    return user
```

Apply guards before mutation or sensitive reads:

- `resolve_review_item`: resolve the response, then require assessment review permission.
- `grade_assessment`: require assessment grade permission.
- `class_diagnostics`: require assessment view permission.
- `list_wrong_questions_for_assessment`: accept `actor_id` and require assessment export/view permission.
- `students_for_assessment`: accept `actor_id` and require assessment view permission.

Change `assessment_overview(actor_id)` and `teacher_dashboard(actor_id, assessment_id=None)` so teachers only see assessments joined through `teacher_classes`; admins see all. Select the newest authorized assessment when no ID is supplied. Return empty-safe dashboard data if no assessment exists.

Remove `class_diagnostics()` from `build_wrong_book_html`; load assessment metadata through an authorized repository method so export does not write a diagnostic-view audit event.

- [x] **Step 5: Enforce the same policy in HTTP routes**

Before export, grade, or response review:

```python
auth = AuthService(conn)
if not auth.can_assessment(user, "export", assessment_id):
    raise PermissionDenied("You do not have access to this assessment")
```

For response review use `auth.can_response(...)`.

Return 403, not a combined `not_found_or_forbidden` 404, when the route exists but policy denies it.

- [x] **Step 6: Run focused and full tests**

Run:

```bash
python3 -m unittest tests.test_security_auth tests.test_workflow tests.test_http_integration -v
python3 -m unittest discover -s tests -v
```

Expected: all cross-class tests pass.

- [x] **Step 7: Commit**

```bash
git add highschoolphysics/auth.py highschoolphysics/repository.py highschoolphysics/server.py highschoolphysics/exporting.py tests/http_support.py tests/test_security_auth.py tests/test_http_integration.py tests/test_workflow.py
git commit -m "fix: enforce teacher class authorization"
```

### Task 3: Enforce Student Ownership And Published Visibility

**Files:**
- Modify: `highschoolphysics/auth.py`
- Modify: `highschoolphysics/repository.py`
- Modify: `highschoolphysics/server.py`
- Modify: `highschoolphysics/exporting.py`
- Modify: `tests/test_http_integration.py`
- Modify: `tests/test_workflow.py`

- [x] **Step 1: Write failing student isolation tests**

Repository tests:

```python
def test_student_cannot_mark_another_students_wrong_question(self):
    self._publish_demo_assessment()
    other_wrong = self.repo.list_wrong_questions_for_student(
        actor_id="stu-1002",
        student_id="stu-1002",
    )[0]
    with self.assertRaises(PermissionDenied):
        self.repo.set_mastery_mark(
            actor_id="stu-1001",
            wrong_question_id=other_wrong["id"],
            level="已掌握",
        )

def test_unpublished_results_are_hidden_from_student(self):
    self.repo.resolve_review_item(
        self.teacher.user["id"],
        "resp-1001-q2",
        "C",
        "复核",
    )
    self.repo.grade_assessment(
        self.teacher.user["id"],
        "assess-week-1",
        publish=False,
    )
    dashboard = self.repo.student_dashboard("stu-1001")
    self.assertEqual(dashboard["assessments"], [])
    self.assertEqual(dashboard["wrong_questions"], [])
```

HTTP tests:

- student 1001 requests export with `student_id=stu-1002`; response must contain only 张明 and never 李华.
- student 1001 posts another student's `wrong_question_id`; response must be 403.
- after `publish=False`, `GET /app` must not contain score or wrong-question stem.

- [x] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_workflow.WorkflowTests.test_student_cannot_mark_another_students_wrong_question \
  tests.test_workflow.WorkflowTests.test_unpublished_results_are_hidden_from_student \
  tests.test_http_integration -v
```

Expected: ownership and publication tests fail.

- [x] **Step 3: Add student ownership policy**

Extend `AuthService` with resource ownership resolution:

```python
def can_wrong_question(self, user, operation, wrong_question_id):
    row = self.conn.execute(
        "select student_id from wrong_questions where id = ?",
        (wrong_question_id,),
    ).fetchone()
    return bool(row) and self.can(
        user,
        operation,
        "mastery_mark",
        row["student_id"],
    )
```

Repository methods must accept actor identity:

```python
def list_wrong_questions_for_student(self, actor_id, student_id, knowledge_node_id=None):
    self._require(actor_id, "view", "wrong_questions", student_id)
```

`set_mastery_mark()` must resolve the wrong-question owner and require `modify` permission before writing. `set_knowledge_mastery_mark()` must require the actor to modify the requested student.

- [x] **Step 4: Filter student data by published state**

In `student_dashboard` and wrong-question student queries add:

```sql
and a.grading_status = 'published'
```

Do not expose a row, score, max score, wrong answer, correct answer, or analysis before publication.

For student export, ignore submitted `class_id` and `student_id` and force:

```python
class_id = None
student_id = user["id"]
```

Teachers and admins retain authorized filters.

- [x] **Step 5: Run focused and full tests**

Run:

```bash
python3 -m unittest tests.test_workflow tests.test_http_integration -v
python3 -m unittest discover -s tests -v
```

Expected: student isolation and publication tests pass.

- [x] **Step 6: Commit**

```bash
git add highschoolphysics/auth.py highschoolphysics/repository.py highschoolphysics/server.py highschoolphysics/exporting.py tests/test_http_integration.py tests/test_workflow.py
git commit -m "fix: isolate student records and published results"
```

### Task 4: Make Published Grading Immutable

**Files:**
- Modify: `highschoolphysics/repository.py`
- Modify: `highschoolphysics/server.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_http_integration.py`

- [x] **Step 1: Replace the permissive regrading test with failing immutability tests**

Replace `test_regrading_after_wrong_question_mastery_does_not_violate_foreign_keys` with:

```python
def test_published_assessment_cannot_be_regraded_and_mastery_survives(self):
    self._publish_demo_assessment()
    wrong = self.repo.list_wrong_questions_for_student(
        actor_id=self.student.user["id"],
        student_id=self.student.user["id"],
    )[0]
    mark = self.repo.set_mastery_mark(
        self.student.user["id"],
        wrong["id"],
        "基本掌握",
        "发布后标记",
    )

    with self.assertRaises(StateConflict):
        self.repo.grade_assessment(
            self.teacher.user["id"],
            "assess-week-1",
            publish=True,
        )

    stored = self.conn.execute(
        "select level from mastery_marks where id = ?",
        (mark["id"],),
    ).fetchone()
    self.assertEqual(stored["level"], "基本掌握")
```

Add an HTTP test expecting 409 and:

```json
{"error": "state_conflict", "message": "Published assessments require an explicit revision"}
```

- [x] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_workflow.WorkflowTests.test_published_assessment_cannot_be_regraded_and_mastery_survives \
  tests.test_http_integration -v
```

Expected: existing code silently regrades and the test fails.

- [x] **Step 3: Add the state guard before any mutation**

Immediately after loading and authorizing the assessment:

```python
if assessment["grading_status"] == "published" or assessment["status"] in ("已发布", "已归档"):
    raise StateConflict("Published assessments require an explicit revision")
```

No rows may be updated or deleted before this check. Keep the current rebuild behavior only for unpublished draft/review/graded data; Phase 2D will replace published correction behavior with revision records.

- [x] **Step 4: Run focused and full tests**

Run:

```bash
python3 -m unittest tests.test_workflow tests.test_http_integration -v
python3 -m unittest discover -s tests -v
```

Expected: repeated published grading returns 409 and stored mastery remains unchanged.

- [x] **Step 5: Commit**

```bash
git add highschoolphysics/repository.py highschoolphysics/server.py tests/test_workflow.py tests/test_http_integration.py
git commit -m "fix: protect published grading history"
```

### Task 5: Complete Temporary Password And Reset Lifecycle

**Files:**
- Modify: `highschoolphysics/auth.py`
- Modify: `highschoolphysics/errors.py`
- Modify: `highschoolphysics/server.py`
- Modify: `highschoolphysics/repository.py`
- Modify: `highschoolphysics/assets/app.js`
- Modify: `highschoolphysics/assets/app.css`
- Modify: `tests/test_security_auth.py`
- Modify: `tests/test_http_integration.py`
- Modify: `tests/test_server.py`

- [x] **Step 1: Write failing password lifecycle tests**

Unit tests:

```python
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

def test_teacher_can_only_reset_password_for_assigned_class(self):
    seed_other_class(self.conn)
    auth = AuthService(self.conn)
    teacher = auth.login("teacher_li", "teacher123", "unit-test").user
    auth.reset_password(teacher, "stu-1001", "TempOne123")
    with self.assertRaises(PermissionDenied):
        auth.reset_password(teacher, "stu-2001", "TempTwo123")
```

HTTP tests:

- imported student login redirects to `/change-password`.
- `GET /app` with that session redirects to `/change-password`.
- password change with the current temporary password redirects to `/app`.
- teacher reset for own-class student returns 200; another-class reset returns 403.
- admin reset for any user returns 200.

- [x] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_security_auth tests.test_http_integration -v
```

Expected: change/reset methods and routes do not exist.

- [x] **Step 3: Implement password policy and auditing**

In `AuthService`:

```python
def change_password(self, actor_id, user_id, current_password, new_password):
    row = self.conn.execute("select * from users where id = ?", (user_id,)).fetchone()
    if row is None:
        raise ResourceNotFound("User not found")
    if actor_id != user_id or not verify_password(current_password, row["password_hash"]):
        raise PermissionDenied("Current password is invalid")
    validate_password(new_password)
    self.conn.execute(
        "update users set password_hash = ?, must_change_password = 0 where id = ?",
        (hash_password(new_password), user_id),
    )
    self._identity_audit(actor_id, "password_changed", user_id, {})
    self.conn.commit()
```

`validate_password` requires at least 10 characters and at least one letter and digit.

`reset_password(actor_user, target_user_id, temporary_password)`:

- admin may reset any active user in the same school.
- teacher may reset active students assigned to one of the teacher's physics classes.
- set `must_change_password=1`.
- revoke all target sessions.
- write `password_reset` identity audit with actor and target, never the password.

- [x] **Step 4: Add forced-change middleware and pages**

Add `render_change_password_page(user, error="")`.

After login:

```python
target = "/change-password" if result.user["must_change_password"] else self._home_for(result.user)
```

For authenticated GET/POST requests, if `must_change_password` is true, allow only:

- `/change-password`
- `/api/password/change`
- `/logout`
- `/assets/*`

Redirect role pages to `/change-password`; API calls return 409 with code `password_change_required`.

Add:

- `POST /api/password/change`
- `POST /api/password/reset`

Add compact reset controls next to authorized students in teacher/admin views.

Teacher and admin pages use the same form contract:

```html
<form data-password-reset-form>
  <select name="target_user_id" required>{authorized_user_options}</select>
  <input name="temporary_password" type="password" minlength="10"
         placeholder="临时密码" required>
  <button type="submit">重置密码</button>
</form>
```

The JavaScript submit handler posts:

```javascript
await postJSON("/api/password/reset", {
  target_user_id: form.elements.target_user_id.value,
  temporary_password: form.elements.temporary_password.value
});
```

The teacher option list contains only students returned by the scoped teacher dashboard. The admin option list contains all active users except the current admin.

- [x] **Step 5: Run focused and full tests**

Run:

```bash
python3 -m unittest tests.test_security_auth tests.test_server tests.test_http_integration -v
python3 -m unittest discover -s tests -v
```

Expected: all password lifecycle tests pass.

- [x] **Step 6: Commit**

```bash
git add highschoolphysics/auth.py highschoolphysics/server.py highschoolphysics/repository.py highschoolphysics/assets/app.js highschoolphysics/assets/app.css tests/test_security_auth.py tests/test_http_integration.py tests/test_server.py
git commit -m "feat: enforce temporary password lifecycle"
```

### Task 6: Configure SQLite For Classroom Writes

**Files:**
- Modify: `highschoolphysics/db.py`
- Create: `tests/test_database.py`

- [x] **Step 1: Write failing connection configuration tests**

```python
def test_file_database_uses_wal_and_five_second_busy_timeout(self):
    conn = connect(self.db_path)
    self.assertEqual(conn.execute("pragma journal_mode").fetchone()[0].lower(), "wal")
    self.assertGreaterEqual(conn.execute("pragma busy_timeout").fetchone()[0], 5000)
    conn.close()
```

Add a concurrent write test with two threads, separate connections, a barrier, and 40 committed audit inserts:

```python
def test_two_connections_can_complete_concurrent_writes(self):
    conn = connect(self.db_path)
    initialize_database(conn)
    seed_demo_data(conn)
    baseline = conn.execute(
        "select count(*) from audit_events"
    ).fetchone()[0]
    conn.close()

    barrier = threading.Barrier(2)
    errors = []

    def write_events(worker):
        try:
            worker_conn = connect(self.db_path)
            barrier.wait(timeout=5)
            for index in range(20):
                worker_conn.execute(
                    """
                    insert into audit_events(
                        id, school_id, actor_id, action, resource_type,
                        resource_id, detail_json
                    ) values(?,?,?,?,?,?,?)
                    """,
                    (
                        "audit-concurrent-%s-%s" % (worker, index),
                        "school-demo", "user-admin", "concurrent_test",
                        "test", str(index), "{}",
                    ),
                )
                worker_conn.commit()
            worker_conn.close()
        except Exception as error:
            errors.append(error)

    threads = [
        threading.Thread(target=write_events, args=(worker,))
        for worker in ("a", "b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    self.assertEqual(errors, [])
    conn = connect(self.db_path)
    final = conn.execute("select count(*) from audit_events").fetchone()[0]
    conn.close()
    self.assertEqual(final - baseline, 40)
```

- [x] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_database -v
```

Expected: journal mode is `delete`; WAL assertion fails.

- [x] **Step 3: Configure connections**

In `connect()`:

```python
conn = sqlite3.connect(str(path), timeout=5.0)
conn.row_factory = sqlite3.Row
conn.execute("pragma foreign_keys = on")
conn.execute("pragma busy_timeout = 5000")
if str(path) != ":memory:":
    conn.execute("pragma journal_mode = WAL")
```

Do not set `busy_timeout=3000`.

- [x] **Step 4: Run database and full tests**

Run:

```bash
python3 -m unittest tests.test_database -v
python3 -m unittest discover -s tests -v
```

Expected: WAL and concurrent writes pass without lock failures.

- [x] **Step 5: Commit**

```bash
git add highschoolphysics/db.py tests/test_database.py
git commit -m "fix: configure sqlite for concurrent writes"
```

### Task 7: Repair Student Redo And Related-Question Navigation

**Files:**
- Modify: `highschoolphysics/server.py`
- Modify: `highschoolphysics/assets/app.js`
- Modify: `highschoolphysics/assets/app.css`
- Modify: `tests/test_server.py`

- [x] **Step 1: Write failing rendering tests**

Publish the demo assessment, render the student page, and assert:

```python
self.assertEqual(
    len(re.findall(r'\sid="wrong-question-q-newton-1"', html)),
    1,
)
self.assertEqual(
    len(re.findall(r'\sid="redo-question-q-newton-1"', html)),
    1,
)
self.assertIn('data-target-tab="wrong"', html)
self.assertIn('data-target-id="wrong-question-q-newton-1"', html)
```

Change one wrong question to `redo_status='done'` and assert it appears in the wrong-book panel but not in the redo panel.

- [x] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_server -v
```

Expected: duplicate IDs and unfiltered redo markup fail.

- [x] **Step 3: Extract card rendering with explicit prefixes**

Add:

```python
def _render_wrong_cards(wrongs, id_prefix):
    cards = []
    for wrong in wrongs:
        options = ""
        if wrong["options"]:
            options = "<p class='options'>%s</p>" % "　".join(
                "%s. %s" % (escape(key), escape(value))
                for key, value in sorted(wrong["options"].items())
            )
        mastery = wrong.get("mastery_level") or "未标记"
        card_id = "%s-question-%s" % (id_prefix, wrong["question_id"])
        cards.append(
            """
            <article class="wrong-card" id="{card_id}"
                     data-knowledge-ids="{knowledge_ids}">
              <div class="card-head"><span>{assessment}</span>
                <strong>{score}/{max_score}</strong></div>
              <h2>{stem}</h2>
              {options}
              <p>我的答案：{wrong_answer}　正确答案：{correct_answer}</p>
              <div class="answer-block">解析：{analysis}</div>
              <div class="tag-row">{knowledge}{ability}</div>
              <div class="mastery-actions" data-wrong-id="{wrong_id}">
                <button data-mastery="未掌握">未掌握</button>
                <button data-mastery="基本掌握">基本掌握</button>
                <button data-mastery="已掌握">已掌握</button>
                <button data-mastery="需教师讲解">需教师讲解</button>
                <span>{mastery}</span>
              </div>
            </article>
            """.format(
                card_id=escape(card_id),
                knowledge_ids=" ".join(
                    escape(tag["tag_id"]) for tag in wrong["knowledge_tags"]
                ),
                assessment=escape(wrong["assessment_title"]),
                score=wrong["score"],
                max_score=wrong["max_score"],
                stem=escape(wrong["stem"]),
                options=options,
                wrong_answer=escape(wrong.get("wrong_answer") or "空白"),
                correct_answer=escape(wrong["correct_answer"]),
                analysis=escape(wrong.get("analysis") or "暂无解析"),
                knowledge=_knowledge_link_pills(wrong["knowledge_tags"]),
                ability=_ability_link_pills(wrong["ability_tags"]),
                wrong_id=escape(wrong["id"]),
                mastery=escape(mastery),
            )
        )
    if not cards:
        cards.append("<article class='empty-state'>当前没有待处理题目</article>")
    return "".join(cards)
```

Render:

```python
wrong_cards = _render_wrong_cards(dashboard["wrong_questions"], "wrong")
redo_cards = _render_wrong_cards(dashboard["redo_queue"], "redo")
```

Related links should carry:

```html
<a href="#wrong-question-q-newton-1"
   data-action="open-question"
   data-target-tab="wrong"
   data-target-id="wrong-question-q-newton-1">牛顿第二定律相关题</a>
```

- [x] **Step 4: Add tab activation before scrolling**

In `app.js`, centralize:

```javascript
function activateStudentTab(name) {
  document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.tabPanel === name);
  });
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.tab === name);
  });
}
```

For `open-question`, activate the target tab, then call `scrollIntoView({ block: "center" })` on the unique target element.

- [x] **Step 5: Run focused and full tests**

Run:

```bash
python3 -m unittest tests.test_server -v
python3 -m unittest discover -s tests -v
```

Expected: rendering tests pass with unique IDs and filtered redo cards.

- [x] **Step 6: Commit**

```bash
git add highschoolphysics/server.py highschoolphysics/assets/app.js highschoolphysics/assets/app.css tests/test_server.py
git commit -m "fix: repair student redo navigation"
```

### Task 8: Make Demo Credentials Explicit

**Files:**
- Modify: `highschoolphysics/db.py`
- Modify: `highschoolphysics/server.py`
- Modify: `tests/test_server.py`
- Modify: `tests/test_http_integration.py`
- Modify: `tests/test_database.py`
- Modify: `README.md`

- [x] **Step 1: Write failing demo-mode tests**

```python
def test_login_hides_demo_credentials_by_default(self):
    html = render_login_page("", demo_mode=False)
    self.assertNotIn("teacher123", html)

def test_login_shows_demo_credentials_when_enabled(self):
    html = render_login_page("", demo_mode=True)
    self.assertIn("teacher_li / teacher123", html)
```

Extend `LivePhysicsServer.__init__` with `demo_mode=True` and `seed=True`; set both `db_path` and `demo_mode` on the generated handler class. Add HTTP tests that use `seed=True` with both `demo_mode=False` and `demo_mode=True`, proving the former hides credentials and the latter displays them.

Add a database test:

```python
def test_bootstrap_admin_creates_non_demo_login_without_seed_accounts(self):
    conn = connect(self.db_path)
    initialize_database(conn)
    user_id = bootstrap_admin(
        conn,
        username="school_admin",
        display_name="学校管理员",
        password_hash=hash_password("AdminPhysics123"),
        school_name="本地学校",
    )
    self.assertTrue(user_id.startswith("user-admin-"))
    self.assertIsNone(
        conn.execute("select id from users where username = 'teacher_li'").fetchone()
    )
    self.assertIsNotNone(
        conn.execute("select id from users where username = 'school_admin'").fetchone()
    )
    conn.close()
```

- [x] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_server tests.test_http_integration -v
```

Expected: render function has no demo-mode control.

- [x] **Step 3: Add CLI and handler configuration**

Change:

```python
def render_login_page(error="", demo_mode=False):
```

Add class setting `PhysicsHandler.demo_mode = False`. Change database startup:

```python
def ensure_database(path, demo_mode=False):
    conn = connect(path)
    initialize_database(conn)
    if demo_mode:
        seed_demo_data(conn)
    conn.close()
```

Extend `run()` and CLI:

```python
parser.add_argument("--demo", action="store_true")
parser.add_argument("--init-admin")
parser.add_argument("--admin-display-name", default="系统管理员")
parser.add_argument("--school-name", default="本地学校")
```

When `--init-admin USERNAME` is supplied:

- initialize the schema without demo data;
- prompt twice with `getpass.getpass`;
- validate the password;
- call `bootstrap_admin(...)`;
- print the created username and exit without starting the HTTP server.

`bootstrap_admin` creates the school row when needed, refuses to run if any user already exists, creates one active admin with `must_change_password=0`, and writes an `admin_bootstrapped` identity audit record. It never receives or logs the plain password.

Seed demo data and show demo credentials only when `--demo` is present. A fresh non-demo database initializes schema but does not seed known-password accounts and can be made usable through `--init-admin`.

Update README commands:

```bash
python3 -m highschoolphysics.server --demo --host 127.0.0.1 --port 8765 --db data/highschoolphysics.sqlite3
python3 -m highschoolphysics.server --db data/school.sqlite3 --init-admin school_admin
python3 -m highschoolphysics.server --host 127.0.0.1 --port 8765 --db data/school.sqlite3
```

Document the interactive bootstrap sequence and that `--init-admin` refuses to overwrite an existing user database.

- [x] **Step 4: Run focused and full tests**

Run:

```bash
python3 -m unittest tests.test_database tests.test_server tests.test_http_integration -v
python3 -m unittest discover -s tests -v
```

Expected: default login hides passwords and explicit demo mode retains the runnable demo.

- [x] **Step 5: Commit**

```bash
git add highschoolphysics/db.py highschoolphysics/server.py tests/test_database.py tests/test_server.py tests/test_http_integration.py README.md
git commit -m "fix: require explicit demo mode"
```

### Task 9: Phase Acceptance And Browser Verification

**Files:**
- Modify: `docs/superpowers/plans/2026-06-10-phase-2a1-security-data-integrity.md`
- Modify: `docs/superpowers/plans/2026-06-06-feedback-adjustment-and-next-stage.md`

- [x] **Step 1: Run the automated acceptance gate**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q highschoolphysics tests
git diff --check
```

Expected: all commands exit 0.

- [x] **Step 2: Start a fresh demo server**

Run with a throwaway database and free port:

```bash
python3 -m highschoolphysics.server \
  --demo \
  --host 127.0.0.1 \
  --port 8877 \
  --db /tmp/highschoolphysics-phase2a1.sqlite3
```

- [x] **Step 3: Verify browser-visible student behavior**

At approximately `1600x900`:

- login as `stu_1001`.
- confirm unpublished results are absent before teacher publication.
- after authorized publication, confirm the score and own wrong questions appear.
- confirm related-question links switch to the wrong-book tab and scroll to a unique card.
- confirm the redo tab only lists pending redo items.
- confirm a temporary-password student is forced to change password.

- [x] **Step 4: Verify browser-visible teacher and admin behavior**

- teacher Li sees only assigned-class assessments and students.
- a crafted request for class 2 returns 403.
- teacher can reset an own-class student's password but not class 2.
- admin can reset any user.
- repeated publication returns a visible conflict without deleting mastery.
- malformed admin form requests show the structured error message in the status bar.

- [x] **Step 5: Verify non-demo login**

Start a separate fresh non-demo database without `--demo` and confirm:

- the login page contains no seeded credentials.
- the database contains no known demo users.
- `--init-admin school_admin` creates the first administrator.
- the bootstrapped administrator can log in after the server starts normally.

- [x] **Step 6: Record completion**

Check all boxes in this plan. Change Phase 2A.1 status in the total plan to:

```markdown
Status: completed and browser-verified on 2026-06-10.
```

Record the exact test count, browser URL, and throwaway database path in a short acceptance note under the phase.

- [x] **Step 7: Commit**

```bash
git add docs/superpowers/plans/2026-06-10-phase-2a1-security-data-integrity.md docs/superpowers/plans/2026-06-06-feedback-adjustment-and-next-stage.md
git commit -m "docs: record phase 2a1 acceptance"
```

## Acceptance Record

Status: completed and browser-verified on 2026-06-10.

- Automated gate: `55` tests passed; `compileall` and `git diff --check` exited 0.
- Browser viewport: `1600x900`.
- Demo URL: `http://127.0.0.1:8877/`.
- Non-demo URL: `http://127.0.0.1:8878/`.
- Throwaway databases:
  - `/tmp/hsp-phase2a1-acceptance.kMOI9G/demo.sqlite3`
  - `/tmp/hsp-phase2a1-acceptance.kMOI9G/school.sqlite3`
- Student evidence: unpublished results were absent; published score `6/10`, own wrong card, pending redo card, unique card IDs, related-question tab activation, and temporary-password redirect were verified.
- Teacher/admin evidence: teacher scope excluded class 2; crafted cross-class export and reset returned structured 403 responses; own-class and admin resets succeeded; malformed admin payload returned structured 400; repeated publication returned 409 while the `基本掌握` mark and original wrong-question record remained.
- Non-demo evidence: login exposed no demo credentials, the database contained only `school_admin`, and the bootstrapped administrator logged in successfully.

## Plan Self-Review

- Phase 2A.1 authorization requirements map to Tasks 2 and 3.
- Published-data immutability maps to Task 4.
- Password lifecycle maps to Task 5.
- WAL and classroom-write evidence map to Task 6.
- Student redo/navigation repair maps to Task 7.
- Demo credential isolation maps to Task 8.
- HTTP, repository, compile, and browser acceptance map to Tasks 1 through 9.
- Explicit grading revisions remain in Phase 2D; Phase 2A.1 only blocks silent published regrading.
- No Phase 2B taxonomy, literacy, parser, OCR, or calculated-mastery work is pulled into this plan.
