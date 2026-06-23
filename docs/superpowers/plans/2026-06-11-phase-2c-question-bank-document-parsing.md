# Phase 2C Question Bank And Document Parsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a real question-bank and document-ingestion workflow that preserves original paper provenance, normalizes parser output, supports split review, and confirms up to three knowledge, ability, and literacy tags per question.

**Architecture:** Additive SQLite migrations introduce original-paper, import-batch, parsed-item, parser-config, and tag-family evidence fields without rewriting assessment snapshots. A focused `parsing.py` module normalizes deterministic text, MarkItDown, MinerU-local, and MinerU-API adapter outputs into one intermediate item shape. Repository and HTTP layers expose teacher/admin question CRUD, parser task execution, parsed-question review, tag confirmation, related-question browsing, and active-only filters while preserving Phase 2A.1 authorization boundaries.

**Tech Stack:** Python 3 standard library, SQLite, `unittest`, optional external `markitdown` and `mineru` command adapters when configured, server-rendered HTML, vanilla JavaScript/CSS.

---

## File Structure

**Create**

- `highschoolphysics/parsing.py`: parser config constants, normalized item dataclasses as dictionaries, deterministic text parser, optional MarkItDown and MinerU adapters, confidence/status helpers, and source-provenance normalization.
- `tests/test_parsing.py`: parser normalization, confidence classification, fallback policy, and malformed-output tests.

**Modify**

- `highschoolphysics/db.py`: bump schema to version 3; add original-paper, import-batch, parser-config, parsed-item schema; add Phase 2C question metadata columns; add `literacy_tags_json` to `question_tag_candidates`.
- `highschoolphysics/llm.py`: generate literacy candidates beside knowledge and ability candidates using active literacy tags.
- `highschoolphysics/repository.py`: question CRUD, question-bank search, original-paper/import-batch creation, parser task execution, parsed item review/save, tri-family candidate generation, tri-family confirmation, max-three validation, related ability/literacy browsing.
- `highschoolphysics/server.py`: teacher/admin question bank rendering and HTTP JSON routes.
- `highschoolphysics/assets/app.js`: question-bank form submissions, parse-task actions, parsed-item save, tag confirmation, filter submission, status display.
- `highschoolphysics/assets/app.css`: question-bank layout, parser queue, tag-family review grids, provenance badges, filter controls.
- `tests/test_database.py`: schema v3 and legacy upgrade coverage.
- `tests/test_workflow.py`: repository question CRUD, parse/review/save, tag-family limits, related browsing, filters, audit behavior.
- `tests/test_http_integration.py`: route authorization, validation, and JSON contracts.
- `tests/test_server.py`: teacher question-bank UI and admin parser-config rendering assertions.
- `README.md`: Phase 2C operator behavior and parser adapter notes.
- `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md`: status note only after acceptance.

## Fixed Contracts

Use these constants throughout the implementation:

```python
SCHEMA_VERSION = 3
QUESTION_TAG_FAMILY_LIMIT = 3

PARSER_MODES = {
    "deterministic_text": "内置文本解析",
    "markitdown": "Microsoft MarkItDown",
    "mineru_local": "MinerU 本地命令",
    "mineru_api": "MinerU API",
}

PARSE_STATUS = {
    "queued",
    "running",
    "parsed",
    "failed",
    "partially_parsed",
}

PARSED_ITEM_STATUS = {
    "needs_review",
    "ready",
    "saved",
    "rejected",
}
```

Normalized parser items use this shape:

```python
{
    "item_index": 1,
    "page_number": 1,
    "question_number": "1",
    "stem": "质量为 2kg 的物体受到 6N 合外力...",
    "question_type": "single_choice",
    "options": {"A": "1m/s", "B": "2m/s", "C": "6m/s", "D": "12m/s"},
    "answer": {"type": "single_choice", "answer": "C"},
    "analysis": "",
    "answer_area": {"kind": "choice", "locator": "第1页 第1题"},
    "media": [],
    "coordinates": {"page": 1, "bbox": []},
    "confidence": 0.92,
    "parser_name": "deterministic_text",
    "parser_version": "phase2c-v1",
    "warnings": []
}
```

Question-bank records must retain:

- original document identity;
- original paper title;
- page number;
- question number;
- source school or publisher when known;
- exam/use type;
- import batch;
- parser task;
- source confidence.

Formal tag confirmation must accept no more than three active tags in each family:

- `knowledge`
- `ability`
- `literacy`

## Task 1: Add Phase 2C Schema And Seedable Demo Metadata

**Files:**

- Modify: `highschoolphysics/db.py`
- Modify: `tests/test_database.py`

- [ ] **Step 1: Write failing schema-v3 tests**

Add this test helper and assertions to `tests/test_database.py`:

```python
def table_columns(conn, table):
    return {
        row["name"]
        for row in conn.execute("pragma table_info(%s)" % table).fetchall()
    }


def test_phase_2c_schema_adds_question_bank_tables_and_columns(self):
    conn = connect(":memory:")
    initialize_database(conn)
    tables = {
        row["name"]
        for row in conn.execute(
            "select name from sqlite_master where type = 'table'"
        ).fetchall()
    }
    expected = {
        "original_papers",
        "question_import_batches",
        "parsed_question_items",
        "document_parser_configs",
    }
    self.assertTrue(expected.issubset(tables))
    question_columns = table_columns(conn, "questions")
    self.assertTrue(
        {
            "original_paper_id",
            "import_batch_id",
            "parser_task_id",
            "original_page",
            "original_question_number",
            "source_school",
            "source_publisher",
            "exam_type",
            "source_confidence",
            "review_status",
        }.issubset(question_columns)
    )
    candidate_columns = table_columns(conn, "question_tag_candidates")
    self.assertIn("literacy_tags_json", candidate_columns)
    self.assertEqual(conn.execute("pragma user_version").fetchone()[0], 3)
```

