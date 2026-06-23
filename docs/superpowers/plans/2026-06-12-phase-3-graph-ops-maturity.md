# Phase 3+ Graph And Operational Maturity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Phase 3+ by replacing demo graph coordinates with deterministic layered graph behavior and proving backup/restore plus migration maturity.

**Architecture:** Add focused modules for graph layout and backup operations, then wire them into the existing stdlib server-rendered app. Keep the student graph readable and accessible without adding frontend dependencies. Use additive schema versioning and repository-level consistency checks for operational evidence.

**Tech Stack:** Python 3 standard library, SQLite, server-rendered HTML/CSS/SVG, vanilla JavaScript, unittest, headless Chrome/Playwright for browser acceptance.

---

## Phase Boundary

Implement Phase 3+ only:

- Imported non-demo graph nodes use deterministic, readable, non-overlapping positions.
- Graph layout must not contain hard-coded demo node IDs.
- Student graph supports zoom-level detail rules and keyboard-accessible node selection.
- Student graph must work at classroom tablet viewport in browser acceptance.
- A database created by the previous release migrates forward without losing assessment, wrong-question, mastery, or ontology history.
- Backup data can restore into a fresh database and pass core consistency checks.

Do not implement production SSO, external LLM credentials, external OCR credentials, new graph decoration themes, or a full third-party graph visualization library.

## File Map

- Create `highschoolphysics/graph_layout.py`: deterministic layered graph layout helper.
- Create `highschoolphysics/backup.py`: backup table list, restore helper, and consistency checks.
- Modify `highschoolphysics/server.py`: use layout metadata in student graph rendering and expose accessibility/detail attributes.
- Modify `highschoolphysics/assets/app.js`: graph zoom detail classes, keyboard selection, pointer capture/cancel handling.
- Modify `highschoolphysics/assets/app.css`: zoom-detail visibility and tablet-safe graph controls.
- Modify `highschoolphysics/db.py`: bump `SCHEMA_VERSION` to `6`, keep migration additive.
- Modify `highschoolphysics/repository.py`: route backup export through `backup.py` and expose consistency check.
- Modify `tests/test_graph_layout.py`: layout determinism and non-overlap tests.
- Modify `tests/test_server.py`: graph rendering accessibility/detail assertions.
- Modify `tests/test_database.py`: Phase 3+ previous-release migration test.
- Modify `tests/test_workflow.py`: backup restore and consistency tests.
- Modify `README.md` and `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md` after acceptance.

### Task 1: Deterministic Layered Graph Layout

**Files:**
- Create: `highschoolphysics/graph_layout.py`
- Modify: `highschoolphysics/server.py`
- Test: `tests/test_graph_layout.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write failing graph layout tests**

Create `tests/test_graph_layout.py`:

```python
import unittest

from highschoolphysics.graph_layout import layout_knowledge_graph


