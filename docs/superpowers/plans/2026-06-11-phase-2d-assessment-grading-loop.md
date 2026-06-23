# Phase 2D Assessment Grading Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Phase 2D by shipping paper assembly, answer-card templates, OCR scan review, explicit grading revisions, wrong-question redo attempts, error-reason tagging, and configurable print exports as one verified teacher/student/admin workflow.

**Architecture:** Keep the current stdlib Python, SQLite, server-rendered HTML architecture. Add a schema-v4 migration for Phase 2D evidence tables, a focused `highschoolphysics/assessment.py` module for deterministic assembly/template/OCR helpers, repository methods for state transitions, HTTP routes for teacher/student actions, and browser-visible UI panels that expose the complete loop. Published assessment snapshots remain immutable; corrections and redo attempts are separate evidence.

**Tech Stack:** Python 3 standard library, SQLite, `unittest`, server-rendered HTML, vanilla JavaScript/CSS, optional external PaddleOCR configuration boundary with deterministic seeded/local payloads for acceptance.

---

## File Structure

**Create**

- `highschoolphysics/assessment.py`: deterministic paper assembly payload helpers, answer-card template generator, OCR payload normalization, grading revision payload helpers, redo grading helpers, export-option normalization.
- `tests/test_assessment_phase2d.py`: focused unit tests for template generation, OCR normalization, revision/redo helper behavior, and export option normalization.

**Modify**

- `highschoolphysics/db.py`: bump `SCHEMA_VERSION` to `4`; add Phase 2D evidence tables and seed data for error-reason tags/export profiles.
- `highschoolphysics/repository.py`: add paper assembly, assessment creation from paper, scan import, grading revision, redo attempt, error-reason tagging, and export-profile methods.
- `highschoolphysics/exporting.py`: support configurable wrong-book export profiles without leaking answers/analysis by default.
- `highschoolphysics/server.py`: add teacher/student/admin HTTP routes and render panels for Phase 2D.
- `highschoolphysics/assets/app.js`: add form handlers/buttons for assembly, OCR import, revision, redo, error tags, and export profile selection.
- `highschoolphysics/assets/app.css`: add layout for Phase 2D teacher/student/admin panels.
- `tests/test_database.py`: schema-v4 and legacy-upgrade coverage.
- `tests/test_workflow.py`: repository acceptance for the Phase 2D loop.
- `tests/test_http_integration.py`: route authorization and JSON contract coverage.
- `tests/test_server.py`: structural render assertions for the new visible workflow.
- `README.md`: operator notes for Phase 2D behavior and boundaries.
- `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md`: Phase 2D status and browser acceptance notes after verification.

## Fixed Contracts

Use these constants and state values consistently:

```python
SCHEMA_VERSION = 4

PHASE2D_OCR_STATUS = {
    "queued",
    "imported",
    "needs_review",
    "reviewed",
    "failed",
}

GRADING_REVISION_STATUS = {
    "draft",
    "applied",
}

REDO_STATUS = {
    "pending",
    "submitted",
    "reviewed",
    "done",
}

EXPORT_PROFILE_DEFAULTS = {
    "include_answers": False,
    "include_analysis": False,
    "include_error_reasons": True,
    "include_redo_history": True,
    "page_break": "student",
}
```

Phase 2D must preserve these invariants:

- `question_version_snapshots` are not rewritten by grading revisions.
- Published assessment grading is not rerun through `grade_assessment`; revisions are applied through explicit revision records.
- OCR import creates `student_responses` with raw/final answer, confidence, review status, and review reason.
- Low-confidence or conflicting OCR items block grading until reviewed.
- Wrong-question redo attempts are separate records and do not overwrite the original `wrong_questions` row.
- Error-reason tags are editable teacher/admin metadata attached to wrong questions.
- Configurable print exports default to hiding answers and analysis.

## Task 1: Add Phase 2D Schema V4

**Files:**

- Modify: `highschoolphysics/db.py`
- Modify: `tests/test_database.py`

- [ ] **Step 1: Write failing schema-v4 tests**

Add `test_phase_2d_schema_adds_revision_redo_reason_and_export_tables` to `tests/test_database.py`:

```python
def test_phase_2d_schema_adds_revision_redo_reason_and_export_tables(self):
    conn = connect(":memory:")
    initialize_database(conn)
    tables = {
        row["name"]
        for row in conn.execute(
            "select name from sqlite_master where type = 'table'"
        ).fetchall()
    }
    self.assertTrue(
        {
            "grading_revisions",
            "grading_revision_items",
            "redo_attempts",
            "error_reason_tags",
            "wrong_question_error_tags",
            "export_profiles",
        }.issubset(tables)
    )
    response_columns = table_columns(conn, "student_responses")
    self.assertIn("ocr_payload_json", response_columns)
    self.assertIn("reviewed_by", response_columns)
    wrong_columns = table_columns(conn, "wrong_questions")
    self.assertIn("latest_redo_status", wrong_columns)
    self.assertIn("error_reason_tag_ids_json", wrong_columns)
    self.assertEqual(conn.execute("pragma user_version").fetchone()[0], 4)
```

Add `test_phase_2d_schema_upgrades_existing_phase_2c_database`:

```python
def test_phase_2d_schema_upgrades_existing_phase_2c_database(self):
    conn = connect(":memory:")
    initialize_database(conn)
    conn.execute("pragma user_version = 3")
    conn.execute(
        """
        insert into wrong_questions(
            id, school_id, assessment_id, student_id, question_id, response_id,
            wrong_answer, correct_answer_json, score, max_score, error_reason,
            redo_status
        ) values(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "legacy-wq",
            "school-demo",
            "assess-week-1",
            "stu-1001",
            "q-newton-1",
            "resp-1001-q1",
            "B",
            '{"type":"single_choice","answer":"A"}',
            0,
            4,
            "旧错因",
            "pending",
        ),
    )
    initialize_database(conn)
    wrong = conn.execute(
        "select error_reason, latest_redo_status, error_reason_tag_ids_json from wrong_questions where id = ?",
        ("legacy-wq",),
    ).fetchone()
    self.assertEqual(wrong["error_reason"], "旧错因")
    self.assertEqual(wrong["latest_redo_status"], "pending")
    self.assertEqual(wrong["error_reason_tag_ids_json"], "[]")
    self.assertEqual(conn.execute("pragma user_version").fetchone()[0], 4)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_database.DatabaseConfigurationTests.test_phase_2d_schema_adds_revision_redo_reason_and_export_tables tests.test_database.DatabaseConfigurationTests.test_phase_2d_schema_upgrades_existing_phase_2c_database -v
```

Expected: failures naming missing Phase 2D tables/columns or user_version `3 != 4`.

- [ ] **Step 3: Implement schema v4**

In `highschoolphysics/db.py`, change:

```python
SCHEMA_VERSION = 4
```

Add these tables to `initialize_database`:

