# Phase 2E Mastery Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build deterministic per-student mastery metrics across knowledge, ability, and literacy tags from versioned assessment evidence and reviewed redo attempts.

**Architecture:** Add a small mastery helper module for threshold classification and evidence normalization, persist current per-student/tag metric snapshots in SQLite, and refresh affected students after grading, grading revisions, and redo reviews. Keep manual knowledge marks as display overrides/annotations while preserving calculated mastery state and evidence counts.

**Tech Stack:** Python 3 standard library, SQLite, unittest, existing stdlib HTTP rendering with static CSS/JS.

---

## Phase Boundary

This plan implements Phase 2E only:

- Calculate tag-level attempts, correct, wrong, blank, correct rate, and mastery state from `question_version_snapshots.tag_snapshot_json`, `student_responses`, and reviewed `redo_attempts`.
- Keep assessment attempts and redo attempts distinguishable in persisted fields.
- Track knowledge, ability, and literacy independently.
- Apply thresholds: zero attempts `未练习`; `<30%` `未掌握`; `<60%` `有困难`; `<80%` `不熟练`; `>=80%` `已掌握`.
- Show calculated mastery colors on the student knowledge graph and expose ability/literacy mastery summaries.
- Preserve manual knowledge marks as `display_mastery_state` overrides while retaining calculated fields.

This plan does not implement Phase 2F drill-down navigation for ability/literacy, nor Phase 2G teacher/admin aggregate analytics.

## Evidence Policy

- Original assessment responses count when the assessment is published.
- Assessment attempt counts are sourced from each response's immutable `tag_snapshot_json`.
- A response is correct when `score >= max_score` and max score is positive.
- A response is blank when `final_answer` is empty after trimming.
- Blank attempts are eligible attempts and reduce correct rate, but they are counted in `blank_count` rather than `wrong_count`.
- Reviewed redo attempts count when status is `reviewed` or `done` and `score` is not null.
- Redo tag evidence reuses the original wrong question response's snapshot tags.
- Combined `eligible_attempts = assessment_attempts + redo_attempts`.
- Combined `correct_count = assessment_correct + redo_correct`.
- Combined `wrong_count = assessment_wrong + redo_wrong`.
- Combined `blank_count = assessment_blank`; redo blanks are not separately represented in Phase 2E because redo records do not yet distinguish OCR blank evidence.
- `correct_rate = correct_count / eligible_attempts`.

## File Map

- Create `highschoolphysics/mastery.py`: threshold constants, `classify_mastery()`, tag normalization, and row aggregation helpers.
- Modify `highschoolphysics/db.py`: bump schema to 5, create `student_mastery_metrics`, indexes, and seed an active deterministic mastery inference version.
- Modify `highschoolphysics/repository.py`: recalculate and query metrics; call refresh after grading, revision, and redo review; enrich student dashboard.
- Modify `highschoolphysics/server.py`: render calculated state, manual override note, evidence counts, and ability/literacy mastery summaries.
- Modify `highschoolphysics/assets/app.css`: add distinct mastery state color classes for graph/list cards.
- Modify `README.md` and roadmap spec: record Phase 2E evidence policy and acceptance status.
- Test in `tests/test_mastery.py`, `tests/test_database.py`, `tests/test_workflow.py`, and `tests/test_server.py`.

### Task 1: Threshold Helper

**Files:**
- Create: `highschoolphysics/mastery.py`
- Test: `tests/test_mastery.py`

- [ ] **Step 1: Write the failing threshold tests**

```python
import unittest

from highschoolphysics.mastery import classify_mastery


class MasteryThresholdTests(unittest.TestCase):
    def test_classify_mastery_boundaries(self):
        cases = [
            (0, None, "未练习"),
            (1, 0.29, "未掌握"),
            (1, 0.30, "有困难"),
            (1, 0.59, "有困难"),
            (1, 0.60, "不熟练"),
            (1, 0.79, "不熟练"),
            (1, 0.80, "已掌握"),
        ]
        for attempts, rate, expected in cases:
            with self.subTest(attempts=attempts, rate=rate):
                self.assertEqual(classify_mastery(attempts, rate), expected)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_mastery.MasteryThresholdTests.test_classify_mastery_boundaries -v
```