Add a legacy-upgrade test that creates a minimal version-2 database by calling `initialize_database`, then manually sets `pragma user_version = 2`, inserts one existing `questions` row and one `question_tag_candidates` row, calls `initialize_database` again, and asserts the original question stem and candidate JSON are unchanged.

- [ ] **Step 2: Run the failing tests**

Run:

```bash
python3 -m unittest tests.test_database.DatabaseConfigurationTests -v
```

Expected: failures naming missing Phase 2C tables or columns.

- [ ] **Step 3: Implement additive schema migration**

In `highschoolphysics/db.py`:

```python
SCHEMA_VERSION = 3
```

After the Phase 2B migration block, add:

```python
_ensure_column(conn, "questions", "original_paper_id text references original_papers(id)")
_ensure_column(conn, "questions", "import_batch_id text references question_import_batches(id)")
_ensure_column(conn, "questions", "parser_task_id text references document_parse_tasks(id)")
_ensure_column(conn, "questions", "original_page integer")
_ensure_column(conn, "questions", "original_question_number text not null default ''")
_ensure_column(conn, "questions", "source_school text not null default ''")
_ensure_column(conn, "questions", "source_publisher text not null default ''")
_ensure_column(conn, "questions", "exam_type text not null default ''")
_ensure_column(conn, "questions", "source_confidence real not null default 1.0")
_ensure_column(conn, "questions", "review_status text not null default 'confirmed'")
_ensure_column(conn, "question_tag_candidates", "literacy_tags_json text not null default '[]'")
_ensure_column(conn, "document_parse_tasks", "original_paper_id text references original_papers(id)")
_ensure_column(conn, "document_parse_tasks", "import_batch_id text references question_import_batches(id)")
_ensure_column(conn, "document_parse_tasks", "parser_mode text not null default 'deterministic_text'")
_ensure_column(conn, "document_parse_tasks", "fallback_policy text not null default 'fail_closed'")
_ensure_column(conn, "document_parse_tasks", "source_text text not null default ''")
```

Add these tables:

```sql
create table if not exists original_papers (
    id text primary key,
    school_id text not null references schools(id),
    title text not null,
    document_name text not null,
    source_school text not null default '',
    source_publisher text not null default '',
    exam_type text not null default '',
    grade text not null default '',
    term text not null default '',
    status text not null default 'active',
    created_by text references users(id),
    created_at text default current_timestamp
);

create table if not exists question_import_batches (
    id text primary key,
    school_id text not null references schools(id),
    original_paper_id text not null references original_papers(id),
    source_file_name text not null,
    parser_mode text not null,
    status text not null default 'queued',
    item_count integer not null default 0,
    saved_count integer not null default 0,
    failure_reason text not null default '',
    created_by text references users(id),
    created_at text default current_timestamp
);

create table if not exists parsed_question_items (
    id text primary key,
    school_id text not null references schools(id),
    parse_task_id text not null references document_parse_tasks(id),
    import_batch_id text not null references question_import_batches(id),
    item_index integer not null,
    page_number integer,
    question_number text not null default '',
    stem text not null,
    question_type text not null,
    options_json text not null default '{}',
    answer_json text not null default '{}',
    analysis text not null default '',
    answer_area_json text not null default '{}',
    media_json text not null default '[]',
    coordinates_json text not null default '{}',
    confidence real not null default 0.0,
    parser_name text not null,
    parser_version text not null,
    review_status text not null default 'needs_review',
    warnings_json text not null default '[]',
    saved_question_id text references questions(id),
    created_at text default current_timestamp,
    unique(parse_task_id, item_index)
);

create table if not exists document_parser_configs (
    id text primary key,
    school_id text not null references schools(id),
    parser_mode text not null,
    enabled integer not null default 1,
    command_path text not null default '',
    api_endpoint text not null default '',
    fallback_policy text not null default 'fail_closed',
    config_json text not null default '{}',
    last_test_status text not null default '',
    created_at text default current_timestamp,
    unique(school_id, parser_mode)
);
```

Create indexes:

```sql
create index if not exists idx_questions_source_filters
on questions(school_id, grade, chapter, difficulty, quality_status, review_status);

create index if not exists idx_questions_original_paper
on questions(original_paper_id, import_batch_id);

create index if not exists idx_parsed_items_batch_status
on parsed_question_items(import_batch_id, review_status, confidence);
```

- [ ] **Step 4: Update demo seed data with original-paper metadata**

In `seed_demo_data`, insert one `original_papers` row and one `question_import_batches` row before seeded questions:

```python
conn.execute(
    """
    insert or ignore into original_papers(
        id, school_id, title, document_name, source_school,
        source_publisher, exam_type, grade, term, status, created_by
    ) values(?,?,?,?,?,?,?,?,?,?,?)
    """,
    (
        "paper-origin-week-1",
        school_id,
        "牛顿运动定律周测一原卷",
        "牛顿运动定律周测一.docx",
        "校内命题",
        "高二物理备课组",
        "weekly_quiz",
        "高二",
        "2025-2026下",
        "active",
        "user-teacher-li",
    ),
)
```

Add `original_paper_id`, `import_batch_id`, `original_page`, `original_question_number`, `source_school`, `source_publisher`, `exam_type`, `source_confidence`, and `review_status` values to the seeded `questions` inserts.