```sql
create table if not exists grading_revisions (
    id text primary key,
    school_id text not null references schools(id),
    assessment_id text not null references assessment_sessions(id),
    status text not null default 'draft',
    reason text not null,
    created_by text not null references users(id),
    applied_at text,
    created_at text default current_timestamp
);

create table if not exists grading_revision_items (
    id text primary key,
    school_id text not null references schools(id),
    revision_id text not null references grading_revisions(id),
    response_id text not null references student_responses(id),
    previous_answer text,
    revised_answer text,
    previous_score integer,
    revised_score integer not null,
    max_score integer not null,
    reason text not null,
    created_at text default current_timestamp
);

create table if not exists redo_attempts (
    id text primary key,
    school_id text not null references schools(id),
    wrong_question_id text not null references wrong_questions(id),
    student_id text not null references users(id),
    answer text not null default '',
    score integer,
    max_score integer,
    status text not null default 'submitted',
    feedback text not null default '',
    submitted_at text default current_timestamp,
    reviewed_by text references users(id),
    reviewed_at text
);

create table if not exists error_reason_tags (
    id text primary key,
    school_id text not null references schools(id),
    code text not null,
    name text not null,
    description text not null default '',
    enabled integer not null default 1,
    created_at text default current_timestamp,
    unique(school_id, code)
);

create table if not exists wrong_question_error_tags (
    wrong_question_id text not null references wrong_questions(id),
    error_reason_tag_id text not null references error_reason_tags(id),
    tagged_by text not null references users(id),
    note text not null default '',
    created_at text default current_timestamp,
    primary key(wrong_question_id, error_reason_tag_id)
);

create table if not exists export_profiles (
    id text primary key,
    school_id text not null references schools(id),
    name text not null,
    options_json text not null,
    created_by text references users(id),
    created_at text default current_timestamp
);
```

After the Phase 2C `_ensure_column` calls, add:

```python
_ensure_column(conn, "student_responses", "ocr_payload_json text not null default '{}'")
_ensure_column(conn, "student_responses", "reviewed_by text references users(id)")
_ensure_column(conn, "student_responses", "reviewed_at text")
_ensure_column(conn, "wrong_questions", "latest_redo_status text not null default 'pending'")
_ensure_column(conn, "wrong_questions", "error_reason_tag_ids_json text not null default '[]'")
```

Add indexes:

```sql
create index if not exists idx_grading_revisions_assessment
on grading_revisions(assessment_id, status);

create index if not exists idx_redo_attempts_wrong
on redo_attempts(wrong_question_id, status);

create index if not exists idx_wrong_question_error_tags_tag
on wrong_question_error_tags(error_reason_tag_id);
```

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_database.DatabaseConfigurationTests.test_phase_2d_schema_adds_revision_redo_reason_and_export_tables tests.test_database.DatabaseConfigurationTests.test_phase_2d_schema_upgrades_existing_phase_2c_database -v
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/db.py tests/test_database.py
git commit -m "feat: add phase 2d grading evidence schema"
```

## Task 2: Add Assessment Helper Module

**Files:**

- Create: `highschoolphysics/assessment.py`
- Create: `tests/test_assessment_phase2d.py`

- [ ] **Step 1: Write failing helper tests**

Create `tests/test_assessment_phase2d.py`:

```python
import unittest

from highschoolphysics.assessment import (
    default_export_options,
    generate_answer_card_template,
    normalize_ocr_items,
    score_redo_attempt,
)


class AssessmentPhase2DHelperTests(unittest.TestCase):
    def test_generate_answer_card_template_uses_snapshot_positions(self):
        snapshots = [
            {"question_id": "q1", "position": 1, "points": 4, "question_type": "single_choice"},
            {"question_id": "q2", "position": 2, "points": 6, "question_type": "fill"},
        ]

        template = generate_answer_card_template("card-new", "高二力学周测", snapshots)

        self.assertEqual(template["id"], "card-new")
        self.assertEqual(template["name"], "高二力学周测答题卡")
        self.assertEqual(template["regions"][0]["question_id"], "q1")
        self.assertEqual(template["regions"][0]["kind"], "choice")
        self.assertEqual(template["regions"][1]["kind"], "text")

    def test_normalize_ocr_items_flags_low_confidence_and_conflicts(self):
        items = normalize_ocr_items(
            [
                {"student_id": "stu-1001", "question_id": "q1", "answer": "A", "confidence": 0.91},
                {"student_id": "stu-1001", "question_id": "q2", "answer": "C", "confidence": 0.42},
                {
                    "student_id": "stu-1002",
                    "question_id": "q1",
                    "answer": "B",
                    "confidence": 0.88,
                    "conflict": True,
                },
            ],
            confidence_threshold=0.75,
        )

        self.assertEqual(items[0]["review_status"], "not_required")
        self.assertEqual(items[1]["review_status"], "required")
        self.assertEqual(items[1]["review_reason"], "low_confidence")
        self.assertEqual(items[2]["review_status"], "required")
        self.assertEqual(items[2]["review_reason"], "conflict")

    def test_score_redo_attempt_keeps_redo_separate_from_original_wrong(self):
        rule = {"type": "single_choice", "answer": "C", "points": 4}

        scored = score_redo_attempt(rule, "C")

        self.assertEqual(scored["score"], 4)
        self.assertEqual(scored["max_score"], 4)
        self.assertEqual(scored["status"], "done")

    def test_default_export_options_hide_answers_and_analysis(self):
        options = default_export_options({})

        self.assertFalse(options["include_answers"])
        self.assertFalse(options["include_analysis"])
        self.assertTrue(options["include_error_reasons"])
        self.assertEqual(options["page_break"], "student")
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_assessment_phase2d -v
```

Expected: import error for missing `highschoolphysics.assessment`.

- [ ] **Step 3: Implement helper module**

Create `highschoolphysics/assessment.py` with:

```python
from .grading import grade_answer


EXPORT_PROFILE_DEFAULTS = {
    "include_answers": False,
    "include_analysis": False,
    "include_error_reasons": True,
    "include_redo_history": True,
    "page_break": "student",
}


def _region_kind(question_type):
    if question_type in ("single_choice", "multiple_choice"):
        return "choice"
    return "text"


def generate_answer_card_template(template_id, title, snapshots):
    regions = []
    for snapshot in sorted(snapshots, key=lambda item: item["position"]):
        regions.append(
            {
                "question_id": snapshot["question_id"],
                "position": snapshot["position"],
                "points": snapshot["points"],
                "kind": _region_kind(snapshot.get("question_type", "")),
                "locator": "第%s题" % snapshot["position"],
            }
        )
    return {
        "id": template_id,
        "name": "%s答题卡" % title,
        "regions": regions,
    }