class GraphLayoutTests(unittest.TestCase):
    def test_layout_positions_non_demo_nodes_without_overlap(self):
        nodes = [
            {"id": "module-a", "parent_id": None, "level": 1, "name": "模块A"},
            {"id": "chapter-a", "parent_id": "module-a", "level": 2, "name": "章节A"},
            {"id": "section-a", "parent_id": "chapter-a", "level": 3, "name": "小节A"},
            {"id": "section-b", "parent_id": "chapter-a", "level": 3, "name": "小节B"},
            {"id": "chapter-b", "parent_id": "module-a", "level": 2, "name": "章节B"},
        ]
        edges = [
            {"source_node_id": "module-a", "target_node_id": "chapter-a"},
            {"source_node_id": "chapter-a", "target_node_id": "section-a"},
            {"source_node_id": "chapter-a", "target_node_id": "section-b"},
            {"source_node_id": "module-a", "target_node_id": "chapter-b"},
        ]

        layout = layout_knowledge_graph(nodes, edges)

        positions = {
            item["id"]: (item["x"], item["y"])
            for item in layout["nodes"]
        }
        self.assertEqual(len(set(positions.values())), len(nodes))
        self.assertLess(positions["module-a"][0], positions["chapter-a"][0])
        self.assertLess(positions["chapter-a"][0], positions["section-a"][0])
        self.assertEqual(layout["layout"], "deterministic-layered-v1")
        self.assertGreaterEqual(layout["view_box"]["height"], 300)

    def test_layout_is_stable_regardless_of_input_order(self):
        nodes = [
            {"id": "n3", "parent_id": "n1", "level": 2, "name": "C"},
            {"id": "n1", "parent_id": None, "level": 1, "name": "A"},
            {"id": "n2", "parent_id": "n1", "level": 2, "name": "B"},
        ]
        edges = [
            {"source_node_id": "n1", "target_node_id": "n3"},
            {"source_node_id": "n1", "target_node_id": "n2"},
        ]

        first = layout_knowledge_graph(nodes, edges)
        second = layout_knowledge_graph(list(reversed(nodes)), list(reversed(edges)))

        first_positions = [(item["id"], item["x"], item["y"]) for item in first["nodes"]]
        second_positions = [(item["id"], item["x"], item["y"]) for item in second["nodes"]]
        self.assertEqual(first_positions, second_positions)


if __name__ == "__main__":
    unittest.main()
```

Add a rendering assertion to `tests/test_server.py`:

```python
def test_student_relation_graph_uses_layered_layout_and_accessible_nodes(self):
    teacher = self.auth.login("teacher_li", "teacher123", "unit-test").user
    self.repo.confirm_question_tags(
        actor_id=teacher["id"],
        question_id="q-newton-1",
        knowledge_node_ids=["kn-pep2019-r1-c04-s03"],
        ability_tag_ids=["ab-force-analysis"],
        literacy_tag_ids=["lit-thinking-model"],
    )
    student = self.auth.login("stu_1001", "student123", "unit-test").user

    html = render_student_app(student, self.repo.student_dashboard(student["id"]))

    self.assertIn('data-layout="deterministic-layered-v1"', html)
    self.assertIn('role="button"', html)
    self.assertIn('tabindex="0"', html)
    self.assertIn('data-detail-level="module"', html)
    self.assertIn('data-detail-level="child"', html)
    self.assertNotIn("kn-mechanics", html)
```

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_graph_layout tests.test_server.ServerRenderingTests.test_student_relation_graph_uses_layered_layout_and_accessible_nodes -v
```

Expected: FAIL because `highschoolphysics.graph_layout` does not exist and graph rendering has no layered layout metadata.

- [ ] **Step 3: Implement layout helper**

Create `highschoolphysics/graph_layout.py`:

```python
"""Deterministic graph layout helpers for student knowledge maps."""


LAYOUT_VERSION = "deterministic-layered-v1"


def _node_sort_key(node):
    return (
        int(node.get("level") or 0),
        node.get("stable_code") or "",
        node.get("name") or "",
        node.get("id") or "",
    )


def layout_knowledge_graph(nodes, edges, width=720, min_height=320):
    ordered = sorted([dict(node) for node in nodes], key=_node_sort_key)
    levels = {}
    for node in ordered:
        level = int(node.get("level") or 1)
        levels.setdefault(level, []).append(node)
    if not levels:
        return {
            "layout": LAYOUT_VERSION,
            "view_box": {"width": width, "height": min_height},
            "nodes": [],
            "edges": [],
        }
    level_values = sorted(levels)
    max_level_size = max(len(items) for items in levels.values())
    height = max(min_height, 90 + max_level_size * 64)
    column_gap = width / max(1, len(level_values) + 1)
    positioned = []
    position_by_id = {}
    for column, level in enumerate(level_values, start=1):
        items = levels[level]
        row_gap = height / max(1, len(items) + 1)
        for row, node in enumerate(items, start=1):
            x = round(column * column_gap, 1)
            y = round(row * row_gap, 1)
            detail_level = "module" if level <= 1 else "child"
            item = {
                **node,
                "x": x,
                "y": y,
                "detail_level": detail_level,
                "min_label_scale": 0.55 if detail_level == "module" else 1.15,
            }
            positioned.append(item)
            position_by_id[item["id"]] = item
    rendered_edges = []
    for edge in sorted(edges, key=lambda item: (item["source_node_id"], item["target_node_id"])):
        start = position_by_id.get(edge["source_node_id"])
        end = position_by_id.get(edge["target_node_id"])
        if not start or not end:
            continue
        rendered_edges.append({"edge": edge, "source": start, "target": end})
    return {
        "layout": LAYOUT_VERSION,
        "view_box": {"width": width, "height": height},
        "nodes": positioned,
        "edges": rendered_edges,
    }
```

