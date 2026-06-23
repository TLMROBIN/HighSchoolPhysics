# Phase 2B Default Physics Taxonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a source-backed, versioned default high-school physics taxonomy covering six PEP 2019 textbook volumes, 15 ability tags, and 4 core-literacy dimensions with 14 elements, while preserving existing school edits and historical records.

**Architecture:** Checked-in JSON manifests are the runtime source of truth; a development-only extraction tool rebuilds the textbook manifest from locally available PDFs. A focused `taxonomy.py` module validates and transactionally installs the bundle, while additive SQLite migrations preserve Phase 2A data and record replacements instead of rewriting historical snapshots. Repository and HTTP layers expose installation status, source evidence, literacy CRUD, and active-only operational lists without changing the existing application architecture.

**Tech Stack:** Python 3 standard library, SQLite, `unittest`, `pdftotext` for the development extraction tool, server-rendered HTML, vanilla JavaScript/CSS.

---

## File Structure

**Create**

- `highschoolphysics/taxonomy.py`: manifest loading, structural validation, deterministic IDs, transactional installation, legacy replacement migration, and install summaries.
- `highschoolphysics/data/taxonomy_sources.json`: source identities, editions, document metadata, and optional local-path hints.
- `highschoolphysics/data/pep2019_knowledge.json`: 6 modules, 27 chapters, and 125 sections with stable default keys.
- `highschoolphysics/data/physics_abilities.json`: 15 flat ability tags.
- `highschoolphysics/data/physics_literacies.json`: 4 literacy dimensions and 14 elements.
- `tools/extract_pep2019_toc.py`: development-only PDF table-of-contents extraction and manifest writer.
- `tests/test_taxonomy.py`: manifest, validator, installer, idempotency, migration, provenance, and visibility tests.

**Modify**

- `highschoolphysics/db.py`: additive Phase 2B schema migration and demo seeding through the installer.
- `highschoolphysics/repository.py`: literacy CRUD, default-install action, install/source summaries, and active/all visibility rules.
- `highschoolphysics/server.py`: admin rendering and HTTP endpoints for defaults and literacy administration.
- `highschoolphysics/assets/app.js`: admin form submission and status refresh.
- `highschoolphysics/assets/app.css`: taxonomy summary, source evidence, and literacy controls.
- `tests/test_database.py`: legacy-schema upgrade coverage and fresh-demo count assertions.
- `tests/test_workflow.py`: repository CRUD, audit, and active-only operational behavior.
- `tests/test_http_integration.py`: admin authorization and JSON endpoint contracts.
- `tests/test_server.py`: rendered admin-page assertions.
- `README.md`: default taxonomy contents, installation behavior, and source provenance.
- `docs/superpowers/specs/2026-06-11-phase-2b-default-taxonomy-design.md`: implementation/acceptance status only after verification.

## Fixed Contracts

Use these constants and counts throughout the implementation:

```python
DEFAULT_TAXONOMY_VERSION = "pep-2019-physics-v1"
DEFAULT_ONTOLOGY_ID = "onto-pep2019-v1"

EXPECTED_COUNTS = {
    "knowledge_modules": 6,
    "knowledge_chapters": 27,
    "knowledge_sections": 125,
    "knowledge_total": 158,
    "abilities": 15,
    "literacy_dimensions": 4,
    "literacy_elements": 14,
    "literacy_total": 18,
}

LEGACY_KNOWLEDGE_REPLACEMENTS = {
    "kn-mechanics": "kn-pep2019-r1",
    "kn-kinematics": "kn-pep2019-r1-c02",
    "kn-newton": "kn-pep2019-r1-c04",
    "kn-newton-2": "kn-pep2019-r1-c04-s03",
    "kn-work": "kn-pep2019-r2-c08",
}

LEGACY_ABILITY_REPLACEMENTS = {
    "ab-modeling": "ab-context-modeling",
    "ab-force": "ab-force-analysis",
    "ab-equation": "ab-equation-building",
    "ab-calc": "ab-calculation",
}
```

Manifest records use the approved field names and this shape:

```json
{
  "manifest_version": 1,
  "ontology_label": "pep-2019-physics-v1",
  "source_keys": ["pep2019-r1"],
  "records": [
    {
      "default_key": "pep2019.r1.c01.s01",
      "id": "kn-pep2019-r1-c01-s01",
      "stable_code": "PEP2019.R1.C01.S01",
      "parent_id": "kn-pep2019-r1-c01",
      "name": "质点 参考系",
      "node_type": "textbook_section",
      "level": 3,
      "aliases": [],
      "description": "",
      "textbook_scope": "普通高中教科书物理必修第一册 第一章",
      "source_refs": [
        {
          "source_key": "pep2019-r1",
          "page_start": 8,
          "page_end": 12,
          "locator": "第一章 第1节",
          "evidence_summary": "教材目录与正文节标题"
        }
      ]
    }
  ]
}
```