- [ ] **Step 5: Run database tests**

Run:

```bash
python3 -m unittest tests.test_database -v
```

Expected: all database tests pass.

- [ ] **Step 6: Commit**

```bash
git add highschoolphysics/db.py tests/test_database.py
git commit -m "feat: add phase 2c question bank schema"
```

## Task 2: Implement Parser Normalization And Adapter Boundaries

**Files:**

- Create: `highschoolphysics/parsing.py`
- Create: `tests/test_parsing.py`

- [ ] **Step 1: Write failing parser tests**

Create `tests/test_parsing.py`:

```python
import unittest

from highschoolphysics.parsing import (
    ParseAdapterError,
    normalize_parser_output,
    parse_deterministic_text,
    run_parser,
)


class ParsingTests(unittest.TestCase):
    def test_deterministic_text_parser_splits_numbered_questions(self):
        text = """
        1. 质量为2kg的物体受到6N合外力，2s末速度是多少？
        A. 1m/s
        B. 2m/s
        C. 6m/s
        D. 12m/s
        答案：C
        2. 简述牛顿第二定律的适用条件。
        答案：宏观低速惯性参考系
        """
        result = parse_deterministic_text(text, parser_version="test-v1")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["question_number"], "1")
        self.assertEqual(result["items"][0]["question_type"], "single_choice")
        self.assertEqual(result["items"][0]["answer"]["answer"], "C")
        self.assertEqual(result["items"][1]["question_type"], "short_answer")

    def test_normalizer_marks_low_confidence_item_needs_review(self):
        raw = {
            "items": [
                {
                    "item_index": 1,
                    "page_number": 1,
                    "question_number": "1",
                    "stem": "题干",
                    "question_type": "single_choice",
                    "options": {"A": "1", "B": "2"},
                    "answer": {"type": "single_choice", "answer": "B"},
                    "confidence": 0.61,
                }
            ],
            "parser_name": "deterministic_text",
            "parser_version": "test-v1",
        }
        normalized = normalize_parser_output(raw)
        self.assertEqual(normalized["items"][0]["review_status"], "needs_review")
        self.assertIn("low_confidence", normalized["items"][0]["warnings"])

    def test_run_parser_fail_closed_reports_missing_external_adapter(self):
        with self.assertRaises(ParseAdapterError):
            run_parser(
                parser_mode="markitdown",
                source_text="1. 测试\n答案：A",
                parser_version="test-v1",
                config={"command_path": "/path/not-present"},
                fallback_policy="fail_closed",
            )
```

- [ ] **Step 2: Run parser tests and confirm missing module failure**

Run:

```bash
python3 -m unittest tests.test_parsing -v
```

Expected: `ModuleNotFoundError: No module named 'highschoolphysics.parsing'`.

- [ ] **Step 3: Implement `parsing.py`**

Create `highschoolphysics/parsing.py` with these public functions and exception:

```python
import re
import shutil
import subprocess


class ParseAdapterError(RuntimeError):
    pass


def _blank_item(index):
    return {
        "item_index": index,
        "page_number": 1,
        "question_number": str(index),
        "stem": "",
        "question_type": "short_answer",
        "options": {},
        "answer": {},
        "analysis": "",
        "answer_area": {},
        "media": [],
        "coordinates": {"page": 1, "bbox": []},
        "confidence": 0.0,
        "parser_name": "deterministic_text",
        "parser_version": "phase2c-v1",
        "warnings": [],
    }
```

Implement deterministic splitting with:

```python
QUESTION_PATTERN = re.compile(r"(?m)^\\s*(\\d+)[\\.、]\\s+")
OPTION_PATTERN = re.compile(r"(?m)^\\s*([A-D])[\\.、]\\s*(.+)$")
ANSWER_PATTERN = re.compile(r"答案[:：]\\s*([^\\n]+)")
```

Rules:

- split each numbered block into one item;
- keep Chinese text unchanged except whitespace collapse;
- if options A-D are present, set `question_type` to `single_choice`;
- otherwise set `question_type` to `short_answer`;
- if no answer marker exists, set `answer` to `{}` and add `missing_answer` warning;
- default deterministic confidence to `0.9`, subtract `0.2` for missing answer and `0.2` for stem shorter than 8 characters.

Implement `normalize_parser_output(raw)` so every item has all fields shown in the fixed contract and sets:

```python
item["review_status"] = "ready" if item["confidence"] >= 0.8 and not item["warnings"] else "needs_review"
```

Implement `run_parser(...)`:

```python
def run_parser(parser_mode, source_text, parser_version="phase2c-v1", config=None, fallback_policy="fail_closed"):
    config = config or {}
    if parser_mode == "deterministic_text":
        return parse_deterministic_text(source_text, parser_version)
    if parser_mode in ("markitdown", "mineru_local", "mineru_api"):
        command = config.get("command_path") or parser_mode
        if not shutil.which(command):
            if fallback_policy == "deterministic_text":
                return parse_deterministic_text(source_text, parser_version)
            raise ParseAdapterError("%s adapter command is not available" % parser_mode)
        completed = subprocess.run(
            [command],
            input=source_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            raise ParseAdapterError(completed.stderr.strip() or "%s adapter failed" % parser_mode)
        return parse_deterministic_text(completed.stdout, parser_version)
    raise ParseAdapterError("Unknown parser mode: %s" % parser_mode)
```

- [ ] **Step 4: Run parser tests**

Run:

```bash
python3 -m unittest tests.test_parsing -v
```