- [ ] **Step 4: Wire layout into student graph rendering**

In `highschoolphysics/server.py`, import `layout_knowledge_graph` and update `_render_student_relation_graph` to:

- call `layout_knowledge_graph(nodes, edges)`;
- render only edges returned by the layout helper;
- render `<svg ... data-layout="deterministic-layered-v1" data-graph-scale-state="medium">`;
- render each graph node with `role="button"`, `tabindex="0"`, `aria-label`, `data-detail-level`, and `data-min-label-scale`;
- remove all hard-coded demo coordinate IDs.

- [ ] **Step 5: Run GREEN**

Run:

```bash
python3 -m unittest tests.test_graph_layout tests.test_server.ServerRenderingTests.test_student_relation_graph_uses_layered_layout_and_accessible_nodes -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add highschoolphysics/graph_layout.py highschoolphysics/server.py tests/test_graph_layout.py tests/test_server.py
git commit -m "feat: add deterministic student graph layout"
```

### Task 2: Zoom Detail, Keyboard, And Tablet Interaction

**Files:**
- Modify: `highschoolphysics/assets/app.js`
- Modify: `highschoolphysics/assets/app.css`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write failing rendering assertions**

Add to the graph rendering test from Task 1:

```python
self.assertIn('aria-label="选择知识节点', html)
self.assertIn('data-graph-detail-control', html)
```

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_server.ServerRenderingTests.test_student_relation_graph_uses_layered_layout_and_accessible_nodes -v
```

Expected: FAIL until the graph toolbar and node attributes expose the detail control contract.

- [ ] **Step 3: Implement frontend interaction**

Update `highschoolphysics/assets/app.js`:

- add `graphScaleState()` that returns `low`, `medium`, or `high`;
- make `updateGraphTransform()` set `data-graph-scale-state` on `.student-relation-graph`;
- handle `keydown` on `[data-action="select-knowledge"]` for `Enter` and `Space`;
- call `setPointerCapture` on pointer down when available;
- clear drag state on `pointerup`, `pointercancel`, and `lostpointercapture`.

- [ ] **Step 4: Implement CSS detail rules**

Update `highschoolphysics/assets/app.css`:

- hide `.graph-node[data-detail-level="child"] text` at low scale;
- show child labels at high scale;
- keep module labels visible at all scales;
- make `.relation-toolbar` touch-friendly with 44px buttons.

- [ ] **Step 5: Run GREEN**

Run:

```bash
python3 -m unittest tests.test_server.ServerRenderingTests.test_student_relation_graph_uses_layered_layout_and_accessible_nodes -v
node --check highschoolphysics/assets/app.js
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add highschoolphysics/assets/app.js highschoolphysics/assets/app.css tests/test_server.py
git commit -m "feat: improve graph zoom and tablet interaction"
```

### Task 3: Backup Restore And Consistency Checks

**Files:**
- Create: `highschoolphysics/backup.py`
- Modify: `highschoolphysics/repository.py`
- Test: `tests/test_workflow.py`

- [ ] **Step 1: Write failing backup restore test**

Add to `tests/test_workflow.py`:

```python
def test_backup_restore_into_fresh_database_preserves_core_history(self):
    teacher = self.auth.login("teacher_li", "teacher123", "unit-test").user
    self.repo.resolve_review_item(
        teacher["id"],
        "resp-1001-q2",
        "C",
        "Phase 3 restore fixture",
    )
    self.repo.grade_assessment(
        teacher["id"],
        "assess-week-1",
        publish=True,
    )
    backup = self.repo.export_backup(actor_id="user-admin")

    restored_conn = connect(Path(self.tmpdir.name) / "restored.sqlite3")
    initialize_database(restored_conn)
    restored_repo = PhysicsRepository(restored_conn)
    summary = restored_repo.restore_backup(backup)
    checks = restored_repo.consistency_check()

    self.assertGreater(summary["restored_rows"], 0)
    self.assertEqual(checks["status"], "ok")
    self.assertEqual(checks["issues"], [])
    self.assertIsNotNone(
        restored_conn.execute(
            "select 1 from assessment_sessions where id = ?",
            ("assess-week-1",),
        ).fetchone()
    )
    self.assertIsNotNone(
        restored_conn.execute(
            "select 1 from student_mastery_metrics where student_id = ?",
            ("stu-1001",),
        ).fetchone()
    )
    restored_conn.close()