def normalize_ocr_items(items, confidence_threshold=0.75):
    normalized = []
    for index, item in enumerate(items, start=1):
        confidence = float(item.get("confidence", 1.0) or 0)
        review_status = "not_required"
        review_reason = ""
        if item.get("conflict"):
            review_status = "required"
            review_reason = "conflict"
        elif confidence < confidence_threshold:
            review_status = "required"
            review_reason = "low_confidence"
        normalized.append(
            {
                "item_index": index,
                "student_id": item["student_id"],
                "question_id": item["question_id"],
                "answer": str(item.get("answer", "")),
                "confidence": confidence,
                "review_status": review_status,
                "review_reason": review_reason,
                "raw": dict(item),
            }
        )
    return normalized


def score_redo_attempt(rule, answer):
    graded = grade_answer(rule, answer)
    return {
        "score": graded["score"],
        "max_score": graded["max_score"],
        "status": "done" if graded["correct"] else "reviewed",
        "correct": graded["correct"],
    }


def default_export_options(options):
    merged = dict(EXPORT_PROFILE_DEFAULTS)
    merged.update(options or {})
    merged["include_answers"] = bool(merged.get("include_answers"))
    merged["include_analysis"] = bool(merged.get("include_analysis"))
    merged["include_error_reasons"] = bool(merged.get("include_error_reasons"))
    merged["include_redo_history"] = bool(merged.get("include_redo_history"))
    if merged.get("page_break") not in ("student", "question", "none"):
        merged["page_break"] = "student"
    return merged
```

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_assessment_phase2d -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/assessment.py tests/test_assessment_phase2d.py
git commit -m "feat: add phase 2d assessment helpers"
```

## Task 3: Paper Assembly And Answer-Card Template Repository Flow

**Files:**

- Modify: `highschoolphysics/repository.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: Write failing repository test**

Add this test to `WorkflowTests`:

```python
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
    self.assertTrue(assessment["answer_card_template_id"].startswith("card-"))
    snapshots = self.conn.execute(
        "select count(*) as count from question_version_snapshots where assessment_id = ?",
        (assessment["id"],),
    ).fetchone()["count"]
    self.assertEqual(snapshots, 2)
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_teacher_assembles_paper_and_creates_answer_card_template -v
```

Expected: `AttributeError: 'PhysicsRepository' object has no attribute 'assemble_paper'`.

- [ ] **Step 3: Implement repository methods**

In `highschoolphysics/repository.py`, import:

```python
from .assessment import generate_answer_card_template
```

Add methods to `PhysicsRepository`:

```python
def assemble_paper(self, actor_id, title, source, question_items):
    actor = self._require_question_bank_actor(actor_id)
    paper_id = "paper-" + uuid.uuid4().hex[:12]
    self.conn.execute(
        "insert into papers(id, school_id, title, source, status) values(?,?,?,?,?)",
        (paper_id, actor["school_id"], title, source, "reviewed"),
    )
    assembled = []
    for position, item in enumerate(question_items, start=1):
        question = self.get_question(item["question_id"])
        if question is None:
            raise ResourceNotFound("Question not found: %s" % item["question_id"])
        self.conn.execute(
            "insert into paper_questions(paper_id, question_id, position, points) values(?,?,?,?)",
            (paper_id, item["question_id"], position, int(item["points"])),
        )
        assembled.append({"question_id": item["question_id"], "position": position, "points": int(item["points"])})
    self.audit(actor_id, "paper_assembled", "paper", paper_id, {"question_count": len(assembled)})
    self.conn.commit()
    return {"paper": row_to_dict(self.conn.execute("select * from papers where id = ?", (paper_id,)).fetchone()), "questions": assembled}

def create_assessment_from_paper(
    self,
    actor_id,
    paper_id,
    class_id,
    title,
    term,
    grade,
    scheduled_at,
):
    actor = self._require_assessment_class_actor(actor_id, class_id)
    paper = self.conn.execute("select * from papers where id = ?", (paper_id,)).fetchone()
    if paper is None:
        raise ResourceNotFound("Paper not found: %s" % paper_id)
    rows = self.conn.execute(
        """
        select pq.*, q.stem, q.options_json, q.answer_json, q.question_type,
               q.version
        from paper_questions pq
        join questions q on q.id = pq.question_id
        where pq.paper_id = ?
        order by pq.position
        """,
        (paper_id,),
    ).fetchall()
    if not rows:
        raise ValueError("Paper has no questions")
    assessment_id = "assess-" + uuid.uuid4().hex[:12]
    snapshots = []
    full_score = 0
    for row in rows:
        answer = loads(row["answer_json"], {})
        rule = {
            "type": row["question_type"],
            "answer": answer.get("answer", answer),
            "points": row["points"],
            "match": answer.get("match", "exact") if isinstance(answer, dict) else "exact",
            "tolerance": answer.get("tolerance", 0) if isinstance(answer, dict) else 0,
        }
        snapshot_id = "snap-" + uuid.uuid4().hex[:12]
        tag_snapshot = self.tags_for_question(row["question_id"])
        snapshots.append(
            {
                "id": snapshot_id,
                "question_id": row["question_id"],
                "position": row["position"],
                "points": row["points"],
                "question_type": row["question_type"],
                "stem": row["stem"],
                "options_json": row["options_json"],
                "answer_json": row["answer_json"],
                "grading_rule_json": dumps(rule),
                "tag_snapshot_json": dumps(tag_snapshot),
                "question_version": row["version"],
            }
        )
        full_score += row["points"]
    template_id = "card-" + uuid.uuid4().hex[:12]
    template = generate_answer_card_template(template_id, title, snapshots)
    self.conn.execute(
        "insert into answer_card_templates(id, school_id, name, template_json) values(?,?,?,?)",
        (template_id, actor["school_id"], template["name"], dumps(template)),
    )
    self.conn.execute(
        """
        insert into assessment_sessions(
            id, school_id, title, term, grade, class_id, scheduled_at, source,
            full_score, paper_id, answer_card_template_id, ontology_version_id,
            mastery_inference_version_id, status, grading_status, statistics_status
        ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            assessment_id,
            actor["school_id"],
            title,
            term,
            grade,
            class_id,
            scheduled_at,
            paper["source"],
            full_score,
            paper_id,
            template_id,
            DEFAULT_ONTOLOGY_ID,
            "mastery-manual-v1",
            "待扫描",
            "not_started",
            "not_started",
        ),
    )
    for student in self.students_for_class(class_id):
        self.conn.execute(
            "insert into assessment_participants(assessment_id, student_id, status) values(?,?,?)",
            (assessment_id, student["id"], "present"),
        )
    for snapshot in snapshots:
        self.conn.execute(
            """
            insert into question_version_snapshots(
                id, assessment_id, question_id, position, points, stem,
                options_json, answer_json, grading_rule_json, tag_snapshot_json,
                question_version, ontology_version_id
            ) values(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                snapshot["id"],
                assessment_id,
                snapshot["question_id"],
                snapshot["position"],
                snapshot["points"],
                snapshot["stem"],
                snapshot["options_json"],
                snapshot["answer_json"],
                snapshot["grading_rule_json"],
                snapshot["tag_snapshot_json"],
                snapshot["question_version"],
                DEFAULT_ONTOLOGY_ID,
            ),
        )
    self.audit(actor_id, "assessment_created_from_paper", "assessment", assessment_id, {"paper_id": paper_id})
    self.conn.commit()
    return self.assessment_detail(actor_id, assessment_id)