`pep2019_knowledge.json` additionally contains `curriculum_topics` and
`curriculum_mappings`. Topics remain outside the textbook tree; mappings
reference `knowledge_node_id` and `curriculum_topic_id`.

Runtime code must not open or parse PDFs. Local source paths are optional evidence and may be absent on another machine.

### Task 1: Build and Validate Versioned Manifests

**Files:**
- Create: `tools/extract_pep2019_toc.py`
- Create: `highschoolphysics/data/taxonomy_sources.json`
- Create: `highschoolphysics/data/pep2019_knowledge.json`
- Create: `highschoolphysics/data/physics_abilities.json`
- Create: `highschoolphysics/data/physics_literacies.json`
- Create: `highschoolphysics/taxonomy.py`
- Test: `tests/test_taxonomy.py`

- [ ] **Step 1: Write failing manifest-count and hierarchy tests**

Add tests that import `load_default_taxonomy` and `validate_taxonomy_bundle`, then assert:

```python
class TaxonomyManifestTests(unittest.TestCase):
    def test_default_bundle_has_expected_counts_and_valid_hierarchy(self):
        bundle = load_default_taxonomy()
        validate_taxonomy_bundle(bundle)

        knowledge = bundle["knowledge"]["records"]
        literacy = bundle["literacy"]["records"]
        self.assertEqual(len(knowledge), 158)
        self.assertEqual(
            {level: sum(item["level"] == level for item in knowledge) for level in (1, 2, 3)},
            {1: 6, 2: 27, 3: 125},
        )
        self.assertEqual(len(bundle["abilities"]["records"]), 15)
        self.assertEqual(len(literacy), 18)
        self.assertEqual(sum(item["level"] == 1 for item in literacy), 4)
        self.assertEqual(sum(item["level"] == 2 for item in literacy), 14)

    def test_every_non_root_item_has_an_existing_parent(self):
        bundle = load_default_taxonomy()
        for collection in ("knowledge", "literacy"):
            records = bundle[collection]["records"]
            ids = {item["id"] for item in records}
            for item in records:
                if item["level"] > 1:
                    self.assertIn(item["parent_id"], ids)

    def test_validator_rejects_duplicate_default_keys(self):
        bundle = load_default_taxonomy()
        bundle["abilities"]["records"].append(dict(bundle["abilities"]["records"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate default_key"):
            validate_taxonomy_bundle(bundle)
```

- [ ] **Step 2: Run the tests and confirm the missing-module failure**

Run:

```bash
python3 -m unittest tests.test_taxonomy.TaxonomyManifestTests -v
```

Expected: `ModuleNotFoundError: No module named 'highschoolphysics.taxonomy'`.

- [ ] **Step 3: Implement the loader and strict validator**

Create `highschoolphysics/taxonomy.py` with:

```python
import copy
import json
from pathlib import Path

DATA_DIR = Path(__file__).with_name("data")
DEFAULT_TAXONOMY_VERSION = "pep-2019-physics-v1"

MANIFEST_FILES = {
    "sources": "taxonomy_sources.json",
    "knowledge": "pep2019_knowledge.json",
    "abilities": "physics_abilities.json",
    "literacy": "physics_literacies.json",
}

EXPECTED_COUNTS = {
    "knowledge": 158,
    "abilities": 15,
    "literacy": 18,
}


def _read_json(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_default_taxonomy(data_dir=DATA_DIR):
    bundle = {
        name: _read_json(Path(data_dir) / filename)
        for name, filename in MANIFEST_FILES.items()
    }
    return copy.deepcopy(bundle)


def _validate_no_parent_cycle(records_by_id, start_id):
    seen = set()
    current_id = start_id
    while current_id:
        if current_id in seen:
            raise ValueError("parent cycle detected at %s" % current_id)
        seen.add(current_id)
        current = records_by_id.get(current_id)
        current_id = current.get("parent_id") if current else None


def _validate_page_range(source_ref, page_count):
    start = source_ref.get("page_start")
    end = source_ref.get("page_end")
    if start is None and end is None:
        return
    if not isinstance(start, int) or not isinstance(end, int):
        raise ValueError("source page range must contain integers")
    if start < 1 or end < start:
        raise ValueError("source page range is invalid")
    if page_count is not None and end > page_count:
        raise ValueError("source page range exceeds source page count")


def validate_taxonomy_bundle(bundle):
    source_manifest = bundle["sources"]
    if source_manifest["manifest_version"] != 1:
        raise ValueError("sources manifest_version must be 1")
    if source_manifest["ontology_label"] != DEFAULT_TAXONOMY_VERSION:
        raise ValueError("sources ontology_label mismatch")

    for name, expected in EXPECTED_COUNTS.items():
        manifest = bundle[name]
        if manifest["manifest_version"] != 1:
            raise ValueError("%s manifest_version must be 1" % name)
        if manifest["ontology_label"] != DEFAULT_TAXONOMY_VERSION:
            raise ValueError("%s ontology_label mismatch" % name)
        if len(manifest["records"]) != expected:
            raise ValueError("%s item count must be %s" % (name, expected))

    source_records = bundle["sources"]["records"]
    source_keys = {item["source_key"] for item in source_records}
    source_page_counts = {
        item["source_key"]: item.get("page_count")
        for item in source_records
    }
    global_ids = []
    global_codes = []
    for name in ("knowledge", "abilities", "literacy"):
        records = bundle[name]["records"]
        keys = [item["default_key"] for item in records]
        ids = [item["id"] for item in records]
        codes = [item["stable_code"] for item in records]
        global_ids.extend(ids)
        global_codes.extend(codes)
        if len(keys) != len(set(keys)):
            raise ValueError("%s contains duplicate default_key" % name)
        if len(ids) != len(set(ids)):
            raise ValueError("%s contains duplicate id" % name)
        if len(codes) != len(set(codes)):
            raise ValueError("%s contains duplicate stable_code" % name)
        records_by_id = {item["id"]: item for item in records}
        for item in records:
            parent_id = item.get("parent_id")
            if parent_id:
                parent = records_by_id.get(parent_id)
                if parent is None:
                    raise ValueError("%s parent does not exist: %s" % (name, parent_id))
                if parent["level"] + 1 != item["level"]:
                    raise ValueError("%s parent level mismatch: %s" % (name, item["id"]))
            _validate_no_parent_cycle(records_by_id, item["id"])
            for source_ref in item.get("source_refs", []):
                source_key = source_ref["source_key"]
                if source_key not in source_keys:
                    raise ValueError("%s has unknown source key: %s" % (name, source_key))
                _validate_page_range(source_ref, source_page_counts[source_key])

    if len(global_ids) != len(set(global_ids)):
        raise ValueError("taxonomy ids must be globally unique")
    if len(global_codes) != len(set(global_codes)):
        raise ValueError("stable_code values must be globally unique")

    knowledge_manifest = bundle["knowledge"]
    topic_ids = {
        item["id"] for item in knowledge_manifest.get("curriculum_topics", [])
    }
    knowledge_ids = {
        item["id"] for item in knowledge_manifest["records"]
    }
    for mapping in knowledge_manifest.get("curriculum_mappings", []):
        if mapping["knowledge_node_id"] not in knowledge_ids:
            raise ValueError("curriculum mapping has unknown knowledge node")
        if mapping["curriculum_topic_id"] not in topic_ids:
            raise ValueError("curriculum mapping has unknown topic")
    for topic in knowledge_manifest.get("curriculum_topics", []):
        for source_ref in topic.get("source_refs", []):
            source_key = source_ref["source_key"]
            if source_key not in source_keys:
                raise ValueError("curriculum topic has unknown source key")
            _validate_page_range(source_ref, source_page_counts[source_key])

    knowledge = bundle["knowledge"]["records"]
    level_counts = {
        level: sum(item["level"] == level for item in knowledge)
        for level in (1, 2, 3)
    }
    if level_counts != {1: 6, 2: 27, 3: 125}:
        raise ValueError("knowledge hierarchy counts mismatch: %r" % level_counts)

    literacy = bundle["literacy"]["records"]
    literacy_counts = {
        level: sum(item["level"] == level for item in literacy)
        for level in (1, 2)
    }
    if literacy_counts != {1: 4, 2: 14}:
        raise ValueError("literacy hierarchy counts mismatch: %r" % literacy_counts)
```

- [ ] **Step 4: Implement the development-only TOC extraction tool and generate manifests**

`tools/extract_pep2019_toc.py` must:

1. Accept `--textbook-dir` and `--output`.
2. Map the six exact PDF filenames to `r1`, `r2`, `r3`, `e1`, `e2`, `e3`.
3. Run `pdftotext -layout <pdf> -`.
4. Read enough front matter to capture the complete table of contents.
5. Match chapter headings with `^第[一二三四五六七八九十]+章` and section headings with `^[0-9]+\\s+`.
6. Normalize whitespace without changing Chinese title text.
7. Emit deterministic IDs and stable codes.
8. Refuse output unless counts equal 6/27/125.

Generate the checked-in manifest with:

```bash
python3 tools/extract_pep2019_toc.py \
  --textbook-dir "/Users/binyu/我的云端硬盘/01_教学与物理/10_教材课标与参考资料/教材课本" \
  --output highschoolphysics/data/pep2019_knowledge.json
```