```

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_backup_restore_into_fresh_database_preserves_core_history -v
```

Expected: FAIL because `restore_backup` and `consistency_check` do not exist.

- [ ] **Step 3: Implement backup module**

Create `highschoolphysics/backup.py` with:

- `BACKUP_TABLES` in dependency order;
- `export_tables(conn)` that exports rows and redacts `users.password_hash`;
- `restore_backup(conn, backup)` that initializes the database, deletes tables in reverse dependency order, replaces redacted password hashes with a deterministic disabled placeholder hash, inserts rows in dependency order, and returns row counts;
- `consistency_check(conn)` that runs `pragma foreign_key_check`, verifies required core tables are populated, and verifies assessment snapshots/responses/mastery rows still connect to users/questions.

- [ ] **Step 4: Wire repository methods**

Update `highschoolphysics/repository.py`:

- `export_backup` should use `backup.export_tables(self.conn)`, add metadata, audit, then return the exported structure;
- add `restore_backup(self, payload)` that calls `backup.restore_backup(self.conn, payload)`;
- add `consistency_check(self)` that calls `backup.consistency_check(self.conn)`.

- [ ] **Step 5: Run GREEN**

Run:

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_backup_export_contains_core_assets tests.test_workflow.WorkflowTests.test_backup_restore_into_fresh_database_preserves_core_history -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add highschoolphysics/backup.py highschoolphysics/repository.py tests/test_workflow.py
git commit -m "feat: restore backups with consistency checks"
```

### Task 4: Previous-Release Migration Exercise

**Files:**
- Modify: `highschoolphysics/db.py`
- Test: `tests/test_database.py`

- [ ] **Step 1: Write failing migration test**

Add to `tests/test_database.py`:

```python
def test_phase_3_schema_migrates_phase_2g_database_without_history_loss(self):
    conn = connect(self.db_path)
    initialize_database(conn)
    seed_demo_data(conn)
    conn.execute("pragma user_version = 5")
    before = {
        "assessments": conn.execute("select count(*) from assessment_sessions").fetchone()[0],
        "responses": conn.execute("select count(*) from student_responses").fetchone()[0],
        "ontology": conn.execute("select count(*) from knowledge_nodes").fetchone()[0],
    }

    initialize_database(conn)

    after = {
        "assessments": conn.execute("select count(*) from assessment_sessions").fetchone()[0],
        "responses": conn.execute("select count(*) from student_responses").fetchone()[0],
        "ontology": conn.execute("select count(*) from knowledge_nodes").fetchone()[0],
    }
    self.assertEqual(before, after)
    self.assertEqual(conn.execute("pragma user_version").fetchone()[0], 6)
    self.assertEqual(conn.execute("pragma foreign_key_check").fetchall(), [])
    conn.close()
