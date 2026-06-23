# Phase 2F Student Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete graph-first student navigation across knowledge, ability, and literacy tags.

**Architecture:** Keep Phase 2E mastery metrics as the evidence source, add student-facing navigation view models that join tags to published related questions, wrong questions, redo records, and mastery evidence, then render three parallel panels in the existing student app. Use existing bottom tabs and `data-action="open-question"` navigation semantics, extending them so links activate the correct panel before scrolling.

**Tech Stack:** Python 3 standard library, SQLite, stdlib HTTP rendering, CSS, vanilla JavaScript, unittest.

---

## Phase Boundary

Implement Phase 2F only:

- Student home remains graph-first.
- Knowledge, ability, and literacy each expose a student-facing module with mastery state, related published questions, wrong questions, and redo tasks.
- Related-question links activate the destination panel before scrolling.
- Repeated cards have unique HTML ids per panel.
- Related lists use published/visible content rules and do not expose unpublished teacher-only drafts.

Do not implement Phase 2G teacher/admin aggregate analytics.

## Evidence And Visibility Policy

- Published-content rule: student navigation can show a related question only when that question appears in a published assessment for the authenticated student.
- Wrong-question rule: wrong and redo links only use records from the authenticated student's published wrong book.
- Tag evidence: knowledge/ability/literacy panels use Phase 2E `student_mastery_metrics` and existing tag relationships.
- Duplicate card rule: each visible card id is prefixed by panel and tag type, for example `ability-ab-force-analysis-question-q-newton-2`.

## File Map

- Modify `highschoolphysics/repository.py`: add student tag navigation view models for knowledge, ability, and literacy.
- Modify `highschoolphysics/server.py`: render three navigation modules and update link targets.
- Modify `highschoolphysics/assets/app.js`: add tag panel activation and cross-panel scroll behavior.
- Modify `highschoolphysics/assets/app.css`: style the student navigation grids and selected panels.
- Modify `tests/test_workflow.py`: assert repository visibility and no unpublished leakage.
- Modify `tests/test_server.py`: assert rendered student app has unique ids and three-family navigation.
- Modify `README.md` and roadmap spec after acceptance.

### Task 1: Repository Navigation View Models

**Files:**
- Modify: `highschoolphysics/repository.py`
- Test: `tests/test_workflow.py`

- [x] **Step 1: Write failing repository test**

Add `test_student_navigation_modules_respect_published_visibility`:

```python
def test_student_navigation_modules_respect_published_visibility(self):
    self._publish_demo_assessment()
    unpublished = self.repo.create_question(
        actor_id=self.teacher.user["id"],
        stem="未发布教师草稿题不能出现在学生导航。",
        options={"A": "是", "B": "否"},
        answer={"type": "single_choice", "answer": "A"},
        analysis="草稿题。",
        question_type="single_choice",
        source="校本题库",
        grade="高二",
        chapter="牛顿运动定律",
        difficulty="基础",
    )
    candidate = self.repo.generate_llm_candidates(
        actor_id=self.teacher.user["id"],
        question_id=unpublished["id"],
    )
    self.repo.confirm_question_tags(
        actor_id=self.teacher.user["id"],
        question_id=unpublished["id"],
        candidate_id=candidate["id"],
        knowledge_node_ids=["kn-pep2019-r1-c04-s03"],
        ability_tag_ids=["ab-force-analysis"],
        literacy_tag_ids=["lit-thinking-model"],
    )

    dashboard = self.repo.student_dashboard("stu-1001")

    knowledge = next(
        item for item in dashboard["knowledge_navigation"]
        if item["tag_id"] == "kn-pep2019-r1-c04-s03"
    )
    ability = next(
        item for item in dashboard["ability_navigation"]
        if item["tag_id"] == "ab-force-analysis"
    )
    literacy = next(
        item for item in dashboard["literacy_navigation"]
        if item["tag_id"] == "lit-thinking-model"
    )

    self.assertGreaterEqual(len(knowledge["related_questions"]), 1)
    self.assertGreaterEqual(len(knowledge["wrong_questions"]), 1)
    self.assertGreaterEqual(len(knowledge["redo_tasks"]), 1)
    self.assertGreaterEqual(len(ability["related_questions"]), 1)
    self.assertGreaterEqual(len(literacy["related_questions"]), 0)
    for module in (knowledge, ability, literacy):
        self.assertNotIn(
            unpublished["id"],
            {question["id"] for question in module["related_questions"]},
        )
```

- [x] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_student_navigation_modules_respect_published_visibility -v
```

Expected: FAIL because `knowledge_navigation`, `ability_navigation`, and `literacy_navigation` do not exist.

- [x] **Step 3: Implement repository view models**

Add helpers:

- `student_published_question_ids(student_id)`
- `student_navigation_modules(student_id, tag_type, metrics, wrongs)`
- `student_navigation_cards_for_tag(...)`

Each module returns:

```python
{
    "tag_type": "ability",
    "tag_id": "ab-force-analysis",
    "tag_name": "受力分析",
    "mastery_css_class": "...",
    "calculated_mastery_state": "...",
    "mastery_evidence_text": "...",
    "related_questions": [...],
    "wrong_questions": [...],
    "redo_tasks": [...],
}
```

Only include related questions whose ids appear in the student's published assessment history.

- [x] **Step 4: Run GREEN**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_student_navigation_modules_respect_published_visibility -v
python3 -m unittest tests.test_workflow.WorkflowTests -v
```

