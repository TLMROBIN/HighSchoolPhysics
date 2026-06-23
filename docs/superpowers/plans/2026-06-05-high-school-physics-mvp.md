# High School Physics MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable single-school MVP that demonstrates the blueprint's required loop: account/class setup, versioned knowledge and ability tags, teacher-reviewed AI candidates, assessment snapshots, answer review, objective grading, wrong-question generation, student mastery marking, teacher diagnostics, audit records, and A4-ready export.

**Architecture:** A dependency-light Python 3.9 web application uses SQLite as the source of truth, standard-library HTTP serving, server-rendered HTML, and focused domain modules for grading, permissions, LLM candidate caching, repositories, and export generation. The app seeds a complete demo school so acceptance workflows can be verified immediately, while all writes use real persisted records.

**Tech Stack:** Python 3.9 standard library, SQLite, unittest, HTML/CSS/JavaScript, no external runtime dependencies.

---

## File Structure

- Create `pyproject.toml`: project metadata and unittest command hints.
- Create `README.md`: run instructions, demo accounts, acceptance workflow, scope notes.
- Create `highschoolphysics/__init__.py`: package marker and version.
- Create `highschoolphysics/db.py`: SQLite schema, connection helpers, seed data, reset utility.
- Create `highschoolphysics/security.py`: password hashing, session token hashing, local key masking helper.
- Create `highschoolphysics/auth.py`: login/session handling and subject-resource-operation permission checks.
- Create `highschoolphysics/grading.py`: multiple-choice, multi-select, fill-in exact/tolerance grading.
- Create `highschoolphysics/llm.py`: deterministic candidate tag generation and cache persistence contract.
- Create `highschoolphysics/repository.py`: workflow operations for ontology, questions, assessments, scans, grading, wrong questions, mastery marks, diagnostics, admin import, audit, backup.
- Create `highschoolphysics/exporting.py`: A4 printable wrong-question-book HTML export.
- Create `highschoolphysics/server.py`: HTTP routes, JSON APIs, role-aware HTML pages, static asset serving.
- Create `highschoolphysics/assets/app.css`: tablet-first student UI, dense teacher/admin UI, printable A4 styles.
- Create `highschoolphysics/assets/app.js`: fetch helpers, workflow actions, local graph rendering, tabs/forms.
- Create `tests/test_grading.py`: RED/GREEN tests for objective grading rules.
- Create `tests/test_security_auth.py`: RED/GREEN tests for password/session and permission boundaries.
- Create `tests/test_workflow.py`: RED/GREEN tests for tag review, assessment grading, wrong-question generation, mastery, diagnostics, export, backup.

## Tasks

### Task 1: Failing Behavioral Tests

**Files:**
- Create: `tests/test_grading.py`
- Create: `tests/test_security_auth.py`
- Create: `tests/test_workflow.py`

- [ ] Write tests that express the MVP contract before production code exists.
- [ ] Run `python3 -m unittest discover -s tests -v`.
- [ ] Expected result: tests fail because `highschoolphysics` modules are missing.

### Task 2: Domain Foundation

**Files:**
- Create: `highschoolphysics/__init__.py`
- Create: `highschoolphysics/security.py`
- Create: `highschoolphysics/grading.py`
- Create: `highschoolphysics/llm.py`

- [ ] Implement password/session hashing and masked key storage helpers.
- [ ] Implement objective grading functions with exact, tolerance, and multi-answer behavior.
- [ ] Implement deterministic LLM candidate generation returning candidate tags, confidence, rationale, prompt version, model version, and cache key.
- [ ] Run targeted tests until security, grading, and candidate tests pass.

### Task 3: SQLite Schema And Workflow Repository

**Files:**
- Create: `highschoolphysics/db.py`
- Create: `highschoolphysics/auth.py`
- Create: `highschoolphysics/repository.py`
- Create: `highschoolphysics/exporting.py`

- [ ] Implement tables for users, identity/provider placeholders, classes, roles, policies, ontology versions, knowledge nodes/edges, ability tags, questions, question tags/candidates, assessments, snapshots, answer cards, scan batches, responses, wrong questions, mastery marks, parse/export/backup tasks, consent records, LLM providers, and audits.
- [ ] Seed one school, one class, one teacher, one admin, three students, ontology nodes, ability tags, questions, an assessment, a scan batch, and answer-card responses.
- [ ] Implement teacher review, grading publication, low-confidence conflict handling, grading override audit, wrong-question generation, student mastery marking, teacher diagnostics, export generation, backup export, and admin import flows.
- [ ] Run workflow tests until they pass.

### Task 4: Web Server And Tablet UI

**Files:**
- Create: `highschoolphysics/server.py`
- Create: `highschoolphysics/assets/app.css`
- Create: `highschoolphysics/assets/app.js`
- Create: `README.md`
- Create: `pyproject.toml`

- [ ] Implement `/login`, `/logout`, `/app`, `/teacher`, `/admin`, `/export/wrong-book/<assessment_id>`, `/backup/download`, and JSON workflow APIs.
- [ ] Build student bottom-navigation screens for wrong questions, redo queue, mastery, and recent assessments.
- [ ] Build teacher dense management screens for assessment status, tag review, scan review, grading, diagnostics, knowledge graph/list, and export.
- [ ] Build admin screens for users/classes, LLM provider configuration, parse/export task queues, audit, privacy/retention, and backup.
- [ ] Document demo accounts and verification commands.

### Task 5: Verification

**Files:**
- Verify: all created source, tests, and generated runtime data.

- [ ] Run `python3 -m unittest discover -s tests -v`.
- [ ] Run `python3 -m highschoolphysics.server --host 127.0.0.1 --port 8765 --db data/highschoolphysics.sqlite3`.
- [ ] Open `http://127.0.0.1:8765/` in the in-app browser and verify login plus teacher/student/admin pages render.
- [ ] Verify API acceptance paths for login, tag approval, grading, mastery mark, export, and backup.
- [ ] Stop the server and report evidence.

## Self-Review

- Spec coverage: The plan intentionally implements the blueprint's MVP cut line and adds schema placeholders for later phases, rather than pretending to finish full MinerU/PaddleOCR/SSO/automatic recommendation production integrations.
- Placeholder scan: No task depends on unresolved TODOs; external OCR/LLM calls are represented as auditable candidate/task records with deterministic local behavior for MVP validation.
- Type consistency: Domain module names, table concepts, and route names are consistent across tasks.