Expected: all parser tests pass.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/parsing.py tests/test_parsing.py
git commit -m "feat: normalize document parser output"
```

## Task 3: Add Repository Question CRUD, Import Batches, And Parsed Item Saving

**Files:**

- Modify: `highschoolphysics/repository.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: Write failing repository tests**

Add tests to `tests/test_workflow.py`:

```python
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
        filters={"grade": "高二", "chapter": "运动的描述", "quality_status": "reviewed"},
    )
    self.assertEqual(updated["version"], question["version"] + 1)
    self.assertTrue(any(item["id"] == question["id"] for item in results))


def test_parse_task_saves_reviewed_question_with_original_provenance(self):
    task = self.repo.create_parse_task(
        actor_id=self.teacher.user["id"],
        paper_title="Phase 2C 解析样卷",
        document_name="phase2c-sample.txt",
        source_text="1. 质量为2kg的物体受到6N合外力，2s末速度是多少？\nA. 1m/s\nB. 2m/s\nC. 6m/s\nD. 12m/s\n答案：C",
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
        "select review_status, saved_question_id from parsed_question_items where id = ?",
        (item["id"],),
    ).fetchone()
    self.assertEqual(stored_item["review_status"], "saved")
    self.assertEqual(stored_item["saved_question_id"], saved["id"])
```

