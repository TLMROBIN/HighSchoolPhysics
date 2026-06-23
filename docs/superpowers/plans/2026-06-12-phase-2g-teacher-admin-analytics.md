# Phase 2G Teacher And Admin Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Phase 2G teacher/admin mastery analytics from deterministic Phase 2E mastery metrics.

**Architecture:** Keep `student_mastery_metrics` as the source of truth and add aggregate view models in `PhysicsRepository`. Teacher analytics are scoped by assessment/class and include own-class student drilldown plus aggregate-only grade comparison. Admin analytics are grade-level and aggregate-only by default, with trend rows that never expose student-level personal rows.

**Tech Stack:** Python 3 standard library, SQLite, stdlib HTTP rendering, CSS, vanilla JavaScript, unittest.

---

## Phase Boundary

Implement Phase 2G only:

- Teacher sees class-level mastery analytics for knowledge, ability, and literacy tags.
- Teacher can drill from class aggregate tags to students in the teacher's own class.
- Teacher sees same-grade aggregate comparison without other-class student rows.
- Admin sees grade-level mastery graphs and aggregate trends without student-level rows by default.
- Error rates use `wrong_count / eligible_attempts`; blank rates are reported separately as `blank_count / eligible_attempts`.
- Aggregate queries bulk-load rows from `student_mastery_metrics` and tag tables rather than looping per node or per question.

Do not implement Phase 3 graph layout, zoom behavior, backup restore, or tablet interaction changes.

## File Map

- Modify `highschoolphysics/repository.py`: add bulk mastery analytics helpers, teacher class analytics, admin grade analytics, and dashboard wiring.
- Modify `highschoolphysics/server.py`: render teacher Phase 2G analytics and admin grade analytics sections.
- Modify `highschoolphysics/assets/app.css`: style analytics grids, state bars, and trend tables.
- Modify `tests/test_workflow.py`: repository analytics, denominator, scope, and aggregate-only tests.
- Modify `tests/test_server.py`: rendering assertions for teacher/admin analytics.
- Modify `README.md` and `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md` after acceptance.

### Task 1: Repository Analytics View Models

**Files:**
- Modify: `highschoolphysics/repository.py`
- Test: `tests/test_workflow.py`

- [ ] **Step 1: Write failing repository tests**

Add tests near the Phase 2E/2F workflow tests:

```python
def test_class_mastery_analytics_use_tagged_attempt_denominator_and_drilldown_scope(self):
    self._publish_demo_assessment()

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
    self.assertEqual(knowledge["eligible_attempts"], 2)
    self.assertEqual(knowledge["wrong_count"], 1)
    self.assertEqual(knowledge["blank_count"], 1)
    self.assertEqual(knowledge["error_rate"], 0.5)
    self.assertEqual(knowledge["blank_rate"], 0.5)
    self.assertEqual(
        knowledge["state_counts"],
        {"未练习": 0, "未掌握": 2, "有困难": 0, "不熟练": 0, "已掌握": 0},
    )
    self.assertEqual(
        {student["student_id"] for student in knowledge["students"]},
        {"stu-1002", "stu-1003"},
    )

    ability = next(
        item for item in analytics["ability"]
        if item["tag_id"] == "ab-calculation"
    )
    self.assertEqual(ability["eligible_attempts"], 2)
    self.assertEqual(ability["error_rate"], 0.5)

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
```

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_class_mastery_analytics_use_tagged_attempt_denominator_and_drilldown_scope tests.test_workflow.WorkflowTests.test_teacher_mastery_analytics_reject_another_class_and_grade_comparison_is_aggregate_only tests.test_workflow.WorkflowTests.test_admin_mastery_analytics_are_grade_level_and_aggregate_only -v
```

Expected: FAIL with missing `class_mastery_analytics` / `admin_mastery_analytics`.

- [ ] **Step 3: Implement repository analytics**

Add helpers with these concrete contracts:

```python
def _mastery_state_counts(self):
    return {"未练习": 0, "未掌握": 0, "有困难": 0, "不熟练": 0, "已掌握": 0}