Create the three other manifests directly from the accepted design:

- sources: six PEP 2019 volumes, the 2017 curriculum standard revised in 2020, and a school-authored ability framework source;
- abilities: information extraction, context modeling, force analysis, process segmentation, model construction, critical conditions, conservation thinking, graph transformation, equation building, calculation, experiment design, data processing, error analysis, reasoning/argumentation, contextual transfer;
- literacies: physical concepts, scientific thinking, scientific inquiry, scientific attitude/responsibility, with the accepted 14 child elements.

Add curriculum-standard topic records and textbook mappings to
`pep2019_knowledge.json`. Every mapping must resolve to one checked-in topic and
one checked-in textbook node; every topic and mapping must carry curriculum
standard `source_refs`.

- [ ] **Step 5: Run manifest tests and inspect generated data**

Run:

```bash
python3 -m unittest tests.test_taxonomy.TaxonomyManifestTests -v
python3 -m json.tool highschoolphysics/data/pep2019_knowledge.json >/dev/null
python3 -m json.tool highschoolphysics/data/physics_abilities.json >/dev/null
python3 -m json.tool highschoolphysics/data/physics_literacies.json >/dev/null
```

Expected: all tests pass and all JSON commands exit `0`.

- [ ] **Step 6: Commit**

```bash
git add tools/extract_pep2019_toc.py highschoolphysics/taxonomy.py highschoolphysics/data tests/test_taxonomy.py
git commit -m "feat: add validated default physics taxonomy manifests"
```

### Task 2: Add the Phase 2B Additive Database Migration

**Files:**
- Modify: `highschoolphysics/db.py`
- Modify: `tests/test_database.py`
- Test: `tests/test_taxonomy.py`

- [ ] **Step 1: Write failing fresh-schema and legacy-upgrade tests**

Assert these columns and tables exist after `initialize_database`:

```python
expected_tables = {
    "taxonomy_sources",
    "taxonomy_source_links",
    "curriculum_topics",
    "knowledge_curriculum_mappings",
    "taxonomy_replacements",
    "literacy_tags",
}
self.assertTrue(expected_tables.issubset(actual_tables))

knowledge_columns = table_columns(conn, "knowledge_nodes")
self.assertTrue({"default_key", "is_default"}.issubset(knowledge_columns))

ability_columns = table_columns(conn, "ability_tags")
self.assertTrue({"default_key", "is_default", "change_note"}.issubset(ability_columns))
self.assertEqual(conn.execute("pragma user_version").fetchone()[0], 2)
```

The legacy test must create a database containing pre-Phase-2B `knowledge_nodes` and `ability_tags`, insert one row in each, call `initialize_database`, and prove both rows and their original values remain.

- [ ] **Step 2: Run the tests and confirm missing schema failures**

Run:

```bash
python3 -m unittest tests.test_database tests.test_taxonomy -v
```

Expected: failures naming `default_key` or missing Phase 2B tables.

- [ ] **Step 3: Implement additive migration helpers**

Add:

```python
SCHEMA_VERSION = 2


def _column_names(conn, table):
    return {row["name"] for row in conn.execute("pragma table_info(%s)" % table)}


def _ensure_column(conn, table, definition):
    column = definition.split()[0]
    if column not in _column_names(conn, table):
        conn.execute("alter table %s add column %s" % (table, definition))
```

After the existing `executescript`, call:

```python
_ensure_column(conn, "knowledge_nodes", "default_key text")
_ensure_column(conn, "knowledge_nodes", "is_default integer not null default 0")
_ensure_column(conn, "ability_tags", "default_key text")
_ensure_column(conn, "ability_tags", "is_default integer not null default 0")
_ensure_column(conn, "ability_tags", "change_note text not null default ''")
```

Create the six new tables from the approved design, including:

```sql
create table if not exists literacy_tags (
    id text primary key,
    school_id text not null references schools(id),
    ontology_version_id text not null references knowledge_ontology_versions(id),
    parent_id text references literacy_tags(id),
    default_key text,
    stable_code text not null,
    name text not null,
    level integer not null,
    description text not null default '',
    source text not null default '',
    enabled integer not null default 1,
    is_default integer not null default 0,
    deleted_at text,
    version integer not null default 1,
    change_note text not null default ''
);
```

Create school-scoped partial unique indexes for non-null default keys, and finish with:

```python
conn.execute("pragma user_version = %d" % SCHEMA_VERSION)
conn.commit()
```

- [ ] **Step 4: Run database tests**

Run:

```bash
python3 -m unittest tests.test_database tests.test_taxonomy -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/db.py tests/test_database.py tests/test_taxonomy.py
git commit -m "feat: add phase 2b taxonomy schema migration"
```