```

Add the class-scope and student helpers used above:

```python
def _require_assessment_class_actor(self, actor_id, class_id):
    actor = self._require_question_bank_actor(actor_id)
    if actor["role"] == "admin":
        return actor
    row = self.conn.execute(
        """
        select 1
        from teacher_classes
        where teacher_id = ? and class_id = ? and subject = 'physics'
        """,
        (actor["id"], class_id),
    ).fetchone()
    if row is None:
        raise PermissionDenied("You do not have access to this class")
    return actor

def students_for_class(self, class_id):
    return rows_to_dicts(
        self.conn.execute(
            "select * from users where class_id = ? and role = 'student' and status = 'active' order by student_no",
            (class_id,),
        ).fetchall()
    )
```

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_teacher_assembles_paper_and_creates_answer_card_template -v
```

Expected: test passes.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/repository.py tests/test_workflow.py
git commit -m "feat: assemble assessments from question bank"
```

## Task 4: OCR Scan Import And Review Blocking

**Files:**

- Modify: `highschoolphysics/repository.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: Write failing repository test**

Add this test:

```python
def test_teacher_imports_ocr_payload_and_reviews_low_confidence_items(self):
    task = self.repo.import_ocr_responses(
        actor_id=self.teacher.user["id"],
        assessment_id="assess-week-1",
        source_name="Phase 2D PaddleOCR 样例",
        recognizer="PaddleOCR",
        recognizer_version="reserved-local-v2",
        items=[
            {"student_id": "stu-1001", "question_id": "q-newton-1", "answer": "A", "confidence": 0.95},
            {"student_id": "stu-1001", "question_id": "q-newton-2", "answer": "D", "confidence": 0.41},
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
        "select review_status, reviewed_by, reviewed_at from student_responses where id = ?",
        (task["responses"][1]["id"],),
    ).fetchone()
    self.assertEqual(stored["review_status"], "resolved")
    self.assertEqual(stored["reviewed_by"], self.teacher.user["id"])
    self.assertIsNotNone(stored["reviewed_at"])
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_teacher_imports_ocr_payload_and_reviews_low_confidence_items -v
```

Expected: missing `import_ocr_responses` or missing `reviewed_by` column before Task 1.

- [ ] **Step 3: Implement import and review evidence**

Import helper:

```python
from .assessment import normalize_ocr_items
```

Add repository method:

```python
def import_ocr_responses(
    self,
    actor_id,
    assessment_id,
    source_name,
    recognizer,
    recognizer_version,
    items,
):
    assessment = self.assessment_detail(actor_id, assessment_id, operation="grade")
    batch_id = "scan-" + uuid.uuid4().hex[:12]
    normalized = normalize_ocr_items(items)
    low_count = sum(1 for item in normalized if item["review_status"] == "required")
    self.conn.execute(
        """
        insert into scan_batches(
            id, school_id, assessment_id, source_name, recognizer,
            recognizer_version, status, low_confidence_count
        ) values(?,?,?,?,?,?,?,?)
        """,
        (
            batch_id,
            assessment["school_id"],
            assessment_id,
            source_name,
            recognizer,
            recognizer_version,
            "needs_review" if low_count else "imported",
            low_count,
        ),
    )
    snapshot_rows = self.conn.execute(
        "select id, question_id from question_version_snapshots where assessment_id = ?",
        (assessment_id,),
    ).fetchall()
    snapshots = {row["question_id"]: row["id"] for row in snapshot_rows}
    responses = []
    for item in normalized:
        response_id = "resp-" + uuid.uuid4().hex[:12]
        snapshot_id = snapshots[item["question_id"]]
        self.conn.execute(
            """
            insert into student_responses(
                id, school_id, assessment_id, scan_batch_id, student_id,
                question_id, snapshot_id, raw_answer, final_answer,
                original_confidence, review_status, review_reason,
                ocr_payload_json
            ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)
            on conflict(assessment_id, student_id, question_id)
            do update set scan_batch_id = excluded.scan_batch_id,
                          raw_answer = excluded.raw_answer,
                          final_answer = excluded.final_answer,
                          original_confidence = excluded.original_confidence,
                          review_status = excluded.review_status,
                          review_reason = excluded.review_reason,
                          ocr_payload_json = excluded.ocr_payload_json,
                          updated_at = current_timestamp
            """,
            (
                response_id,
                assessment["school_id"],
                assessment_id,
                batch_id,
                item["student_id"],
                item["question_id"],
                snapshot_id,
                item["answer"],
                item["answer"],
                item["confidence"],
                item["review_status"],
                item["review_reason"],
                dumps(item["raw"]),
            ),
        )
        stored = self.conn.execute(
            """
            select * from student_responses
            where assessment_id = ? and student_id = ? and question_id = ?
            """,
            (assessment_id, item["student_id"], item["question_id"]),
        ).fetchone()
        responses.append(row_to_dict(stored))
    self.audit(actor_id, "ocr_responses_imported", "assessment", assessment_id, {"scan_batch_id": batch_id, "low_confidence_count": low_count})
    self.conn.commit()
    batch = row_to_dict(self.conn.execute("select * from scan_batches where id = ?", (batch_id,)).fetchone())
    batch["responses"] = responses
    return batch
```

Update `resolve_review_item` SQL:

```python
update student_responses
set final_answer = ?, review_status = 'resolved', review_note = ?,
    reviewed_by = ?, reviewed_at = current_timestamp,
    updated_at = current_timestamp
where id = ?
```

Use parameters `(corrected_answer, reason, actor_id, response_id)`.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_teacher_imports_ocr_payload_and_reviews_low_confidence_items -v
```

Expected: test passes.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/repository.py tests/test_workflow.py
git commit -m "feat: import and review ocr responses"
```

## Task 5: Explicit Grading Revisions For Published Assessments

**Files:**

- Modify: `highschoolphysics/repository.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_http_integration.py`

- [ ] **Step 1: Write failing repository test**

Add:

```python
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
        "select final_answer, score, overridden_by, override_reason from student_responses where id = ?",
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
```

- [ ] **Step 2: Write failing HTTP test**

Add to `tests/test_http_integration.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_teacher_applies_explicit_grading_revision_after_publication tests.test_http_integration.HttpIntegrationTests.test_teacher_can_apply_explicit_grading_revision_after_publication -v
```

Expected: missing repository method or route.

- [ ] **Step 4: Implement revision method and route**

Add `apply_grading_revision` to `PhysicsRepository`:

```python
def apply_grading_revision(self, actor_id, assessment_id, reason, items):
    assessment = self.assessment_detail(actor_id, assessment_id, operation="grade")
    revision_id = "grev-" + uuid.uuid4().hex[:12]
    self.conn.execute(
        """
        insert into grading_revisions(
            id, school_id, assessment_id, status, reason, created_by, applied_at
        ) values(?,?,?,?,?,?,current_timestamp)
        """,
        (revision_id, assessment["school_id"], assessment_id, "applied", reason, actor_id),
    )
    for item in items:
        response = self.conn.execute(
            "select * from student_responses where id = ? and assessment_id = ?",
            (item["response_id"], assessment_id),
        ).fetchone()
        if response is None:
            raise ResourceNotFound("Response not found: %s" % item["response_id"])
        item_id = "grevi-" + uuid.uuid4().hex[:12]
        self.conn.execute(
            """
            insert into grading_revision_items(
                id, school_id, revision_id, response_id, previous_answer,
                revised_answer, previous_score, revised_score, max_score, reason
            ) values(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                item_id,
                assessment["school_id"],
                revision_id,
                response["id"],
                response["final_answer"],
                item.get("revised_answer", response["final_answer"]),
                response["score"],
                int(item["revised_score"]),
                int(item["max_score"]),
                item["reason"],
            ),
        )
        self.conn.execute(
            """
            update student_responses
            set final_answer = ?, score = ?, max_score = ?,
                grading_status = case when ? = ? then 'correct' else 'wrong' end,
                overridden_by = ?, override_reason = ?,
                updated_at = current_timestamp
            where id = ?
            """,
            (
                item.get("revised_answer", response["final_answer"]),
                int(item["revised_score"]),
                int(item["max_score"]),
                int(item["revised_score"]),
                int(item["max_score"]),
                actor_id,
                item["reason"],
                response["id"],
            ),
        )
        if int(item["revised_score"]) < int(item["max_score"]):
            snapshot = self.conn.execute(
                "select answer_json from question_version_snapshots where id = ?",
                (response["snapshot_id"],),
            ).fetchone()
            self.conn.execute(
                """
                insert into wrong_questions(
                    id, school_id, assessment_id, student_id, question_id,
                    response_id, wrong_answer, correct_answer_json, score,
                    max_score, error_reason, latest_redo_status
                ) values(?,?,?,?,?,?,?,?,?,?,?,?)
                on conflict(assessment_id, student_id, question_id)
                do update set wrong_answer = excluded.wrong_answer,
                              score = excluded.score,
                              max_score = excluded.max_score,
                              error_reason = excluded.error_reason
                """,
                (
                    "wq-%s-%s-%s" % (assessment_id, response["student_id"], response["question_id"]),
                    assessment["school_id"],
                    assessment_id,
                    response["student_id"],
                    response["question_id"],
                    response["id"],
                    item.get("revised_answer", response["final_answer"]),
                    snapshot["answer_json"],
                    int(item["revised_score"]),
                    int(item["max_score"]),
                    item["reason"],
                    "pending",
                ),
            )
        else:
            self.conn.execute("delete from wrong_questions where response_id = ?", (response["id"],))
    self.audit(actor_id, "grading_revision_applied", "assessment", assessment_id, {"revision_id": revision_id, "item_count": len(items)})
    self.conn.commit()
    return row_to_dict(self.conn.execute("select * from grading_revisions where id = ?", (revision_id,)).fetchone())
```

Add route in `server.py`:

```python
elif path == "/api/teacher/grading-revision" and user["role"] in ("teacher", "admin"):
    revision = repo.apply_grading_revision(
        actor_id=user["id"],
        assessment_id=payload["assessment_id"],
        reason=payload["reason"],
        items=payload.get("items", []),
    )
    self._send_json({"ok": True, "revision": revision})
```

- [ ] **Step 5: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_teacher_applies_explicit_grading_revision_after_publication tests.test_http_integration.HttpIntegrationTests.test_teacher_can_apply_explicit_grading_revision_after_publication -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add highschoolphysics/repository.py highschoolphysics/server.py tests/test_workflow.py tests/test_http_integration.py
git commit -m "feat: apply explicit grading revisions"
```

## Task 6: Wrong-Question Redo Attempts And Error-Reason Tags

**Files:**

- Modify: `highschoolphysics/repository.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_http_integration.py`

- [ ] **Step 1: Write failing repository test**

Add:

```python
def test_student_submits_redo_and_teacher_tags_error_reason(self):
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
    reviewed = self.repo.review_redo_attempt(
        actor_id=self.teacher.user["id"],
        attempt_id=attempt["id"],
        score=wrong["max_score"],
        feedback="重做正确",
    )

    self.assertEqual(reviewed["status"], "done")
    updated_wrong = self.repo.list_wrong_questions_for_student(self.student.user["id"])[0]
    self.assertEqual(updated_wrong["latest_redo_status"], "done")
    self.assertEqual(updated_wrong["error_reason_tags"][0]["name"], "概念混淆")
    self.assertEqual(updated_wrong["redo_attempts"][0]["feedback"], "重做正确")
```

- [ ] **Step 2: Write failing HTTP test**

Add:

```python
def test_student_redo_route_and_teacher_error_tag_route(self):
    self._publish_demo_assessment()
    _, student_cookie, _ = self.server.login("stu_1001", "student123")
    conn = connect(self.db_path)
    try:
        wrong = PhysicsRepository(conn).list_wrong_questions_for_student("stu-1001")[0]
    finally:
        conn.close()

    status, _, payload = self.server.post_json(
        "/api/student/redo-attempt",
        {"wrong_question_id": wrong["id"], "answer": "C"},
        student_cookie,
    )
    self.assertEqual(status, 200)
    attempt_id = json.loads(payload)["attempt"]["id"]

    _, teacher_cookie, _ = self.server.login("teacher_li", "teacher123")
    status, _, payload = self.server.post_json(
        "/api/teacher/redo-attempt/review",
        {"attempt_id": attempt_id, "score": wrong["max_score"], "feedback": "重做正确"},
        teacher_cookie,
    )
    self.assertEqual(status, 200)
    self.assertEqual(json.loads(payload)["attempt"]["status"], "done")
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_student_submits_redo_and_teacher_tags_error_reason tests.test_http_integration.HttpIntegrationTests.test_student_redo_route_and_teacher_error_tag_route -v
```

Expected: missing repository methods/routes.

- [ ] **Step 4: Implement repository methods and routes**

Add repository methods:

```python
def create_error_reason_tag(self, actor_id, code, name, description=""):
    actor = self._require_question_bank_actor(actor_id)
    tag_id = "ert-" + uuid.uuid4().hex[:12]
    self.conn.execute(
        "insert into error_reason_tags(id, school_id, code, name, description) values(?,?,?,?,?)",
        (tag_id, actor["school_id"], code, name, description),
    )
    self.audit(actor_id, "error_reason_tag_created", "error_reason_tag", tag_id, {"code": code})
    self.conn.commit()
    return row_to_dict(self.conn.execute("select * from error_reason_tags where id = ?", (tag_id,)).fetchone())