- [ ] **Step 2: Run workflow tests and confirm missing methods**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests -v
```

Expected: `AttributeError` for new repository methods.

- [ ] **Step 3: Implement question CRUD helpers**

Add methods to `PhysicsRepository`:

```python
def create_question(
    self, actor_id, stem, options, answer, analysis, question_type, source,
    grade, chapter, difficulty, media=None, scenario="", notes="",
    source_school="", source_publisher="", exam_type="", original_paper_id=None,
    import_batch_id=None, parser_task_id=None, original_page=None,
    original_question_number="", source_confidence=1.0, quality_status="draft",
    review_status="confirmed",
):
    user = self._actor(actor_id)
    if user["role"] not in ("teacher", "admin"):
        raise PermissionDenied("Teacher or admin role required")
    school_id = self.school_id_for_actor(actor_id)
    question_id = "q-" + uuid.uuid4().hex
    self.conn.execute(
        """
        insert into questions(
            id, school_id, stem, options_json, answer_json, analysis,
            question_type, source, grade, chapter, difficulty, media_json,
            scenario, quality_status, notes, original_paper_id, import_batch_id,
            parser_task_id, original_page, original_question_number,
            source_school, source_publisher, exam_type, source_confidence,
            review_status
        ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            question_id, school_id, stem, dumps(options), dumps(answer), analysis,
            question_type, source, grade, chapter, difficulty, dumps(media or []),
            scenario, quality_status, notes, original_paper_id, import_batch_id,
            parser_task_id, original_page, original_question_number,
            source_school, source_publisher, exam_type, source_confidence,
            review_status,
        ),
    )
    self.audit(actor_id, "question_created", "question", question_id, {"source": source})
    self.conn.commit()
    return self.get_question(question_id)
```

Add `update_question(...)` using the same editable fields and:

```sql
version = version + 1
```

Audit action: `question_updated`.

- [ ] **Step 4: Implement search and provenance payload**

Update `get_question` so returned payload includes:

```python
question["original_paper_title"] = None
```

When `original_paper_id` is present, query `original_papers.title` and attach it.

Implement:

```python
def search_questions(self, actor_id, filters=None):
    user = self._actor(actor_id)
    if user["role"] not in ("teacher", "admin"):
        raise PermissionDenied("Teacher or admin role required")
    filters = filters or {}
    clauses = ["q.school_id = ?"]
    params = [self.school_id_for_actor(actor_id)]
    allowed = {
        "grade": "q.grade",
        "chapter": "q.chapter",
        "difficulty": "q.difficulty",
        "quality_status": "q.quality_status",
        "review_status": "q.review_status",
        "original_paper_id": "q.original_paper_id",
        "import_batch_id": "q.import_batch_id",
    }
    for key, column in allowed.items():
        if filters.get(key):
            clauses.append("%s = ?" % column)
            params.append(filters[key])
    if filters.get("source_confidence_max") is not None:
        clauses.append("q.source_confidence <= ?")
        params.append(float(filters["source_confidence_max"]))
    if filters.get("tag_type") and filters.get("tag_id"):
        clauses.append(
            "exists (select 1 from question_tags qt where qt.question_id = q.id and qt.tag_type = ? and qt.tag_id = ? and qt.enabled = 1)"
        )
        params.extend([filters["tag_type"], filters["tag_id"]])
    rows = self.conn.execute(
        """
        select q.*, p.title as original_paper_title
        from questions q
        left join original_papers p on p.id = q.original_paper_id
        where %s
        order by q.created_at desc, q.id
        """ % " and ".join(clauses),
        params,
    ).fetchall()
    return [self._question_payload(row) for row in rows]
```

- [ ] **Step 5: Implement parse task workflow**

Import:

```python
from .parsing import ParseAdapterError, run_parser
```

Add `create_parse_task(...)`, `run_parse_task(...)`, `parsed_question_items(...)`, and `save_parsed_question(...)`.

`create_parse_task` must:

- require teacher/admin;
- create `original_papers`;
- create `question_import_batches`;
- create `document_parse_tasks` with `source_text`;
- audit `parse_task_created`;
- return task, paper, and batch IDs.

`run_parse_task` must:

- set task and batch status to `running`;
- call `run_parser`;
- insert one `parsed_question_items` row per normalized item;
- set task status to `parsed` when all items exist;
- set batch `item_count`;
- on `ParseAdapterError`, set task and batch status to `failed` and record `failure_reason`;
- audit `parse_task_completed` or `parse_task_failed`.

`save_parsed_question` must:

- read the parsed item, batch, task, and original paper;
- create a question from parsed item fields;
- copy provenance fields to `questions`;
- update item `review_status = 'saved'`;
- update batch `saved_count`;
- audit `parsed_question_saved`;
- return `get_question(saved_question_id)`.

- [ ] **Step 6: Run focused workflow tests**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_teacher_can_create_edit_and_filter_question_bank_item tests.test_workflow.WorkflowTests.test_parse_task_saves_reviewed_question_with_original_provenance -v
```

Expected: both tests pass.

- [ ] **Step 7: Commit**

```bash
git add highschoolphysics/repository.py tests/test_workflow.py
git commit -m "feat: manage question bank provenance and parse review"
```

## Task 4: Extend Candidate Generation And Formal Tag Confirmation To Literacy

**Files:**

- Modify: `highschoolphysics/llm.py`
- Modify: `highschoolphysics/repository.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: Write failing tri-family tag tests**

Add:

```python
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
        ability_tag_ids=["ab-data-processing", "ab-reasoning-argumentation"],
        literacy_tag_ids=["lit-inquiry-evidence", "lit-thinking-model"],
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
```

- [ ] **Step 2: Run focused tests and confirm failures**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_candidate_generation_and_confirmation_supports_three_tag_families tests.test_workflow.WorkflowTests.test_confirm_question_tags_rejects_more_than_three_per_family -v
```

Expected: missing `confirm_question_tags` or missing `literacy_tags` failure.

- [ ] **Step 3: Extend deterministic candidate generation**

In `highschoolphysics/llm.py`, change `generate_candidate_tags` signature to:

```python
def generate_candidate_tags(question, knowledge_nodes, ability_tags, literacy_tags, ontology_version_id):
```

Return:

```python
{
    "knowledge_tags": [...],
    "ability_tags": [...],
    "literacy_tags": [...],
    "prompt_version": "local-deterministic-v2",
    "model_version": "rules-only",
    "cache_key": ...
}
```

Use simple deterministic scoring:

- lower-case stem, chapter, analysis, and scenario into one text;
- select knowledge nodes when name appears in text or chapter appears in path;
- select ability tags by keyword groups such as `实验`, `数据`, `图像`, `方程`, `受力`, `模型`, `推理`;
- select literacy tags by keyword groups such as `证据`, `模型`, `推理`, `能量`, `物质`, `责任`;
- sort by confidence descending and stable code;
- keep at most five candidates per family in the candidate payload, because formal confirmation enforces three.

- [ ] **Step 4: Update candidate persistence**

In `generate_llm_candidates`, pass `self.literacy_tags()` and insert `literacy_tags_json`.

Update `_candidate_payload`:

```python
payload["literacy_tags"] = loads(payload.pop("literacy_tags_json"), [])
```

Legacy rows with default `[]` must still load.

- [ ] **Step 5: Implement `confirm_question_tags`**

Add:

```python
def _validate_tag_limit(self, label, ids):
    if len(ids) > 3:
        raise ValueError("At most 3 %s tags may be confirmed" % label)
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate %s tags are not allowed" % label)
```

Implement:

```python
def confirm_question_tags(
    self, actor_id, question_id, candidate_id=None,
    knowledge_node_ids=None, ability_tag_ids=None, literacy_tag_ids=None,
):
    knowledge_node_ids = knowledge_node_ids or []
    ability_tag_ids = ability_tag_ids or []
    literacy_tag_ids = literacy_tag_ids or []
    self._validate_tag_limit("knowledge", knowledge_node_ids)
    self._validate_tag_limit("ability", ability_tag_ids)
    self._validate_tag_limit("literacy", literacy_tag_ids)
    question = self.get_question(question_id)
    if question is None:
        raise ResourceNotFound("Question not found: %s" % question_id)
    user = self._actor(actor_id)
    if user["role"] not in ("teacher", "admin"):
        raise PermissionDenied("Teacher or admin role required")
    school_id = question["school_id"]
    ontology_id = self.first_active_ontology_id()
    self._assert_active_tags(school_id, "knowledge", knowledge_node_ids)
    self._assert_active_tags(school_id, "ability", ability_tag_ids)
    self._assert_active_tags(school_id, "literacy", literacy_tag_ids)
    self.conn.execute(
        "delete from question_tags where question_id = ? and source = 'teacher_review'",
        (question_id,),
    )
    for tag_type, ids in (
        ("knowledge", knowledge_node_ids),
        ("ability", ability_tag_ids),
        ("literacy", literacy_tag_ids),
    ):
        for tag_id in ids:
            self.conn.execute(
                """
                insert into question_tags(
                    id, school_id, question_id, tag_type, tag_id,
                    ontology_version_id, source, confirmed_by, candidate_id,
                    confidence, rationale
                ) values(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "tag-" + uuid.uuid4().hex,
                    school_id,
                    question_id,
                    tag_type,
                    tag_id,
                    ontology_id,
                    "teacher_review",
                    actor_id,
                    candidate_id,
                    1.0,
                    "教师审核候选标签后确认",
                ),
            )
    if candidate_id:
        self.conn.execute(
            """
            update question_tag_candidates
            set status = 'approved', reviewed_by = ?, reviewed_at = current_timestamp
            where id = ?
            """,
            (actor_id, candidate_id),
        )
    self.audit(
        actor_id,
        "question_tags_confirmed",
        "question",
        question_id,
        {
            "candidate_id": candidate_id,
            "knowledge_node_ids": knowledge_node_ids,
            "ability_tag_ids": ability_tag_ids,
            "literacy_tag_ids": literacy_tag_ids,
        },
    )
    self.conn.commit()
    return self.get_question_tags(question_id)