Expected: import failure because `highschoolphysics.mastery` does not exist.

- [ ] **Step 3: Add the helper**

```python
MASTERY_STATES = ("未练习", "未掌握", "有困难", "不熟练", "已掌握")


def classify_mastery(eligible_attempts, correct_rate):
    if int(eligible_attempts or 0) <= 0:
        return "未练习"
    rate = float(correct_rate or 0)
    if rate < 0.30:
        return "未掌握"
    if rate < 0.60:
        return "有困难"
    if rate < 0.80:
        return "不熟练"
    return "已掌握"
```

- [ ] **Step 4: Run the test and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_mastery.MasteryThresholdTests.test_classify_mastery_boundaries -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/mastery.py tests/test_mastery.py
git commit -m "feat: add mastery threshold classifier"
```

### Task 2: Schema V5 Metric Table

**Files:**
- Modify: `highschoolphysics/db.py`
- Modify: `tests/test_database.py`

- [ ] **Step 1: Write the failing schema tests**

Add a test that initializes a fresh database and asserts:

```python
self.assertEqual(conn.execute("pragma user_version").fetchone()[0], 5)
self.assertIn("student_mastery_metrics", tables)
columns = table_columns(conn, "student_mastery_metrics")
self.assertIn("tag_type", columns)
self.assertIn("assessment_attempts", columns)
self.assertIn("redo_attempts", columns)
self.assertIn("correct_rate", columns)
self.assertIn("mastery_state", columns)
```

Add a migration test that starts from version 4 with an existing wrong question and knowledge mastery mark, runs `initialize_database(conn)`, and verifies both rows remain while `student_mastery_metrics` exists.

- [ ] **Step 2: Run schema tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_database.DatabaseConfigurationTests.test_phase_2e_schema_adds_mastery_metric_table tests.test_database.DatabaseConfigurationTests.test_phase_2e_schema_upgrades_existing_phase_2d_database -v
```

Expected: FAIL because schema version is 4 and the table does not exist.

- [ ] **Step 3: Implement schema**

Set `SCHEMA_VERSION = 5` and create:

```sql
create table if not exists student_mastery_metrics (
    id text primary key,
    school_id text not null references schools(id),
    student_id text not null references users(id),
    mastery_inference_version_id text not null references mastery_inference_versions(id),
    tag_type text not null,
    tag_id text not null,
    tag_name text not null default '',
    assessment_attempts integer not null default 0,
    assessment_correct integer not null default 0,
    assessment_wrong integer not null default 0,
    assessment_blank integer not null default 0,
    redo_attempts integer not null default 0,
    redo_correct integer not null default 0,
    redo_wrong integer not null default 0,
    eligible_attempts integer not null default 0,
    correct_count integer not null default 0,
    wrong_count integer not null default 0,
    blank_count integer not null default 0,
    correct_rate real,
    mastery_state text not null,
    calculated_at text default current_timestamp,
    unique(student_id, tag_type, tag_id)
);
```

Add indexes on `(student_id, tag_type)` and `(school_id, tag_type, tag_id)`. Seed an active `mastery-deterministic-v1` row in `mastery_inference_versions` with method describing the evidence policy.

- [ ] **Step 4: Run schema tests and verify GREEN**

Run the two schema tests above.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/db.py tests/test_database.py
git commit -m "feat: add mastery metric schema"
```

### Task 3: Repository Recalculation

**Files:**
- Modify: `highschoolphysics/mastery.py`
- Modify: `highschoolphysics/repository.py`
- Test: `tests/test_workflow.py`

- [ ] **Step 1: Write failing workflow tests**

Add tests that:

- Publish the demo assessment after resolving low-confidence response.
- Assert `repo.student_mastery_metrics(student_id, tag_type="knowledge")` returns deterministic rows from snapshot tags.
- Assert `stu-1001` has knowledge `kn-pep2019-r1-c04-s03` with `assessment_attempts=1`, `assessment_correct=1`, `redo_attempts=0`, `correct_rate=1.0`, `mastery_state="已掌握"`.
- Assert `stu-1002` has `kn-pep2019-r1-c02` with `assessment_blank=0`, `assessment_wrong=1`, `mastery_state="未掌握"`.
- Assert `stu-1003` has `kn-pep2019-r1-c02` with `assessment_blank=1`, `assessment_wrong=0`, `mastery_state="未掌握"`.
- Submit and review a correct redo for `stu-1002`'s wrong question, then assert the same tag row has `assessment_attempts=1`, `redo_attempts=1`, `correct_count=1`, `eligible_attempts=2`, `correct_rate=0.5`, and `mastery_state="有困难"`.
- Assert ability rows are present for `ab-force-analysis` and `ab-calculation`.

- [ ] **Step 2: Run the workflow tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_mastery_metrics_update_from_published_assessment_and_reviewed_redo -v
```