def _tag_catalog(self, tag_type):
    """Return {tag_id: {tag_type, tag_id, tag_name, stable_code, path_text}}."""

def _aggregate_mastery_rows(self, rows, include_students=False):
    """Return a dict with knowledge, ability, and literacy aggregate lists."""

def _class_mastery_rows(self, class_id):
    """Bulk-load student mastery rows joined to users for one class."""

def _grade_mastery_rows(self, grade):
    """Bulk-load student mastery rows joined to users/class_groups for one grade."""

def _grade_mastery_trends(self):
    """Return aggregate score-rate trend rows by grade and assessment."""

def class_mastery_analytics(self, actor_id, assessment_id):
    """Return class aggregates, own-class drilldown, and aggregate grade comparison."""

def admin_mastery_analytics(self, actor_id):
    """Return grade-level aggregate mastery and score trends for admins only."""
```

Implementation rules:

- `class_mastery_analytics` must call `self._require(actor_id, "view", "diagnostics", assessment_id)`.
- Class rows must join `student_mastery_metrics` to `users` and filter by `users.class_id = ?`.
- Grade comparison rows must join `student_mastery_metrics` to `users` and `class_groups`, filter by `class_groups.grade = ?`, and aggregate with `include_students=False`.
- Aggregated `error_rate` must be `wrong_count / eligible_attempts`, not `wrong_count / participant_count`.
- Aggregated `blank_rate` must be `blank_count / eligible_attempts`.
- `state_counts` must use the five Phase 2E states and drive both graph and table rendering.
- Student drilldown rows are included only in class analytics aggregates, not grade comparison or admin aggregates.
- `admin_mastery_analytics` must require admin role and return grade rows plus aggregate score trends only.

- [ ] **Step 4: Run GREEN**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_class_mastery_analytics_use_tagged_attempt_denominator_and_drilldown_scope tests.test_workflow.WorkflowTests.test_teacher_mastery_analytics_reject_another_class_and_grade_comparison_is_aggregate_only tests.test_workflow.WorkflowTests.test_admin_mastery_analytics_are_grade_level_and_aggregate_only -v
python3 -m unittest tests.test_workflow.WorkflowTests -v
```

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/repository.py tests/test_workflow.py
git commit -m "feat: aggregate mastery analytics"
```

### Task 2: Teacher Analytics Rendering

**Files:**
- Modify: `highschoolphysics/server.py`
- Modify: `highschoolphysics/assets/app.css`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write failing rendering test**

Add:

```python
def test_teacher_app_exposes_phase_2g_mastery_analytics(self):
    self._publish_demo_assessment()
    teacher = self.auth.login("teacher_li", "teacher123", "unit-test").user

    html = render_teacher_app(teacher, self.repo.teacher_dashboard(teacher["id"]))

    self.assertIn("Phase 2G 掌握度分析", html)
    self.assertIn("班级掌握图谱", html)
    self.assertIn("年级均值对比", html)
    self.assertIn("知识掌握", html)
    self.assertIn("能力掌握", html)
    self.assertIn("核心素养掌握", html)
    self.assertIn("学生明细", html)
    self.assertIn("错误率", html)
    self.assertIn("空白率", html)
    self.assertIn("data-analytics-family=\"knowledge\"", html)
```

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_server.ServerRenderingTests.test_teacher_app_exposes_phase_2g_mastery_analytics -v
```

Expected: FAIL because teacher rendering does not expose Phase 2G analytics.

- [ ] **Step 3: Implement teacher rendering**

Add rendering helpers with these contracts:

```python
def _percent(value):
    return "%.1f%%" % (float(value or 0) * 100)

def _render_mastery_state_bar(state_counts):
    """Render five Phase 2E state counts with the same values used in tables."""

def _render_mastery_analytics_table(items, include_students=False):
    """Render aggregate rows and optional own-class student drilldown rows."""

def _render_teacher_mastery_analytics(analytics):
    """Render class mastery, grade comparison, and three tag-family tables."""
```