def tag_wrong_question_error(self, actor_id, wrong_question_id, tag_ids, note=""):
    wrong = self.conn.execute("select * from wrong_questions where id = ?", (wrong_question_id,)).fetchone()
    if wrong is None:
        raise ResourceNotFound("Wrong question not found: %s" % wrong_question_id)
    self._require(actor_id, "review", "assessment", wrong["assessment_id"])
    self.conn.execute("delete from wrong_question_error_tags where wrong_question_id = ?", (wrong_question_id,))
    for tag_id in tag_ids:
        self.conn.execute(
            "insert into wrong_question_error_tags(wrong_question_id, error_reason_tag_id, tagged_by, note) values(?,?,?,?)",
            (wrong_question_id, tag_id, actor_id, note),
        )
    self.conn.execute(
        "update wrong_questions set error_reason_tag_ids_json = ? where id = ?",
        (dumps(tag_ids), wrong_question_id),
    )
    self.audit(actor_id, "wrong_question_error_tagged", "wrong_question", wrong_question_id, {"tag_ids": tag_ids})
    self.conn.commit()

def submit_redo_attempt(self, actor_id, wrong_question_id, answer):
    wrong = self.conn.execute("select * from wrong_questions where id = ?", (wrong_question_id,)).fetchone()
    if wrong is None:
        raise ResourceNotFound("Wrong question not found: %s" % wrong_question_id)
    self._require(actor_id, "modify", "mastery_mark", wrong["student_id"])
    attempt_id = "redo-" + uuid.uuid4().hex[:12]
    self.conn.execute(
        "insert into redo_attempts(id, school_id, wrong_question_id, student_id, answer, status) values(?,?,?,?,?,?)",
        (attempt_id, wrong["school_id"], wrong_question_id, wrong["student_id"], answer, "submitted"),
    )
    self.conn.execute(
        "update wrong_questions set latest_redo_status = 'submitted', redo_status = 'submitted' where id = ?",
        (wrong_question_id,),
    )
    self.audit(actor_id, "redo_attempt_submitted", "wrong_question", wrong_question_id, {"attempt_id": attempt_id})
    self.conn.commit()
    return row_to_dict(self.conn.execute("select * from redo_attempts where id = ?", (attempt_id,)).fetchone())

def review_redo_attempt(self, actor_id, attempt_id, score, feedback=""):
    attempt = self.conn.execute("select * from redo_attempts where id = ?", (attempt_id,)).fetchone()
    if attempt is None:
        raise ResourceNotFound("Redo attempt not found: %s" % attempt_id)
    wrong = self.conn.execute("select * from wrong_questions where id = ?", (attempt["wrong_question_id"],)).fetchone()
    self._require(actor_id, "review", "assessment", wrong["assessment_id"])
    max_score = int(wrong["max_score"])
    status = "done" if int(score) >= max_score else "reviewed"
    self.conn.execute(
        """
        update redo_attempts
        set score = ?, max_score = ?, status = ?, feedback = ?,
            reviewed_by = ?, reviewed_at = current_timestamp
        where id = ?
        """,
        (int(score), max_score, status, feedback, actor_id, attempt_id),
    )
    self.conn.execute(
        "update wrong_questions set latest_redo_status = ?, redo_status = ? where id = ?",
        (status, status, wrong["id"]),
    )
    self.audit(actor_id, "redo_attempt_reviewed", "wrong_question", wrong["id"], {"attempt_id": attempt_id, "status": status})
    self.conn.commit()
    return row_to_dict(self.conn.execute("select * from redo_attempts where id = ?", (attempt_id,)).fetchone())
```

Extend `list_wrong_questions_for_student` and `list_wrong_questions_for_assessment` so each wrong item includes:

```python
item["error_reason_tags"] = self.error_reason_tags_for_wrong(item["id"])
item["redo_attempts"] = self.redo_attempts_for_wrong(item["id"])
```

Add helpers:

```python
def error_reason_tags_for_wrong(self, wrong_question_id):
    return rows_to_dicts(
        self.conn.execute(
            """
            select t.*, wt.note
            from wrong_question_error_tags wt
            join error_reason_tags t on t.id = wt.error_reason_tag_id
            where wt.wrong_question_id = ? and t.enabled = 1
            order by t.name
            """,
            (wrong_question_id,),
        ).fetchall()
    )

def redo_attempts_for_wrong(self, wrong_question_id):
    return rows_to_dicts(
        self.conn.execute(
            "select * from redo_attempts where wrong_question_id = ? order by submitted_at desc, id",
            (wrong_question_id,),
        ).fetchall()
    )
```

Add routes:

```python
elif path == "/api/student/redo-attempt" and user["role"] == "student":
    attempt = repo.submit_redo_attempt(
        actor_id=user["id"],
        wrong_question_id=payload["wrong_question_id"],
        answer=payload.get("answer", ""),
    )
    self._send_json({"ok": True, "attempt": attempt})
elif path == "/api/teacher/redo-attempt/review" and user["role"] in ("teacher", "admin"):
    attempt = repo.review_redo_attempt(
        actor_id=user["id"],
        attempt_id=payload["attempt_id"],
        score=payload["score"],
        feedback=payload.get("feedback", ""),
    )
    self._send_json({"ok": True, "attempt": attempt})
```

- [ ] **Step 5: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_student_submits_redo_and_teacher_tags_error_reason tests.test_http_integration.HttpIntegrationTests.test_student_redo_route_and_teacher_error_tag_route -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add highschoolphysics/repository.py highschoolphysics/server.py tests/test_workflow.py tests/test_http_integration.py
git commit -m "feat: track redo attempts and error reasons"
```

## Task 7: Configurable Wrong-Book Exports

**Files:**

- Modify: `highschoolphysics/exporting.py`
- Modify: `highschoolphysics/repository.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: Write failing export test**

Add:

```python
def test_wrong_book_export_profile_controls_answers_analysis_and_redo_history(self):
    self._publish_demo_assessment()
    wrong = self.repo.list_wrong_questions_for_student(self.student.user["id"])[0]
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
        options={"include_answers": False, "include_analysis": False, "include_redo_history": True},
    )
    shown = build_wrong_book_html(
        self.repo,
        actor_id=self.teacher.user["id"],
        assessment_id="assess-week-1",
        options={"include_answers": True, "include_analysis": True, "include_redo_history": True},
    )

    self.assertNotIn("正确答案：", hidden)
    self.assertNotIn("解析：", hidden)
    self.assertIn("重做记录", hidden)
    self.assertIn("正确答案：", shown)
    self.assertIn("解析：", shown)
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_wrong_book_export_profile_controls_answers_analysis_and_redo_history -v
```

Expected: `build_wrong_book_html()` does not accept `options`.

- [ ] **Step 3: Implement export options**

In `exporting.py`, import:

```python
from .assessment import default_export_options
```

Change signature:

```python
def build_wrong_book_html(repo, actor_id, assessment_id, class_id=None, student_id=None, options=None):
    export_options = default_export_options(options)