### Task 3: Implement Transactional Default Installation and Legacy Migration

**Files:**
- Modify: `highschoolphysics/taxonomy.py`
- Modify: `highschoolphysics/db.py`
- Modify: `tests/test_taxonomy.py`

- [ ] **Step 1: Write failing installer tests**

Cover:

```python
def test_install_creates_expected_default_records(self):
    summary = install_default_taxonomy(
        self.conn,
        school_id="school-demo",
        actor_id="user-admin",
        publish=True,
    )
    self.assertEqual(summary["knowledge"]["created"], 158)
    self.assertEqual(summary["abilities"]["created"], 15)
    self.assertEqual(summary["literacy"]["created"], 18)

def test_reinstall_is_idempotent_and_preserves_school_edits(self):
    install_default_taxonomy(self.conn, "school-demo", "user-admin", publish=True)
    self.conn.execute(
        "update knowledge_nodes set name = ?, enabled = 0 where default_key = ?",
        ("校本修订名称", "pep2019.r1.c01.s01"),
    )
    second = install_default_taxonomy(self.conn, "school-demo", "user-admin", publish=True)
    row = self.conn.execute(
        "select name, enabled from knowledge_nodes where default_key = ?",
        ("pep2019.r1.c01.s01",),
    ).fetchone()
    self.assertEqual(dict(row), {"name": "校本修订名称", "enabled": 0})
    self.assertEqual(second["knowledge"]["created"], 0)

def test_legacy_live_tags_move_to_replacements_but_snapshot_json_is_unchanged(self):
    before_snapshot = self.conn.execute(
        "select knowledge_snapshot_json from wrong_questions limit 1"
    ).fetchone()[0]
    install_default_taxonomy(self.conn, "school-demo", "user-admin", publish=True)
    migrated = self.conn.execute(
        "select tag_id from question_tags where id = 'tag-q2-kn'"
    ).fetchone()[0]
    after_snapshot = self.conn.execute(
        "select knowledge_snapshot_json from wrong_questions limit 1"
    ).fetchone()[0]
    self.assertEqual(migrated, "kn-pep2019-r1-c04-s03")
    self.assertEqual(after_snapshot, before_snapshot)

def test_existing_school_install_creates_draft_without_replacing_active_version(self):
    old_active = self.conn.execute(
        "select id from knowledge_ontology_versions where status = 'active'"
    ).fetchone()[0]
    install_default_taxonomy(
        self.conn, "school-demo", "user-admin", publish=False
    )
    self.assertEqual(
        self.conn.execute(
            "select id from knowledge_ontology_versions where status = 'active'"
        ).fetchone()[0],
        old_active,
    )
    self.assertEqual(
        self.conn.execute(
            "select status from knowledge_ontology_versions "
            "where id = 'onto-pep2019-v1'"
        ).fetchone()[0],
        "draft",
    )
```

Also test a malformed bundle rolls back all inserts.

- [ ] **Step 2: Run installer tests and confirm failures**

Run:

```bash
python3 -m unittest tests.test_taxonomy -v
```

Expected: import or missing-function failures for `install_default_taxonomy`.

- [ ] **Step 3: Implement one-transaction installation**

Add:

```python
def install_default_taxonomy(
    conn,
    school_id,
    actor_id,
    publish=False,
    local_source_root=None,
    bundle=None,
):
    bundle = bundle or load_default_taxonomy()
    validate_taxonomy_bundle(bundle)
    summary = {
        "version": DEFAULT_TAXONOMY_VERSION,
        "knowledge": {"created": 0, "existing": 0},
        "abilities": {"created": 0, "existing": 0},
        "literacy": {"created": 0, "existing": 0},
        "replacements": 0,
    }
    conn.execute("savepoint install_default_taxonomy")
    try:
        ontology_id = _ensure_default_ontology(conn, school_id, publish)
        _install_sources(conn, school_id, bundle["sources"], local_source_root)
        _install_knowledge(conn, school_id, ontology_id, bundle["knowledge"], summary)
        _install_abilities(conn, school_id, ontology_id, bundle["abilities"], summary)
        _install_literacy(conn, school_id, ontology_id, bundle["literacy"], summary)
        _install_curriculum(conn, school_id, ontology_id, bundle["knowledge"])
        _record_and_apply_replacements(conn, school_id, ontology_id, summary)
        _record_install_audit(conn, school_id, actor_id, summary)
        conn.execute("release savepoint install_default_taxonomy")
    except Exception:
        conn.execute("rollback to savepoint install_default_taxonomy")
        conn.execute("release savepoint install_default_taxonomy")
        raise
    return summary
```

Insertion rules:

- Resolve parents by `default_key`, not input order or display name.
- Match existing defaults by `(school_id, default_key)`.
- On a match, preserve editable fields: name, aliases, description, source, enabled, deleted_at, and change_note.
- Insert provenance links if missing.
- Never delete records absent from a newer manifest.
- Never mutate `wrong_questions.*_snapshot_json`, graded responses, or audit history.
- Preserve old knowledge and ability entities, disable them, and update only
  current operational `question_tags` for known replacements.
- Record old/new IDs in `taxonomy_replacements`.
- Add one `audit_events` row with action `default_taxonomy_installed`.
- When `publish=False`, create or reuse the default ontology as `draft` and
  leave the school's current active version unchanged.
- Import curriculum topics and mappings idempotently; mappings never alter the
  textbook hierarchy.

- [ ] **Step 4: Replace demo hardcoding with the installer**

In `seed_demo_data`:

1. Keep school/users/classes/policies and the mastery version.
2. Remove the five hardcoded knowledge nodes and four hardcoded abilities.
3. Call `install_default_taxonomy(conn, school_id, "user-admin", publish=True)`.
4. Update sample edges and tags to new IDs:

```python
"kn-pep2019-r1-c02"
"kn-pep2019-r1-c04"
"kn-pep2019-r1-c04-s03"
"kn-pep2019-r2-c08"
"ab-force-analysis"
"ab-equation-building"
"ab-calculation"
```

5. Keep the sample assessment behavior and historical snapshot payloads deterministic.

- [ ] **Step 5: Run focused and full regression tests**

Run:

```bash
python3 -m unittest tests.test_taxonomy tests.test_database tests.test_workflow -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add highschoolphysics/taxonomy.py highschoolphysics/db.py tests/test_taxonomy.py tests/test_database.py tests/test_workflow.py
git commit -m "feat: install and migrate default physics taxonomy"
```

### Task 4: Add Repository APIs, Literacy CRUD, and Visibility Rules

**Files:**
- Modify: `highschoolphysics/repository.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_taxonomy.py`

- [ ] **Step 1: Write failing repository behavior tests**

Test:

- `literacy_tags()` returns enabled, non-deleted rows only.
- `all_literacy_tags()` includes disabled rows and parent names.
- create/update/disable literacy actions are audited.
- `install_default_taxonomy(actor_id, publish=False)` requires an admin actor.
- `taxonomy_summary()` returns expected installed/active/default counts and source rows.
- disabled knowledge, ability, and literacy rows do not appear in operational selectors.
- disabled defaults remain visible in the admin dashboard.

Use this expected summary shape:

```python
{
    "version": "pep-2019-physics-v1",
    "installed": True,
    "knowledge": {"total": 158, "active": 158},
    "abilities": {"total": 15, "active": 15},
    "literacy": {"total": 18, "active": 18},
    "sources": [...],
}
```

- [ ] **Step 2: Run focused tests and confirm missing-method failures**

Run:

```bash
python3 -m unittest tests.test_workflow tests.test_taxonomy -v
```

Expected: `AttributeError` for the new repository methods.

- [ ] **Step 3: Add repository methods**

Implement:

```python
def literacy_tags(self):
    ...

def all_literacy_tags(self):
    ...

def create_literacy_tag(
    self, actor_id, stable_code, name, parent_id=None,
    description="", source="教师校本", change_note="", enabled=True
):
    ...

def update_literacy_tag(
    self, actor_id, literacy_id, name, description="",
    source="", change_note=""
):
    ...

def set_literacy_tag_enabled(
    self, actor_id, literacy_id, enabled, change_note=""
):
    ...

def install_default_taxonomy(self, actor_id, publish=False):
    if self.user_by_id(actor_id)["role"] != "admin":
        raise PermissionDenied("Admin role required")
    return taxonomy.install_default_taxonomy(
        self.conn,
        self.school_id_for_actor(actor_id),
        actor_id,
        publish=publish,
    )

def taxonomy_summary(self):
    ...
```

Follow existing knowledge/ability audit patterns. Validate a literacy parent belongs to the same school, has level 1, and is not deleted. A child receives level 2; a root receives level 1.

Add `literacy_tags`, `taxonomy_summary`, and `taxonomy_sources` to `admin_dashboard`.

- [ ] **Step 4: Enforce active-only operational visibility**

Review every operational query and selector. Use `enabled = 1 and deleted_at is null` for knowledge/ability/literacy lists used by:

- LLM candidate generation;
- question tag review;
- teacher/student graph and filters;
- mastery diagnostics;
- new-tag selectors.

Do not filter historical snapshots or admin `all_*` methods.

- [ ] **Step 5: Run focused and full tests**

Run:

```bash
python3 -m unittest tests.test_workflow tests.test_taxonomy -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add highschoolphysics/repository.py tests/test_workflow.py tests/test_taxonomy.py
git commit -m "feat: manage literacy tags and taxonomy visibility"
```