Expected: FAIL because repository methods and refresh logic do not exist.

- [ ] **Step 3: Implement repository aggregation**

Add:

- `recalculate_student_mastery_metrics(student_id)` that deletes current rows for the student and rebuilds from published responses and reviewed redo attempts.
- `refresh_assessment_mastery_metrics(assessment_id)` that refreshes all participants.
- `student_mastery_metrics(actor_id, student_id=None, tag_type=None)` with ownership checks.

Call refresh after `grade_assessment(... publish=True)`, `apply_grading_revision()`, and `review_redo_attempt()`.

- [ ] **Step 4: Run workflow tests and verify GREEN**

Run the target workflow test, then:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests -v
```

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/mastery.py highschoolphysics/repository.py tests/test_workflow.py
git commit -m "feat: calculate deterministic mastery metrics"
```

### Task 4: Student Dashboard Rendering

**Files:**
- Modify: `highschoolphysics/repository.py`
- Modify: `highschoolphysics/server.py`
- Modify: `highschoolphysics/assets/app.css`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write failing rendering tests**

Add tests that render `student_dashboard()` after publishing the demo assessment and assert:

- Graph nodes include a mastery color class such as `mastery-state-mastered`.
- The rendered graph text includes calculated evidence like `正确率 100%`.
- A manual knowledge mastery mark appears as an override/note without deleting calculated state.
- Ability and literacy mastery summary sections render even when literacy has no attempts.

- [ ] **Step 2: Run rendering tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_server.ServerRenderingTests.test_student_app_renders_calculated_mastery_colors_and_evidence -v
```

Expected: FAIL because current rendering only shows manual marks.

- [ ] **Step 3: Implement dashboard enrichment and CSS**

Add dashboard keys:

- `mastery_metrics`
- `ability_mastery`
- `literacy_mastery`

Enrich each knowledge node with:

- `calculated_mastery_state`
- `display_mastery_state`
- `manual_mastery_level`
- `manual_mastery_note`
- `mastery_css_class`
- `mastery_evidence_text`

Add CSS classes:

- `mastery-state-unpracticed`
- `mastery-state-not-mastered`
- `mastery-state-difficult`
- `mastery-state-rough`
- `mastery-state-mastered`

- [ ] **Step 4: Run rendering tests and verify GREEN**

Run the target server test, then all server tests.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/repository.py highschoolphysics/server.py highschoolphysics/assets/app.css tests/test_server.py
git commit -m "feat: show mastery metrics in student graph"
```

### Task 5: Documentation And Acceptance

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md`

- [ ] **Step 1: Update documentation**

Document Phase 2E's evidence policy, thresholds, and known boundary that full ability/literacy navigation remains Phase 2F.

- [ ] **Step 2: Run full verification**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/hsp-phase2e-verify python3 -m compileall -q highschoolphysics tools tests
node --check highschoolphysics/assets/app.js
python3 -m unittest discover -s tests -v
git diff --check
```

- [ ] **Step 3: Browser acceptance**

Run a throwaway server with a throwaway SQLite DB, open the student, teacher, and admin surfaces at roughly 1600x900, and verify the student graph shows calculated mastery colors/evidence.

- [ ] **Step 4: Commit docs and acceptance note**

```bash
git add README.md docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md
git commit -m "docs: record phase 2e mastery acceptance"
```

## Final Integration

After all tasks pass:

- Verify branch status is clean.
- Merge `codex/phase-2e-mastery-metrics` to `main`.
- Push `main`.
- Run `git rev-list --left-right --count main...origin/main` and expect `0 0`.