```

Only render answers when enabled:

```python
if export_options["include_answers"]:
    parts.append(
        "<p>正确答案：%s</p>" % html.escape(_answer_text(wrong["correct_answer"]))
    )
```

Only render analysis when enabled:

```python
if export_options["include_analysis"] and wrong.get("analysis"):
    parts.append("<p>解析：%s</p>" % html.escape(wrong["analysis"]))
```

Render error reasons when enabled:

```python
if export_options["include_error_reasons"]:
    reasons = [tag["name"] for tag in wrong.get("error_reason_tags", [])]
    parts.append("<p><strong>错因：</strong>%s</p>" % html.escape("；".join(reasons) or wrong.get("error_reason") or "未标注"))
```

Render redo history when enabled:

```python
if export_options["include_redo_history"] and wrong.get("redo_attempts"):
    parts.append("<div class='tag-block'><strong>重做记录</strong>")
    for attempt in wrong["redo_attempts"]:
        parts.append(
            "<p>%s：%s/%s %s</p>"
            % (
                html.escape(attempt["status"]),
                attempt.get("score", ""),
                attempt.get("max_score", ""),
                html.escape(attempt.get("feedback", "")),
            )
        )
    parts.append("</div>")
```

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_wrong_book_export_profile_controls_answers_analysis_and_redo_history -v
```

Expected: test passes.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/exporting.py tests/test_workflow.py
git commit -m "feat: configure wrong book exports"
```

## Task 8: Teacher, Student, And Admin UI Surfaces

**Files:**

- Modify: `highschoolphysics/server.py`
- Modify: `highschoolphysics/assets/app.js`
- Modify: `highschoolphysics/assets/app.css`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write failing render tests**

Add to `tests/test_server.py`:

```python
def test_teacher_app_exposes_phase_2d_assessment_revision_and_redo_tools(self):
    teacher = self.auth.login("teacher_li", "teacher123", "unit-test").user
    html = render_teacher_app(teacher, self.repo.teacher_dashboard(teacher["id"]))

    self.assertIn("组卷与答题卡", html)
    self.assertIn("OCR 导入复核", html)
    self.assertIn("批改修订", html)
    self.assertIn("错因标签", html)
    self.assertIn('data-teacher-form="paper-assembly"', html)
    self.assertIn('data-teacher-form="ocr-import"', html)
    self.assertIn('data-teacher-form="grading-revision"', html)
    self.assertIn('data-teacher-form="redo-review"', html)

def test_student_app_exposes_redo_submission_form(self):
    self._publish_demo_assessment()
    student = self.auth.login("stu_1001", "student123", "unit-test").user
    html = render_student_app(student, self.repo.student_dashboard(student["id"]))

    self.assertIn("提交重做", html)
    self.assertIn('data-student-form="redo-attempt"', html)

def test_admin_app_exposes_export_profiles_and_error_reason_tags(self):
    admin = self.auth.login("admin", "admin123", "unit-test").user
    html = render_admin_app(admin, self.repo.admin_dashboard())

    self.assertIn("错因标签", html)
    self.assertIn("导出配置", html)
    self.assertIn('data-admin-form="error-reason-tag"', html)
    self.assertIn('data-admin-form="export-profile"', html)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_server.ServerRenderingTests.test_teacher_app_exposes_phase_2d_assessment_revision_and_redo_tools tests.test_server.ServerRenderingTests.test_student_app_exposes_redo_submission_form tests.test_server.ServerRenderingTests.test_admin_app_exposes_export_profiles_and_error_reason_tags -v
```

Expected: missing visible sections/forms.

- [ ] **Step 3: Implement minimal UI surfaces**

Add teacher forms in `render_teacher_app`:

```html
<section class="panel span-2 phase2d-workspace">
  <h2>组卷与答题卡</h2>
  <form data-teacher-form="paper-assembly">
    <label>试卷标题<input name="title" value="Phase 2D 力学小测" required></label>
    <label>来源<input name="source" value="校本组卷" required></label>
    <label>题目 JSON<textarea name="question_items" data-json="true" required>[{"question_id":"q-newton-1","points":4},{"question_id":"q-newton-2","points":6}]</textarea></label>
    <button type="submit">生成试卷</button>
  </form>
  <h2>OCR 导入复核</h2>
  <form data-teacher-form="ocr-import">
    <label>测评ID<input name="assessment_id" value="assess-week-1" required></label>
    <label>来源名<input name="source_name" value="PaddleOCR 导入样例" required></label>
    <label>识别器<input name="recognizer" value="PaddleOCR" required></label>
    <label>版本<input name="recognizer_version" value="reserved-local-v2" required></label>
    <label>识别项 JSON<textarea name="items" data-json="true" required>[{"student_id":"stu-1001","question_id":"q-newton-1","answer":"A","confidence":0.95},{"student_id":"stu-1001","question_id":"q-newton-2","answer":"D","confidence":0.41}]</textarea></label>
    <button type="submit">导入 OCR 结果</button>
  </form>
  <h2>批改修订</h2>
  <form data-teacher-form="grading-revision">
    <label>测评ID<input name="assessment_id" value="assess-week-1" required></label>
    <label>修订原因<input name="reason" value="发布后复查" required></label>
    <label>修订项 JSON<textarea name="items" data-json="true" required>[{"response_id":"resp-1001-q1","revised_answer":"B","revised_score":0,"max_score":4,"reason":"学生实际选择 B"}]</textarea></label>
    <button type="submit">应用修订</button>
  </form>
  <h2>错因标签</h2>
  <form data-teacher-form="error-tagging">
    <label>错题ID<input name="wrong_question_id" required></label>
    <label>标签ID JSON<textarea name="tag_ids" data-json="true" required>[]</textarea></label>
    <label>备注<input name="note" value="教师归因"></label>
    <button type="submit">保存错因</button>
  </form>
  <h2>重做复核</h2>
  <form data-teacher-form="redo-review">
    <label>重做ID<input name="attempt_id" required></label>
    <label>得分<input name="score" type="number" value="4" required></label>
    <label>反馈<input name="feedback" value="重做正确"></label>
    <button type="submit">复核重做</button>
  </form>
</section>
```

Add student redo form inside each wrong card:

```html
<form data-student-form="redo-attempt" class="redo-submit-form">
  <input type="hidden" name="wrong_question_id" value="{wrong_id}">
  <label>重做答案<input name="answer" required></label>
  <button type="submit">提交重做</button>
</form>
```

Add admin cards:

```html
<section class="panel">
  <h2>错因标签</h2>
  <form data-admin-form="error-reason-tag">
    <label>编码<input name="code" value="concept-force" required></label>
    <label>名称<input name="name" value="概念混淆" required></label>
    <label>说明<input name="description" value="力与运动关系理解错误"></label>
    <button type="submit">新增错因</button>
  </form>