### Task 5: Add Admin HTTP Contracts

**Files:**
- Modify: `highschoolphysics/server.py`
- Modify: `tests/test_http_integration.py`

- [ ] **Step 1: Write failing integration tests**

Cover these routes:

```text
POST /api/admin/taxonomy/install
POST /api/admin/literacy-tag
POST /api/admin/literacy-tag/update
```

Assertions:

- anonymous requests return `401`;
- teacher/student requests return `403`;
- admin install returns `200`, `ok: true`, the version, and counts;
- a second install is idempotent;
- literacy create/update/disable returns the saved record;
- malformed or missing fields return the existing `400` domain-error JSON contract.

- [ ] **Step 2: Run tests and confirm route failures**

Run:

```bash
python3 -m unittest tests.test_http_integration -v
```

Expected: `404` or missing-result failures for the new endpoints.

- [ ] **Step 3: Add explicit admin-only route branches**

Add:

```python
elif path == "/api/admin/taxonomy/install":
    if user["role"] != "admin":
        raise PermissionDenied("Admin role required")
    result = repo.install_default_taxonomy(
        actor_id=user["id"],
        publish=truthy(payload.get("publish", "0")),
    )
    self._send_json({
        "ok": True,
        "message": "默认物理体系已安装",
        "result": result,
    })
```

Add literacy create/update branches matching existing knowledge and ability request conventions. Require `change_note` when disabling an item.

- [ ] **Step 4: Run integration and full tests**

Run:

```bash
python3 -m unittest tests.test_http_integration -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/server.py tests/test_http_integration.py
git commit -m "feat: expose admin taxonomy endpoints"
```

### Task 6: Render Default Taxonomy Status and Literacy Administration

**Files:**
- Modify: `highschoolphysics/server.py`
- Modify: `highschoolphysics/assets/app.js`
- Modify: `highschoolphysics/assets/app.css`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write failing render tests**

Assert the admin page contains:

```python
self.assertIn("默认物理体系", html)
self.assertIn("158 个知识节点", html)
self.assertIn("15 个能力标签", html)
self.assertIn("18 个核心素养标签", html)
self.assertIn("来源与版本", html)
self.assertIn("安装或补齐默认体系", html)
self.assertIn("核心素养管理", html)
self.assertIn("物理观念", html)
self.assertIn("科学思维", html)
```

Also assert source rows are escaped and local filesystem paths are not rendered to non-admin pages.

- [ ] **Step 2: Run render tests and confirm missing-content failures**

Run:

```bash
python3 -m unittest tests.test_server -v
```

Expected: assertions fail because the new admin sections are absent.

- [ ] **Step 3: Add concise admin status and source evidence**

Render a summary section before detailed tables:

```html
<section class="panel span-2 taxonomy-overview">
  <div class="panel-head">
    <div>
      <h2>默认物理体系</h2>
      <p>人民教育出版社 2019 版六册教材目录 + 课程标准核心素养。</p>
    </div>
    <button data-action="install-default-taxonomy">安装或补齐默认体系</button>
  </div>
  <div class="metric-strip">...</div>
  <details>
    <summary>来源与版本</summary>
    <table>...</table>
  </details>
</section>
```

Keep the 158-row knowledge table usable by adding a module filter and a text search. Render default/custom badges and active/disabled states. Do not draw all 158 nodes in the SVG graph; render level 1-2 nodes in the overview graph and keep all nodes in the table.

- [ ] **Step 4: Add literacy create/update controls and JavaScript**

Use the same form/result-status conventions as existing admin forms. Add:

- root/child parent selector;
- stable code, name, description, source, change note;
- enabled checkbox;
- install confirmation text explaining that existing edits are preserved.

On successful POST, show the returned message in `#action-status` and reload the page.

- [ ] **Step 5: Add focused CSS**

Add responsive styles for:

```css
.taxonomy-overview {}
.metric-strip {}
.taxonomy-source-table {}
.taxonomy-filter-bar {}
.taxonomy-badge {}
.taxonomy-table-scroll {}
.literacy-grid {}
```

At `1600x900`, the summary, source disclosure, filters, and first table rows must be visible without horizontal page overflow. Tables may scroll within their panel.

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
git commit -m "feat: add admin default taxonomy workspace"
```

### Task 7: Document Behavior and Run Automated Acceptance

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-11-phase-2b-default-taxonomy-design.md`
- Modify: affected tests only if acceptance exposes a real defect

- [ ] **Step 1: Update operator documentation**

Document:

- fresh demo databases automatically receive the published default taxonomy;
- non-demo databases require an admin to install defaults;
- reinstall is idempotent and preserves school edits/disabled states;
- live question tags are migrated through replacement mappings;
- historical snapshots are immutable;
- checked-in manifests are runtime truth;
- `pdftotext` is needed only to rebuild the knowledge manifest;
- source files may be unavailable on another deployment.

- [ ] **Step 2: Run static and automated checks**

Run:

```bash
rg -n "TO[D]O|TB[D]|implement la[t]er|fill in deta[i]ls" \
  highschoolphysics tools tests README.md \
  docs/superpowers/specs/2026-06-11-phase-2b-default-taxonomy-design.md
python3 -m compileall -q highschoolphysics tools tests
python3 -m unittest discover -s tests -v
```

Expected: placeholder search has no implementation gaps, compilation exits `0`, and the full suite passes.

- [ ] **Step 3: Verify a fresh demo database directly**

Run:

```bash
tmpdir="$(mktemp -d /tmp/hsp-phase2b-auto.XXXXXX)"
python3 -c \
  'import sys; from highschoolphysics.db import connect, initialize_database, seed_demo_data; c=connect(sys.argv[1]); initialize_database(c); seed_demo_data(c); c.close()' \
  "$tmpdir/demo.sqlite3"
sqlite3 "$tmpdir/demo.sqlite3" \
  "select level, count(*) from knowledge_nodes where is_default=1 group by level order by level;"
sqlite3 "$tmpdir/demo.sqlite3" \
  "select count(*) from ability_tags where is_default=1; select count(*) from literacy_tags where is_default=1;"
```

Expected:

```text
1|6
2|27
3|125
15
18
```

- [ ] **Step 4: Commit documentation**

```bash
git add README.md docs/superpowers/specs/2026-06-11-phase-2b-default-taxonomy-design.md
git commit -m "docs: record phase 2b taxonomy behavior"
```

### Task 8: Browser Acceptance at Classroom Viewport

**Files:**
- Modify: only files required to fix observed acceptance defects
- Record: `docs/superpowers/specs/2026-06-11-phase-2b-default-taxonomy-design.md`

- [ ] **Step 1: Start a fresh demo server on a free port**

Run:

```bash
tmpdir="$(mktemp -d /tmp/hsp-phase2b-browser.XXXXXX)"
python3 -m highschoolphysics.server --demo \
  --host 127.0.0.1 --port 8879 --db "$tmpdir/demo.sqlite3"
```

Expected: server listens on `http://127.0.0.1:8879`.

- [ ] **Step 2: Verify the admin flow in Browser at 1600x900**

Use the Browser plugin:

1. Open `http://127.0.0.1:8879/login`.
2. Log in as `admin` / `admin123`.
3. Confirm summary counts are 158/15/18.
4. Expand source evidence and confirm the PEP volumes and curriculum standard are named.
5. Filter knowledge nodes to one module and search for `牛顿第二定律`.
6. Disable and restore one default literacy element with a change note.
7. Install defaults again and confirm counts do not increase and the edited state is preserved.
8. Confirm no horizontal page overflow at 1600x900.

- [ ] **Step 3: Verify non-admin isolation**

1. Log out and log in as `teacher_li` / `teacher123`.
2. Confirm teacher pages contain active taxonomy data needed for tagging/diagnostics.
3. Confirm `/admin` returns `403`.
4. POST `/api/admin/taxonomy/install` and confirm `403`.
5. Confirm disabled taxonomy items do not appear in operational selectors.

- [ ] **Step 4: Run final verification after browser fixes**

Run:

```bash
python3 -m compileall -q highschoolphysics tools tests
python3 -m unittest discover -s tests -v
git status --short
```

Expected: compilation exits `0`, all tests pass, and status contains only intentional Phase 2B changes.

- [ ] **Step 5: Record acceptance and commit fixes**

Append exact date, database path, URL, viewport, verified flows, test count, and any residual limits to the Phase 2B design document.

```bash
git add highschoolphysics tools tests README.md docs/superpowers/specs/2026-06-11-phase-2b-default-taxonomy-design.md
git commit -m "docs: record phase 2b acceptance"
```

## Completion Gate

Phase 2B is complete only when all of the following are true:

- checked-in manifests validate at runtime without PDF access;
- knowledge counts are exactly 6 modules, 27 chapters, 125 sections;
- ability count is exactly 15;
- literacy counts are exactly 4 dimensions and 14 elements;
- a fresh demo install publishes defaults automatically;
- an existing database upgrades additively;
- repeated installation creates no duplicates and preserves school edits;
- live tags migrate through recorded replacements;
- historical snapshots remain byte-for-byte unchanged;
- disabled records are hidden operationally but visible to admins;
- all write endpoints are admin-only and audited;
- the full `unittest` suite passes;
- the admin and teacher flows are browser-verified at `1600x900`.