```

Keep existing `approve_candidate_tags(...)` as a compatibility wrapper that calls `confirm_question_tags` with empty literacy IDs.

- [ ] **Step 6: Update tag readers for literacy**

Update `get_question_tags` and `tags_for_question` joins to include `literacy_tags`.

For literacy:

```sql
left join literacy_tags lt on qt.tag_type = 'literacy' and lt.id = qt.tag_id
```

Active condition must include `lt.enabled = 1 and lt.deleted_at is null`.

- [ ] **Step 7: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_candidate_generation_and_confirmation_supports_three_tag_families tests.test_workflow.WorkflowTests.test_confirm_question_tags_rejects_more_than_three_per_family tests.test_workflow.WorkflowTests.test_llm_candidate_review_is_required_before_formal_question_tags -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add highschoolphysics/llm.py highschoolphysics/repository.py tests/test_workflow.py
git commit -m "feat: confirm question tags across three families"
```

## Task 5: Add Related Browsing And Question-Bank Filters For All Tag Families

**Files:**

- Modify: `highschoolphysics/repository.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: Write failing related/filter tests**

Add:

```python
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
    ability_related = self.repo.related_questions_for_ability("ab-force-analysis")
    literacy_related = self.repo.related_questions_for_literacy("lit-thinking-model")
    filtered = self.repo.search_questions(
        actor_id=self.teacher.user["id"],
        filters={"tag_type": "literacy", "tag_id": "lit-thinking-model"},
    )
    self.assertTrue(any(item["id"] == "q-newton-1" for item in ability_related))
    self.assertTrue(any(item["id"] == "q-newton-1" for item in literacy_related))
    self.assertTrue(any(item["id"] == "q-newton-1" for item in filtered))
```

- [ ] **Step 2: Run focused test and confirm missing methods**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_related_questions_and_filters_cover_ability_and_literacy -v
```

Expected: `AttributeError` for missing related methods.

- [ ] **Step 3: Implement related question methods**

Add:

```python
def _related_questions_for_tag(self, tag_type, tag_id):
    rows = self.conn.execute(
        """
        select distinct q.id, q.stem, q.question_type, q.difficulty,
               q.chapter, q.grade, q.quality_status
        from questions q
        join question_tags qt on qt.question_id = q.id
        where qt.tag_type = ?
          and qt.tag_id = ?
          and qt.enabled = 1
        order by q.grade, q.chapter, q.id
        """,
        (tag_type, tag_id),
    ).fetchall()
    return rows_to_dicts(rows)


def related_questions_for_ability(self, ability_tag_id):
    return self._related_questions_for_tag("ability", ability_tag_id)


def related_questions_for_literacy(self, literacy_tag_id):
    return self._related_questions_for_tag("literacy", literacy_tag_id)
```

- [ ] **Step 4: Enrich admin/teacher dashboard data**

In `teacher_dashboard`, include:

```python
"question_bank": self.search_questions(actor_id, {}),
"ability_tags": self.ability_tags(),
"literacy_tags": self.literacy_tags(),
"parse_tasks": self.list_parse_tasks(actor_id),
"parsed_items": self.parsed_question_items(actor_id, status="needs_review"),
```

If `teacher_dashboard` already includes some of these keys, preserve existing names and add missing keys only.

- [ ] **Step 5: Run workflow tests**

Run:

```bash
python3 -m unittest tests.test_workflow -v
```

Expected: all workflow tests pass.

- [ ] **Step 6: Commit**

```bash
git add highschoolphysics/repository.py tests/test_workflow.py
git commit -m "feat: filter question bank by tag family"
```

## Task 6: Add HTTP JSON Contracts For Question Bank And Parsing

**Files:**

- Modify: `highschoolphysics/server.py`
- Modify: `tests/test_http_integration.py`

- [ ] **Step 1: Write failing HTTP integration tests**

Add tests covering:

```python
def test_question_bank_routes_require_teacher_or_admin_and_save_question(self):
    teacher_status, teacher_cookie, _ = self.live.login("teacher_li", "teacher123")
    student_status, student_cookie, _ = self.live.login("stu_1001", "student123")
    self.assertEqual(teacher_status, 303)
    self.assertEqual(student_status, 303)
    status, _, payload = self.live.post_json(
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
    self.assertEqual(status, 200)
    self.assertIn(b"question", payload)
    forbidden, _, _ = self.live.post_json(
        "/api/teacher/question",
        {"stem": "学生不应创建"},
        student_cookie,
    )
    self.assertEqual(forbidden, 404)
```

Add a parse route test:

```python
def test_parse_task_routes_create_run_and_save_item(self):
    _, cookie, _ = self.live.login("teacher_li", "teacher123")
    status, _, payload = self.live.post_json(
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
    created = json.loads(payload.decode("utf-8"))
    task_id = created["task"]["id"]
    status, _, payload = self.live.post_json(
        "/api/teacher/parse-task/run",
        {"task_id": task_id},
        cookie,
    )
    self.assertEqual(status, 200)
    parsed = json.loads(payload.decode("utf-8"))
    item_id = parsed["result"]["items"][0]["id"]
    status, _, payload = self.live.post_json(
        "/api/teacher/parsed-question/save",
        {"parsed_item_id": item_id, "chapter": "运动的描述", "difficulty": "easy"},
        cookie,
    )
    self.assertEqual(status, 200)
    self.assertIn(b"question", payload)
```