- [x] **Step 5: Commit**

```bash
git add highschoolphysics/repository.py tests/test_workflow.py
git commit -m "feat: build student tag navigation data"
```

### Task 2: Student Rendering

**Files:**
- Modify: `highschoolphysics/server.py`
- Modify: `highschoolphysics/assets/app.css`
- Test: `tests/test_server.py`

- [x] **Step 1: Write failing rendering test**

Add `test_student_app_renders_three_family_navigation_without_duplicate_ids`:

```python
def test_student_app_renders_three_family_navigation_without_duplicate_ids(self):
    self._publish_demo_assessment()
    student = self.auth.login("stu_1001", "student123", "unit-test").user
    html = render_student_app(student, self.repo.student_dashboard(student["id"]))

    self.assertIn('data-tag-family-panel="knowledge"', html)
    self.assertIn('data-tag-family-panel="ability"', html)
    self.assertIn('data-tag-family-panel="literacy"', html)
    self.assertIn("知识导航", html)
    self.assertIn("能力导航", html)
    self.assertIn("核心素养导航", html)
    self.assertIn("当前掌握证据", html)
    self.assertIn('data-target-tab="graph"', html)
    self.assertIn('data-target-panel="ability"', html)

    ids = re.findall(r'\\sid="([^"]+)"', html)
    self.assertEqual(len(ids), len(set(ids)))
```

- [x] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_server.ServerRenderingTests.test_student_app_renders_three_family_navigation_without_duplicate_ids -v
```

Expected: FAIL because the three-family navigation panels are not rendered.

- [x] **Step 3: Implement rendering**

Render:

- `knowledge_navigation` as “知识导航”
- `ability_navigation` as “能力导航”
- `literacy_navigation` as “核心素养导航”

For each module show mastery state/evidence, related questions, wrong questions, redo tasks, and links with `data-action="open-question"`, `data-target-tab`, `data-target-panel`, and unique `data-target-id`.

- [x] **Step 4: Run GREEN**

Run:

```bash
python3 -m unittest tests.test_server.ServerRenderingTests.test_student_app_renders_three_family_navigation_without_duplicate_ids -v
python3 -m unittest tests.test_server.ServerRenderingTests -v
```

- [x] **Step 5: Commit**

```bash
git add highschoolphysics/server.py highschoolphysics/assets/app.css tests/test_server.py
git commit -m "feat: render student three-family navigation"
```

### Task 3: Frontend Navigation Activation

**Files:**
- Modify: `highschoolphysics/assets/app.js`
- Test: `tests/test_server.py`

- [x] **Step 1: Write failing static behavior test**

Add assertions to the rendering test:

```python
script = Path("highschoolphysics/assets/app.js").read_text()
self.assertIn("activateTagFamilyPanel", script)
self.assertIn("targetPanel", script)
```

- [x] **Step 2: Run RED**

Run the rendering test. Expected: FAIL because the JS helper is absent.

- [x] **Step 3: Implement JS**

Add `activateTagFamilyPanel(panelName)` and update `open-question` handling:

- activate destination bottom tab first;
- activate `data-tag-family-panel` when `data-target-panel` is provided;
- scroll after `requestAnimationFrame`;
- highlight destination card.

- [x] **Step 4: Run GREEN**

Run:

```bash
node --check highschoolphysics/assets/app.js
python3 -m unittest tests.test_server.ServerRenderingTests.test_student_app_renders_three_family_navigation_without_duplicate_ids -v
```

- [x] **Step 5: Commit**

```bash
git add highschoolphysics/assets/app.js tests/test_server.py
git commit -m "feat: activate student tag navigation targets"
```

### Task 4: Documentation And Acceptance

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md`

- [x] **Step 1: Update docs**

Document Phase 2F completion, visibility policy, and remaining Phase 2G boundary.

- [x] **Step 2: Run verification**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/hsp-phase2f-verify python3 -m compileall -q highschoolphysics tools tests
node --check highschoolphysics/assets/app.js
python3 -m unittest discover -s tests -v
git diff --check
```

- [x] **Step 3: Browser acceptance**

Run demo server on a free local port with a throwaway SQLite DB. Publish the demo assessment, then verify at `1600x900`:

- student page shows knowledge, ability, and literacy navigation;
- related-question links can activate the graph panel and scroll to unique cards;
- teacher and admin pages still render;
- no horizontal overflow on student, teacher, or admin pages.

- [x] **Step 4: Commit docs**

```bash
git add README.md highschoolphysics/server.py docs/superpowers/plans/2026-06-11-phase-2f-student-navigation.md docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md
git commit -m "docs: record phase 2f navigation acceptance"
```

## Final Integration

- Verify branch is clean.
- Merge `codex/phase-2f-student-navigation` to `main`.
- Push `main`.
- Run `git rev-list --left-right --count main...origin/main` and expect `0 0`.