Render a teacher section headed `Phase 2G 掌握度分析` with:

- class summary and `班级掌握图谱`;
- three family sections: `知识掌握`, `能力掌握`, `核心素养掌握`;
- table columns for eligible attempts, correct, wrong, blank, `错误率`, `空白率`, state counts, and optional `学生明细`;
- `年级均值对比` section with aggregate-only rows.

- [ ] **Step 4: Run GREEN**

Run:

```bash
python3 -m unittest tests.test_server.ServerRenderingTests.test_teacher_app_exposes_phase_2g_mastery_analytics -v
python3 -m unittest tests.test_server.ServerRenderingTests -v
```

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/server.py highschoolphysics/assets/app.css tests/test_server.py
git commit -m "feat: render teacher mastery analytics"
```

### Task 3: Admin Analytics Rendering

**Files:**
- Modify: `highschoolphysics/server.py`
- Modify: `highschoolphysics/assets/app.css`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write failing rendering test**

Add:

```python
def test_admin_app_exposes_phase_2g_grade_analytics_without_student_rows(self):
    self._publish_demo_assessment()
    admin = self.auth.login("admin", "admin123", "unit-test").user

    html = render_admin_app(admin, self.repo.admin_dashboard())

    self.assertIn("Phase 2G 年级掌握分析", html)
    self.assertIn("年级掌握趋势", html)
    self.assertIn("聚合标签掌握", html)
    self.assertIn("aggregate-only", html)
    self.assertIn("高二", html)
    self.assertNotIn('data-admin-analytics-student-id="stu-1001"', html)
```

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_server.ServerRenderingTests.test_admin_app_exposes_phase_2g_grade_analytics_without_student_rows -v
```

Expected: FAIL because admin rendering does not expose Phase 2G analytics.

- [ ] **Step 3: Implement admin rendering**

Wire `dashboard["mastery_analytics"] = self.admin_mastery_analytics(admin_id)` in `admin_dashboard`, passing the current admin actor from the route if needed. Render an admin section headed `Phase 2G 年级掌握分析` with:

- grade overview cards;
- aggregate-only tag sections for knowledge, ability, and literacy;
- `年级掌握趋势` table from published/graded assessments;
- no student-level `data-*` attributes or student drilldown rows.

- [ ] **Step 4: Run GREEN**

Run:

```bash
python3 -m unittest tests.test_server.ServerRenderingTests.test_admin_app_exposes_phase_2g_grade_analytics_without_student_rows -v
python3 -m unittest tests.test_server.ServerRenderingTests -v
```

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/repository.py highschoolphysics/server.py highschoolphysics/assets/app.css tests/test_server.py
git commit -m "feat: render admin grade mastery analytics"
```

### Task 4: Documentation And Acceptance

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md`

- [ ] **Step 1: Update docs**

Document Phase 2G completion, teacher/admin analytics semantics, aggregate-only admin defaults, denominator policy, and remaining Phase 3 boundary.

- [ ] **Step 2: Run full verification**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/hsp-phase2g-verify python3 -m compileall -q highschoolphysics tools tests
node --check highschoolphysics/assets/app.js
python3 -m unittest discover -s tests -v
git diff --check
```

- [ ] **Step 3: Browser acceptance**

Run demo server on a free local port with a throwaway SQLite DB. Publish the demo assessment, then verify at `1600x900`:

- teacher login shows `Phase 2G 掌握度分析`, class mastery, student drilldown, grade comparison, and no horizontal overflow;
- admin login shows `Phase 2G 年级掌握分析`, grade trends, aggregate-only tag mastery, and no horizontal overflow;
- student page still shows graph-first 2F navigation and no horizontal overflow.

- [ ] **Step 4: Commit docs**

```bash
git add README.md docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md
git commit -m "docs: record phase 2g analytics acceptance"
```