```

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_database.DatabaseConfigurationTests.test_phase_3_schema_migrates_phase_2g_database_without_history_loss -v
```

Expected: FAIL because `SCHEMA_VERSION` is still `5`.

- [ ] **Step 3: Bump schema version**

Update `highschoolphysics/db.py`:

```python
SCHEMA_VERSION = 6
```

No destructive migration is required; Phase 3+ operational maturity uses the existing schema and records the forward migration through SQLite `user_version`.

- [ ] **Step 4: Update existing schema-version expectations**

In `tests/test_database.py`, update schema version assertions from `5` to `6` where they assert the current schema version after `initialize_database`.

- [ ] **Step 5: Run GREEN**

Run:

```bash
python3 -m unittest tests.test_database.DatabaseConfigurationTests -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add highschoolphysics/db.py tests/test_database.py
git commit -m "feat: record phase 3 schema migration"
```

### Task 5: Documentation And Acceptance

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md`

- [ ] **Step 1: Run full automated verification**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/hsp-phase3-verify python3 -m compileall -q highschoolphysics tools tests
node --check highschoolphysics/assets/app.js
python3 -m unittest discover -s tests -v
git diff --check
```

Expected: all commands pass.

- [ ] **Step 2: Run browser acceptance**

Start a throwaway demo database, publish `assess-week-1`, and verify at `1366x1024` and `1600x900`:

- student login shows graph-first navigation;
- graph SVG has `data-layout="deterministic-layered-v1"`;
- child labels become available after zooming in;
- a graph node can be selected with keyboard focus and `Enter`;
- touch/pointer pan changes graph transform;
- page has no horizontal document overflow;
- admin can download backup and a restored database passes `consistency_check`.

- [ ] **Step 3: Update docs**

In `README.md`, add a Phase 3+ section that states:

- deterministic layered graph layout is complete;
- zoom/detail and keyboard/touch interaction are complete;
- backup restore and consistency checks are complete;
- schema version 6 records the Phase 3+ forward migration exercise.

In `docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md`, add:

- `Phase 3+ Automated Acceptance Note`;
- `Phase 3+ Browser Acceptance Note`;
- final completion audit summary mapping Phase 2A.1 through Phase 3+ to accepted evidence.

- [ ] **Step 4: Commit docs**

```bash
git add README.md docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md
git commit -m "docs: record phase 3 graph ops acceptance"
```

### Task 6: Merge And Push

**Files:**
- No code edits.

- [ ] **Step 1: Verify branch clean**

Run:

```bash
git status --short --branch
git log --oneline --decorate -8
```

- [ ] **Step 2: Merge to main**

From `/Users/binyu/Projects/HighSchoolPhysics`:

```bash
git status --short --branch
git merge --ff-only codex/phase-3-graph-ops-maturity
```

- [ ] **Step 3: Verify on main**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/hsp-phase3-main-verify python3 -m compileall -q highschoolphysics tools tests
node --check highschoolphysics/assets/app.js
python3 -m unittest discover -s tests -v
git diff --check
```

- [ ] **Step 4: Push and sync-check**

Run:

```bash
git push origin main
git rev-list --left-right --count main...origin/main
```

Expected: `0 0`.

- [ ] **Step 5: Clean worktree**

Run:

```bash
git worktree remove /Users/binyu/Projects/HighSchoolPhysics/.worktrees/phase-3-graph-ops-maturity
git branch -d codex/phase-3-graph-ops-maturity
git worktree prune
```

## Plan Self-Review

- Spec coverage: graph layout, zoom/detail, tablet interaction, migration exercise, backup restore, consistency checks, browser acceptance, docs, merge/push are covered.
- Placeholder scan: no unresolved placeholder text remains.
- Type consistency: `layout_knowledge_graph`, `restore_backup`, and `consistency_check` are introduced before being referenced by repository and tests.