- [ ] **Step 2: Run HTTP tests and confirm route failures**

Run:

```bash
python3 -m unittest tests.test_http_integration -v
```

Expected: new route tests fail with 404 or missing fields.

- [ ] **Step 3: Add route branches**

In `PhysicsHandler.do_POST`, add branches before the final not-found:

```python
elif path == "/api/teacher/question" and user["role"] in ("teacher", "admin"):
    result = repo.create_question(
        actor_id=user["id"],
        stem=payload["stem"],
        options=payload.get("options", {}),
        answer=payload.get("answer", {}),
        analysis=payload.get("analysis", ""),
        question_type=payload["question_type"],
        source=payload.get("source", "教师录入"),
        grade=payload["grade"],
        chapter=payload["chapter"],
        difficulty=payload["difficulty"],
        media=payload.get("media", []),
        scenario=payload.get("scenario", ""),
        notes=payload.get("notes", ""),
        source_school=payload.get("source_school", ""),
        source_publisher=payload.get("source_publisher", ""),
        exam_type=payload.get("exam_type", ""),
    )
    self._send_json({"ok": True, "message": "题目已保存", "question": result})
```

Add:

- `/api/teacher/question/update`
- `/api/teacher/parse-task`
- `/api/teacher/parse-task/run`
- `/api/teacher/parsed-question/save`
- `/api/teacher/question-tags/confirm`

All branches must require `user["role"] in ("teacher", "admin")`. Missing required fields should flow through the existing structured 400 contract.

- [ ] **Step 4: Run HTTP tests**

Run:

```bash
python3 -m unittest tests.test_http_integration -v
```

Expected: all integration tests pass.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/server.py tests/test_http_integration.py
git commit -m "feat: expose question bank parsing endpoints"
```

## Task 7: Render Teacher Question Bank Workspace

**Files:**

- Modify: `highschoolphysics/server.py`
- Modify: `highschoolphysics/assets/app.js`
- Modify: `highschoolphysics/assets/app.css`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write failing render tests**

Add to `tests/test_server.py`:

```python
def test_teacher_app_exposes_question_bank_parse_queue_and_tri_family_tags(self):
    teacher = self.auth.login("teacher_li", "teacher123", "unit-test").user
    html = render_teacher_app(
        teacher,
        self.repo.teacher_dashboard(teacher["id"]),
    )
    self.assertIn("真实题库", html)
    self.assertIn("新增题目", html)
    self.assertIn("原卷解析", html)
    self.assertIn("拆题复核", html)
    self.assertIn("知识标签", html)
    self.assertIn("能力标签", html)
    self.assertIn("核心素养标签", html)
    self.assertIn('data-teacher-form="question"', html)
    self.assertIn('data-teacher-form="parse-task"', html)
    self.assertIn('data-action="run-parse-task"', html)
```

- [ ] **Step 2: Run render tests and confirm missing UI**

Run:

```bash
python3 -m unittest tests.test_server.ServerRenderingTests.test_teacher_app_exposes_question_bank_parse_queue_and_tri_family_tags -v
```

Expected: assertion failures for missing content.

- [ ] **Step 3: Render question-bank panel**

In `render_teacher_app`, add a panel after the LLM candidate area:

```html
<section class="panel span-2 question-bank-workspace">
  <div class="panel-head">
    <div>
      <h2>真实题库</h2>
      <p class="explain">录入、解析、复核并确认知识/能力/核心素养标签。</p>
    </div>
  </div>
  ...
</section>
```

Render:

- create-question form with stem, options JSON textarea, answer JSON textarea, analysis, type, source, grade, chapter, difficulty;
- parse-task form with paper title, document name, parser mode, source text, source school, publisher, exam type, grade, term;
- parsed item table with save buttons;
- filter bar for grade/chapter/difficulty/quality/review/source confidence;
- question table showing original paper, question number, grade, chapter, difficulty, status;
- tag confirmation form with three select lists per family using active knowledge, ability, and literacy tags.

- [ ] **Step 4: Add JavaScript form handlers**

In `highschoolphysics/assets/app.js`, add:

```javascript
const TEACHER_FORM_ENDPOINTS = {
  "question": "/api/teacher/question",
  "question-update": "/api/teacher/question/update",
  "parse-task": "/api/teacher/parse-task",
  "parsed-question-save": "/api/teacher/parsed-question/save",
  "question-tags-confirm": "/api/teacher/question-tags/confirm"
};
```

Extend submit listener:

```javascript
const teacherForm = event.target.closest("[data-teacher-form]");
if (teacherForm) {
  event.preventDefault();
  const endpoint = TEACHER_FORM_ENDPOINTS[teacherForm.dataset.teacherForm];
  if (!endpoint) return;
  try {
    setStatus("正在保存题库数据...", "busy");
    const response = await postJSON(endpoint, formPayload(teacherForm, event.submitter));
    setStatus(response.message || "题库数据已保存，页面即将刷新。", "success");
    reloadSoon();
  } catch (error) {
    setStatus(`操作失败：${error.message}`, "error");
  }
  return;
}
```

Add click handler:

```javascript
if (action.dataset.action === "run-parse-task") {
  setStatus("正在执行解析任务...", "busy");
  const response = await postJSON("/api/teacher/parse-task/run", {
    task_id: action.dataset.taskId
  });
  setStatus(response.message || "解析任务完成，页面即将刷新。", "success");
  reloadSoon();
  return;
}
```

- [ ] **Step 5: Add CSS**

Add styles:

```css
.question-bank-workspace {}
.question-bank-grid {}
.parse-task-grid {}
.parsed-item-table {}
.tag-family-grid {}
.provenance-badge {}
.question-filter-bar {}
```

Tables may scroll inside panels; the page must not create horizontal document overflow at `1600x900`.

- [ ] **Step 6: Run render and full tests**

Run:

```bash
python3 -m unittest tests.test_server -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add highschoolphysics/server.py highschoolphysics/assets/app.js highschoolphysics/assets/app.css tests/test_server.py
git commit -m "feat: add teacher question bank workspace"
```

## Task 8: Document Phase 2C Behavior And Run Automated Acceptance

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md`