</section>
<section class="panel">
  <h2>导出配置</h2>
  <form data-admin-form="export-profile">
    <label>名称<input name="name" value="默认错题本" required></label>
    <label>配置 JSON<textarea name="options" data-json="true" required>{"include_answers":false,"include_analysis":false,"include_error_reasons":true,"include_redo_history":true,"page_break":"student"}</textarea></label>
    <button type="submit">保存导出配置</button>
  </form>
</section>
```

Add endpoints to `TEACHER_FORM_ENDPOINTS`, `ADMIN_FORM_ENDPOINTS`, and a new `STUDENT_FORM_ENDPOINTS` map in `app.js`.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_server.ServerRenderingTests.test_teacher_app_exposes_phase_2d_assessment_revision_and_redo_tools tests.test_server.ServerRenderingTests.test_student_app_exposes_redo_submission_form tests.test_server.ServerRenderingTests.test_admin_app_exposes_export_profiles_and_error_reason_tags -v
node --check highschoolphysics/assets/app.js
```

Expected: tests pass and JS parses.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/server.py highschoolphysics/assets/app.js highschoolphysics/assets/app.css tests/test_server.py
git commit -m "feat: expose phase 2d workflow surfaces"
```

## Task 9: HTTP Routes For Phase 2D Workflow

**Files:**

- Modify: `highschoolphysics/server.py`
- Modify: `tests/test_http_integration.py`

- [ ] **Step 1: Write route contract test**

Add one route test method that exercises paper assembly, assessment creation, OCR import, grading revision, error-reason creation/tagging, student redo submission, and teacher redo review through HTTP:

```python
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
                {"student_id": "stu-1001", "question_id": "q-newton-1", "answer": "A", "confidence": 0.96},
                {"student_id": "stu-1001", "question_id": "q-newton-2", "answer": "D", "confidence": 0.41},
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
    conn = connect(self.db_path)
    try:
        wrong = PhysicsRepository(conn).list_wrong_questions_for_student("stu-1001")[0]
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
        {"attempt_id": attempt_id, "score": wrong["max_score"], "feedback": "重做正确"},
        teacher_cookie,
    )
    self.assertEqual(status, 200)
    self.assertEqual(json.loads(payload)["attempt"]["status"], "done")
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
python3 -m unittest tests.test_http_integration.HttpIntegrationTests.test_phase_2d_routes_execute_teacher_student_loop -v
```

Expected: first missing route returns 404.

- [ ] **Step 3: Implement routes**

Add route branches in `PhysicsHandler._do_POST` matching the test payloads and repository methods. For teacher/admin routes, rely on repository authorization plus explicit `user["role"] in ("teacher", "admin")`; for student redo, force `actor_id=user["id"]`.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m unittest tests.test_http_integration.HttpIntegrationTests.test_phase_2d_routes_execute_teacher_student_loop -v
```

Expected: route test passes.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/server.py tests/test_http_integration.py
git commit -m "feat: expose phase 2d workflow routes"
```

## Task 10: Documentation, Automated Checks, And Browser Acceptance

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md`

- [ ] **Step 1: Update README**

Document:

- teachers can assemble a paper from the question bank and generate an answer-card template;
- PaddleOCR is represented by importable OCR payloads and review queues, not bundled production OCR binaries;
- grading revisions are explicit records after publication;
- redo attempts are separate evidence from original wrong-question rows;
- error-reason tags are teacher/admin metadata;
- wrong-book exports are configurable and hide answers/analysis by default;
- Phase 2D does not calculate deterministic mastery aggregation; that remains Phase 2E.

- [ ] **Step 2: Run automated verification**

Run:

```bash
rg -n "TO[D]O|TB[D]|implement la[t]er|fill in deta[i]ls" \
  highschoolphysics tests README.md \
  docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md
PYTHONPYCACHEPREFIX=/private/tmp/hsp-phase2d-pycache python3 -m compileall -q highschoolphysics tools tests
node --check highschoolphysics/assets/app.js
python3 -m unittest discover -s tests -v
git diff --check
```

Expected: placeholder search has no output, compile exits 0, JS parses, all tests pass, diff check exits 0.

- [ ] **Step 3: Browser acceptance**

Start a fresh server:

```bash
tmpdir="$(mktemp -d /tmp/hsp-phase2d-browser.XXXXXX)"
python3 -m highschoolphysics.server --demo \
  --host 127.0.0.1 --port 8881 --db "$tmpdir/demo.sqlite3"
```

Browser acceptance at `1600x900`:

1. Teacher `teacher_li / teacher123` sees “组卷与答题卡”, “OCR 导入复核”, “批改修订”, and “错因标签”.
2. Teacher assembles a two-question paper from `q-newton-1` and `q-newton-2`.
3. Teacher creates an assessment from that paper and confirms an answer-card template is created.
4. Teacher imports OCR payload with one low-confidence item; grading blocks until review.
5. Teacher resolves the review item and publishes grading.
6. Teacher applies one explicit grading revision after publication.
7. Teacher creates/tags one error reason.
8. Student `stu_1001 / student123` sees a wrong question, submits a redo attempt, and the attempt is visible in the wrong-card UI.
9. Teacher reviews the redo attempt and the wrong question shows `latest_redo_status=done`.
10. Teacher exports wrong book with answers hidden by default and with redo history visible.
11. Admin `admin / admin123` sees error-reason and export-profile configuration surfaces.
12. No horizontal document overflow at `1600x900`.

- [ ] **Step 4: Record status**

Append a Phase 2D automated and browser acceptance note to `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md` with exact date, branch, URL, database path, test count, commands, browser evidence, residual limits, and future Phase 2E boundary.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md
git commit -m "docs: record phase 2d acceptance"
```

## Completion Gate

Phase 2D is complete only when all of the following are true:

- schema version is 4 and upgrades existing Phase 2C databases additively;
- teachers/admins can assemble papers from question-bank records;
- assessment creation snapshots assembled paper questions and creates answer-card templates;
- OCR payload import creates scan batches and student responses with review evidence;
- low-confidence or conflicting OCR responses block grading until reviewed;
- objective grading still generates wrong questions and diagnostics;
- published assessments still reject ordinary regrading;
- explicit grading revisions can update response score/answer after publication and create revision evidence;
- grading revisions update wrong-question state without rewriting snapshots;
- students can submit redo attempts as separate evidence;
- teachers/admins can review redo attempts and update `latest_redo_status`;
- teacher/admin error-reason tags can be created and attached to wrong questions;
- wrong-question lists and exports show error reasons and redo history;
- configurable exports hide answers and analysis by default and can include them when explicitly requested;
- student users cannot mutate teacher-only Phase 2D records;
- teacher users cannot mutate another class's Phase 2D records;
- admin UI exposes error-reason and export-profile configuration;
- full `unittest` suite passes;
- browser acceptance verifies teacher/student/admin flows at `1600x900`.