- [ ] **Step 1: Update README**

Document:

- teachers/admins can create and edit question-bank records;
- question records preserve original-paper and parser provenance;
- deterministic text parser is built in;
- MarkItDown and MinerU adapters are optional command/API integrations and fail closed unless fallback is configured;
- formal tags are capped at three per family;
- Phase 2C does not perform OCR grading or mastery calculation.

- [ ] **Step 2: Run static and automated checks**

Run:

```bash
rg -n "TO[D]O|TB[D]|implement la[t]er|fill in deta[i]ls" \
  highschoolphysics tests README.md \
  docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md
python3 -m compileall -q highschoolphysics tools tests
python3 -m unittest discover -s tests -v
git diff --check
```

Expected: placeholder search has no output, compilation exits 0, tests pass, and diff check exits 0.

- [ ] **Step 3: Verify a fresh demo database directly**

Run:

```bash
tmpdir="$(mktemp -d /tmp/hsp-phase2c-auto.XXXXXX)"
python3 -c \
  'import sys; from highschoolphysics.db import connect, initialize_database, seed_demo_data; c=connect(sys.argv[1]); initialize_database(c); seed_demo_data(c); print(c.execute("pragma user_version").fetchone()[0]); print(c.execute("select count(*) from original_papers").fetchone()[0]); print(c.execute("select count(*) from question_import_batches").fetchone()[0]); c.close()' \
  "$tmpdir/demo.sqlite3"
```

Expected:

```text
3
1
1
```

- [ ] **Step 4: Record status**

Append a Phase 2C implementation and automated acceptance note to `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md` with:

- date;
- test count;
- commands run;
- demo database path;
- remaining non-goals.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md
git commit -m "docs: record phase 2c question bank behavior"
```

## Task 9: Browser Acceptance At Classroom Viewport

**Files:**

- Modify: only files required to fix observed acceptance defects
- Record: `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md`

- [ ] **Step 1: Start a fresh demo server**

Run:

```bash
tmpdir="$(mktemp -d /tmp/hsp-phase2c-browser.XXXXXX)"
python3 -m highschoolphysics.server --demo \
  --host 127.0.0.1 --port 8880 --db "$tmpdir/demo.sqlite3"
```

Expected: server listens on `http://127.0.0.1:8880`.

- [ ] **Step 2: Verify teacher question-bank flow in Browser at `1600x900`**

Use Browser:

1. Open `http://127.0.0.1:8880/login`.
2. Log in as `teacher_li / teacher123`.
3. Confirm the teacher page shows “真实题库”, “原卷解析”, and “拆题复核”.
4. Create one manual short-answer question.
5. Create a deterministic text parse task from a numbered sample source.
6. Run the parse task.
7. Save the parsed item as a question with chapter and difficulty.
8. Generate candidates for the saved question.
9. Confirm one knowledge tag, one ability tag, and one literacy tag.
10. Filter the question bank by the confirmed literacy tag and confirm the saved question remains visible.
11. Confirm there is no horizontal document overflow at `1600x900`.

- [ ] **Step 3: Verify student/admin boundaries**

1. Log out and log in as `stu_1001 / student123`.
2. POST `/api/teacher/question` and confirm it returns not-found or forbidden without creating a question.
3. Log in as `admin / admin123`.
4. Confirm admin can see parser configuration status and active taxonomy remains intact.

- [ ] **Step 4: Run final verification**

Run:

```bash
python3 -m compileall -q highschoolphysics tools tests
python3 -m unittest discover -s tests -v
git status --short
```

Expected: compilation exits 0, all tests pass, and status contains only intentional Phase 2C changes before the acceptance commit.

- [ ] **Step 5: Record browser acceptance and commit**

Append exact date, URL, database path, viewport, verified flows, test count, and residual limits to `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md`.

```bash
git add highschoolphysics tests README.md docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md
git commit -m "docs: record phase 2c acceptance"
```

## Completion Gate

Phase 2C is complete only when all of the following are true:

- schema version is 3 and upgrades existing Phase 2B databases additively;
- question records preserve original paper, page, question number, source, exam type, import batch, parser task, and confidence metadata;
- deterministic text parser normalizes numbered questions into parsed items;
- MarkItDown and MinerU adapter modes have tested fail-closed or deterministic fallback behavior;
- teachers/admins can create and edit question-bank records;
- parser tasks create reviewable parsed items;
- reviewed parsed items can become question records without losing provenance;
- candidate generation returns knowledge, ability, and literacy suggestions;
- formal confirmation enforces at most three tags per family;
- disabled taxonomy tags cannot be confirmed;
- question-bank search filters by grade, chapter, difficulty, quality status, review status, source confidence, original paper, parser batch, and tag family;
- related questions are available from knowledge, ability, and literacy tags;
- student users cannot create or mutate question-bank/parser records;
- full `unittest` suite passes;
- browser acceptance verifies teacher/admin flows at `1600x900`.
