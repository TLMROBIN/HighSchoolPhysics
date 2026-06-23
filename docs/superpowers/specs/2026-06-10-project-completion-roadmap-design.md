# HighSchoolPhysics Project Completion Roadmap Design

## Context

The current repository contains the runnable MVP and completed Phase 2A admin ontology management. The June 10 review identified real authorization, publication-boundary, regrading, error-handling, and workflow gaps. Those findings must be resolved before more classes, papers, questions, and student records amplify the blast radius.

This design turns the accepted blueprint, the staged plan, and the corrected review findings into one completion route. It deliberately separates defect repair from feature expansion so every stage leaves the system in a demonstrably safer and more useful state.

## Completion Target

The project is complete when all of the following are true:

- Phase 2A.1, Phases 2B through 2G, and Phase 3+ meet their documented acceptance criteria.
- The student, teacher, and admin workflows are verified through real HTTP requests and browser-visible behavior, not render-function tests alone.
- Authorization is enforced at both the HTTP boundary and repository methods that read or mutate sensitive student/class data.
- Published assessment history, question snapshots, grading revisions, wrong-question records, and mastery evidence remain explainable and reproducible.
- Default knowledge, ability, and literacy systems are sourced, editable, versioned, and filtered correctly by active state.
- Real question import, parser review, answer-card OCR review, grading, wrong-question redo, mastery calculation, and teacher/admin analytics form one working chain.
- The graph-first student experience and diagnostic teacher experience work with non-demo data volumes.
- Regression tests, compile checks, database migration checks, and browser acceptance all pass from a fresh database.

The future StudyAgent/edudimu unified identity integration is not part of this completion target because the blueprint explicitly treats it as a later platform-integration phase. Production credentials for external LLM, MinerU API, and identity providers are also not required; the project must instead provide tested configuration, failure, and fallback behavior.

## Approaches Considered

### 1. Feature-First Continuation

Proceed directly to Phase 2B and defer review findings until the new workflows need them.

This creates visible progress quickly, but it builds multi-class and real-data features on authorization and publication rules already known to be unsafe. It is rejected.

### 2. Big-Bang Completion

Implement security, taxonomy, question parsing, OCR, mastery, analytics, and graph improvements in one long branch.

This minimizes planning boundaries but makes regressions difficult to isolate and leaves no trustworthy intermediate acceptance point. It is rejected.

### 3. Gate-First Staged Completion

Establish a Phase 2A.1 stability gate, then execute 2B through 2G in dependency order. Each phase has its own implementation plan, TDD cycle, browser proof, and commit boundary.

This is the selected approach because it fixes known data and privacy risks first while preserving the existing staged product direction.

## Stage Architecture

### Phase 2A.1: Security And Data Integrity Gate

This phase is a release blocker for all later feature work.

Authorization:

- Introduce HTTP integration tests that log in through the real handler and exercise cookies, status codes, and response bodies.
- Resolve the protected resource before authorization. Assessment, response, wrong-question, and student identifiers must be mapped to their owning class or student before `AuthService.can()` is called.
- Scope teacher dashboards, assessment lists, review queues, diagnostics, exports, grading, and response review to assigned classes.
- Force student export and mastery operations to the authenticated student regardless of submitted identifiers.
- Hide assessments, scores, and wrong questions from students until the assessment is published.
- Keep repository-level ownership checks on sensitive reads and writes so a future route cannot bypass policy accidentally.

Published-data integrity:

- Reject ordinary grading requests for an already published or archived assessment.
- Preserve wrong-question and mastery rows when a repeated request is rejected.
- Record explicit grading corrections and revision history in Phase 2D, where the complete assessment workflow is built.

Request and runtime reliability:

- Return stable JSON errors for malformed payloads, missing fields, validation failures, forbidden operations, and unexpected server errors.
- Roll back failed transactions and avoid returning internal exception details to clients.
- Enable SQLite WAL mode once per database and retain a busy timeout at least as large as Python's current 5000 ms default.
- Add a focused concurrent-write test before claiming classroom-write readiness.

Identity:

- Enforce `must_change_password` after temporary-password login.
- Add password-change and scoped password-reset flows with identity audit records.
- Keep demo credentials behind an explicit demo-mode presentation path.

Acceptance:

- HTTP tests prove students cannot read or modify another student's data.
- HTTP tests prove teachers cannot read, export, review, or grade another class.
- HTTP tests prove unpublished results are invisible to students.
- Regrading a published assessment returns a conflict and preserves mastery records.
- Invalid POST bodies return structured 400 responses; forbidden requests return 403.
- Temporary-password users cannot enter role pages before changing their password.
- Teacher and admin password resets enforce scope and write identity audit records.
- Non-demo startup does not render seeded account passwords on the login page.
- Fresh and existing databases open successfully with the connection settings.

### Phase 2B: Default Physics Taxonomy, Ability, And Literacy Systems

Import verified textbook hierarchy and authoritative ability/literacy defaults. Add literacy-tag persistence, evidence metadata, admin CRUD, ontology versioning, and active/inactive visibility rules. This is schema, import, UI, and provenance work rather than a data-only task.

### Phase 2C: Real Question Bank And Document Parsing

Build question CRUD, original-paper provenance, MarkItDown/MinerU task adapters, unified parser output, split-review queues, and editable confirmation of up to three knowledge, ability, and literacy tags per family.

### Phase 2D: Assessment, OCR, Grading Revision, And Wrong-Question Loop

Build paper assembly, answer-card templates, PaddleOCR review, objective grading, explicit grading revisions, wrong-question redo attempts, error-reason tagging, and configurable print exports. Redo attempts remain separate evidence; mastery aggregation must declare whether and how they affect calculated mastery.

### Phase 2E: Deterministic Mastery Metrics

Calculate tag-level attempts, correct, wrong, blank, and rate values from versioned evidence. Keep assessment attempts and redo attempts distinguishable. Implement the accepted thresholds and preserve manual marks as annotations or overrides rather than the sole data source.

### Phase 2F: Student Knowledge, Ability, And Literacy Navigation

Complete graph-first navigation across all three tag families. Related-question links must activate the correct panel before scrolling. Redo lists must use their own records and unique element identifiers. Published-content rules apply to every list.

### Phase 2G: Teacher And Admin Analytics

Provide class-scoped teacher diagnostics and aggregate-only grade comparisons, with admin grade-level trends. Error rates use eligible tagged attempts as denominators and report blanks separately. Query paths must be bulk-loaded rather than performing per-node and per-question lookups.

### Phase 3+: Graph And Operational Maturity

Replace demo-coordinate graph rendering with deterministic layered layout, add zoom/detail rules, verify tablet interaction, and complete backup/restore plus database migration exercises. Visual polish follows stable data-entry and diagnostic behavior.

Acceptance:

- Imported non-demo graph nodes receive readable, non-overlapping positions without hard-coded node IDs.
- Student graph navigation works at the classroom tablet viewport with touch and keyboard-accessible controls.
- A database created by the previous release migrates forward without losing assessment, wrong-question, mastery, or ontology history.
- Backup data restores into a fresh test environment and passes core repository consistency checks.

## Data And Authorization Boundaries

- `AuthService` owns policy decisions.
- HTTP handlers resolve request context, invoke policy, and translate domain errors into HTTP responses.
- `PhysicsRepository` owns persistence and enforces invariant-level ownership for sensitive operations.
- Student-visible queries require published assessment state.
- Assessment snapshots remain immutable after creation.
- Published grading is immutable through normal grade operations.
- Corrections create revision evidence instead of overwriting historical facts silently.
- Ontology changes affect current active tagging while historical snapshots continue to explain past assessments.

## Testing Strategy

Every implementation phase follows red-green-refactor:

- Repository tests cover ownership, state transitions, history, calculations, and migrations.
- HTTP integration tests cover login cookies, route authorization, payload validation, and response contracts.
- Rendering tests cover structural UI requirements but do not substitute for HTTP tests.
- Browser acceptance verifies the student, teacher, and admin workflows at a classroom-style viewport.
- A fresh throwaway SQLite database is used for each acceptance pass.
- The common gate is:
  - `python3 -m unittest discover -s tests -v`
  - `python3 -m compileall -q highschoolphysics tests`
  - `git diff --check`
  - phase-specific browser acceptance

## Delivery Rules

- Each phase gets a detailed implementation plan before code changes.
- Each phase is committed independently after its acceptance evidence passes.
- Security defects discovered during later phases return to the current phase and block further feature work.
- New feature requests are assigned to the earliest phase whose data and workflow dependencies support them.
- The overall goal remains active until the completion audit can map every requirement above to current code, tests, migrations, and browser evidence.

## Phase 2C Automated Acceptance Note

Date: 2026-06-11

Implemented in the `codex/phase-2c-question-bank-parsing` branch through the automated acceptance checkpoint:

- schema version 3 adds original-paper, import-batch, parsed-item, parser-config, and question provenance fields;
- built-in deterministic text parsing normalizes numbered questions into reviewable parsed items;
- MarkItDown and MinerU adapter modes fail closed unless deterministic fallback is configured;
- teachers/admins can create and edit question-bank records;
- parser tasks create parsed items, and reviewed parsed items can be saved as provenance-preserving questions;
- candidate generation now returns knowledge, ability, and literacy suggestions;
- formal confirmation enforces at most three active tags per family;
- question-bank filtering and related browsing cover knowledge, ability, and literacy families;
- teacher HTTP JSON routes and the teacher question-bank workspace expose create, parse, save, and tag-confirm flows;
- student users cannot create teacher question-bank records through the teacher route.

Automated evidence:

- `python3 -m unittest discover -s tests -v` -> 92 tests passed.
- `PYTHONPYCACHEPREFIX=/private/tmp/hsp-phase2c-task7-pycache python3 -m compileall -q highschoolphysics tests` -> passed.
- `node --check highschoolphysics/assets/app.js` -> passed.
- `git diff --check` -> passed.
- Fresh demo database check at `/tmp/hsp-phase2c-auto.oXeZ4A/demo.sqlite3` returned schema version `3`, `original_papers=1`, and `question_import_batches=1`.

Remaining non-goals and residual limits:

- Phase 2C automated acceptance does not claim PaddleOCR grading, grading revisions, mastery calculation, or redo-attempt aggregation; those remain Phase 2D and Phase 2E.
- Browser acceptance at classroom viewport is recorded below.
- External MarkItDown and MinerU commands/API credentials are not bundled; this checkpoint verifies configuration boundaries, fail-closed behavior, and deterministic fallback behavior.

## Phase 2C Browser Acceptance Note

Date: 2026-06-11

Browser acceptance ran against `http://127.0.0.1:8880` with database `/tmp/hsp-phase2c-browser.EerYXc/demo.sqlite3` and viewport `1600x900`.

Verified flows:

- Teacher login `teacher_li / teacher123` reaches the teacher workspace and shows “真实题库”, “原卷解析”, and “拆题复核”.
- Teacher can create a manual short-answer question (`q-e9a388fbcd70`).
- Teacher can create and run a deterministic text parse task (`parse-35d3b3243d7f`), producing one parsed item (`parsed-563ccb354808`).
- Teacher can save the parsed item as a question (`q-a09e7bbf01b4`) while preserving parser provenance.
- The saved question exposes a per-question “生成候选” action.
- Candidate generation produced `cand-21355a3fbced410b9782a3ac3da50237`.
- Teacher confirmed one knowledge tag (`kn-pep2019-r1-c04`), one ability tag (`ab-force-analysis`), and one literacy tag (`lit-thinking-reasoning`).
- Filtering the question bank by the confirmed literacy tag left the saved question visible and hid non-matching rows.
- At `1600x900`, `documentElement.scrollWidth=1600` and `window.innerWidth=1600`, so no horizontal document overflow was observed.
- Student login `stu_1001 / student123` cannot create teacher question-bank records; POST `/api/teacher/question` returned `404`.
- Admin login `admin / admin123` shows parser/task status and active taxonomy areas including “解析任务”, “知识图谱与能力标签”, and “核心素养标签”.

Browser tooling note: the in-app Browser connection returned `native pipe is closed` during this run. The acceptance used Google Chrome headless through the bundled Playwright package with the same URL, database, and viewport.

Final residual limits:

- External MarkItDown and MinerU binaries/API credentials are not bundled.
- Phase 2C does not implement PaddleOCR grading, grading revision UX, mastery calculation, or redo-attempt aggregation; those remain later phases.

## Phase 2D Automated Acceptance Note

Date: 2026-06-11

Implemented in the `codex/phase-2d-assessment-grading-loop` branch through the automated acceptance checkpoint:

- schema version 4 adds grading revision, redo attempt, error-reason tag, wrong-question tag, and export-profile evidence tables;
- paper assembly creates reviewed papers from question-bank records;
- assessment creation snapshots assembled paper questions and creates answer-card templates;
- OCR payload import creates scan batches and student responses with raw/final answer, confidence, payload, and review evidence;
- low-confidence OCR responses block grading until reviewed;
- published assessments reject ordinary regrading and require explicit grading revisions;
- explicit grading revisions record revision evidence and update responses/wrong questions without rewriting snapshots;
- students can submit redo attempts as separate evidence;
- teachers/admins can review redo attempts and update `latest_redo_status`;
- teachers/admins can create error-reason tags and attach them to wrong questions;
- wrong-question lists, student cards, and exports expose redo history and error reasons;
- wrong-book exports hide answers and analysis by default and can explicitly include them;
- admin UI exposes and saves export-profile configuration;
- Phase 2D intentionally does not calculate deterministic mastery aggregation; that remains Phase 2E.

Automated evidence:

- `rg -n "TO[D]O|TB[D]|implement la[t]er|fill in deta[i]ls" highschoolphysics tests README.md docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md` -> no matches.
- `PYTHONPYCACHEPREFIX=/private/tmp/hsp-phase2d-pycache python3 -m compileall -q highschoolphysics tools tests` -> passed.
- `node --check highschoolphysics/assets/app.js` -> passed.
- `python3 -m unittest discover -s tests -v` -> 111 tests passed.
- `git diff --check` -> passed.

Remaining non-goals and residual limits:

- PaddleOCR production binaries/services are not bundled; Phase 2D verifies importable OCR payloads, scan batches, and review queues.
- Deterministic mastery metrics, redo-attempt weighting, and calculated tag-level mastery remain Phase 2E.
- External MarkItDown, MinerU, LLM provider, and identity-provider credentials remain configuration boundaries rather than bundled dependencies.

## Phase 2D Browser Acceptance Note

Date: 2026-06-11

Browser acceptance ran against `http://127.0.0.1:8881` with database `/tmp/hsp-phase2d-browser.0aYZif/demo.sqlite3`, branch `codex/phase-2d-assessment-grading-loop`, and viewport `1600x900`.

Verified flows:

- Teacher login `teacher_li / teacher123` reaches the teacher workspace and shows “组卷与答题卡”, “OCR 导入复核”, “批改修订”, and “错因标签”.
- Teacher assembled a two-question paper (`paper-dfaef495b2c8`) from `q-newton-1` and `q-newton-2`.
- Teacher created an assessment (`assess-2c8b9c93995e`) from that paper and the response included an answer-card template id.
- Teacher imported OCR payload with one low-confidence item; an immediate grading attempt returned `blocked_for_review`.
- Teacher resolved the low-confidence review item and then published grading.
- Teacher applied an explicit grading revision after publication.
- Teacher created and attached an error-reason tag to `wq-assess-2c8b9c93995e-stu-1001-q-newton-1`.
- Student login `stu_1001 / student123` saw the wrong question and redo form, submitted redo attempt `redo-aed41fb15209`, and the wrong-card UI showed submitted redo history.
- Teacher reviewed the redo attempt to `done`; the student wrong-card then showed `重做状态：done` and feedback “重做正确”.
- Teacher exported the wrong book; default export hid “正确答案：” and “解析：” while showing “重做记录”.
- Admin login `admin / admin123` saw “错因标签” and “导出配置”, and saved an export-profile configuration.
- At `1600x900`, teacher, student, and admin pages all reported `documentElement.scrollWidth=1600` and `window.innerWidth=1600`, so no horizontal document overflow was observed.

Browser tooling note: the in-app Browser connection timed out and reset while opening the local page. The acceptance used Google Chrome headless through the bundled Playwright package with the same URL, database, and viewport.

## Phase 2E Automated Acceptance Note

Date: 2026-06-11

Implemented in the `codex/phase-2e-mastery-metrics` branch through the automated acceptance checkpoint:

- schema version 5 adds `student_mastery_metrics` with assessment, redo, combined eligible-attempt, correct-rate, and mastery-state fields;
- `mastery-deterministic-v1` records the Phase 2E deterministic evidence policy;
- published assessment responses update per-student tag metrics from immutable `question_version_snapshots.tag_snapshot_json`;
- reviewed redo attempts remain distinguishable in dedicated redo fields while also contributing to combined correct rate;
- blank assessment responses count as eligible attempts and `assessment_blank`/`blank_count`, not as nonblank wrong attempts;
- knowledge, ability, and literacy tags are calculated independently;
- thresholds are implemented as `未练习`, `未掌握`, `有困难`, `不熟练`, and `已掌握`;
- student knowledge graph nodes expose calculated state, evidence text, and mastery color classes;
- ability and literacy mastery summaries expose Phase 2E colors and evidence while full drill-down navigation remains Phase 2F;
- manual knowledge mastery marks remain as display overrides/notes and do not replace calculated evidence.

Automated evidence:

- `PYTHONPYCACHEPREFIX=/private/tmp/hsp-phase2e-verify python3 -m compileall -q highschoolphysics tools tests` -> passed.
- `node --check highschoolphysics/assets/app.js` -> passed.
- `python3 -m unittest discover -s tests -v` -> 116 tests passed.
- `git diff --check` -> passed.

Remaining non-goals and residual limits:

- Phase 2E does not implement full knowledge/ability/literacy click-through navigation; that remains Phase 2F.
- Phase 2E does not implement teacher/admin class-level or grade-level mastery aggregation; that remains Phase 2G.
- External PaddleOCR, MarkItDown, MinerU, LLM provider, and identity-provider credentials remain configuration boundaries rather than bundled dependencies.

## Phase 2E Browser Acceptance Note

Date: 2026-06-11

Browser acceptance ran against `http://127.0.0.1:8892` with database `/tmp/hsp-phase2e-browser/demo.sqlite3`, branch `codex/phase-2e-mastery-metrics`, and viewport `1600x900`.

Setup:

- Demo database was initialized through `python3 -m highschoolphysics.server --demo`.
- The demo assessment `assess-week-1` was resolved and published before browser checks so deterministic mastery metrics were present.

Verified flows:

- Student login `stu_1001 / student123` reached `/app` and showed “知识图谱”, `正确率 100%`, `计算：已掌握`, “能力掌握”, and “核心素养掌握”.
- Student page exposed Phase 2E color classes: `mastery-state-mastered` count 7 and `mastery-state-unpracticed` count 342.
- Teacher login `teacher_li / teacher123` reached `/teacher` and showed “组卷与答题卡”, “批改修订”, “班级诊断”, and “A4 错题本”.
- Admin login `admin / admin123` reached `/admin` and showed “管理员设定”, “用户与班级”, “知识图谱与能力标签”, and “核心素养管理”.
- At `1600x900`, student, teacher, and admin pages all reported `documentElement.scrollWidth=1600` and `window.innerWidth=1600`, so no horizontal document overflow was observed.

Browser tooling note: the Browser plugin tool surface was not exposed in this session. The acceptance used terminal-run Google Chrome headless through the bundled Playwright package. A first MCP Node REPL attempt failed because bundled Playwright's Chromium executable was not installed; a second MCP attempt against system Chrome could launch but could not manage the process under MCP permissions. The successful run used the same system Chrome executable from an escalated terminal command.

## Phase 2F Automated Acceptance Note

Date: 2026-06-12

Implemented in the `codex/phase-2f-student-navigation` branch through the automated acceptance checkpoint:

- student dashboards now expose `knowledge_navigation`, `ability_navigation`, and `literacy_navigation` view models;
- each navigation module joins the tag family to current mastery evidence, published related questions, the authenticated student's wrong questions, and redo tasks;
- related-question lists are limited to questions visible through the authenticated student's published assessment history;
- wrong and redo links use the authenticated student's own wrong-question records;
- student rendering exposes graph-first knowledge, ability, and literacy navigation panels with current mastery evidence;
- repeated question cards use panel-scoped unique element identifiers;
- related-question links carry `data-target-tab`, `data-target-panel`, and `data-target-id`;
- frontend click handling activates the requested tag-family panel before scrolling and highlighting the target card;
- Phase 2F intentionally does not implement teacher/admin class-level or grade-level mastery analytics; that remains Phase 2G.

Automated evidence:

- `PYTHONPYCACHEPREFIX=/private/tmp/hsp-phase2f-verify python3 -m compileall -q highschoolphysics tools tests` -> passed.
- `node --check highschoolphysics/assets/app.js` -> passed.
- `python3 -m unittest discover -s tests -v` -> 118 tests passed.
- `git diff --check` -> passed.

Remaining non-goals and residual limits:

- Teacher/admin cross-class, grade-level, and trend analytics remain Phase 2G.
- External PaddleOCR, MarkItDown, MinerU, LLM provider, and identity-provider credentials remain configuration boundaries rather than bundled dependencies.

## Phase 2F Browser Acceptance Note

Date: 2026-06-12

Browser acceptance ran against `http://127.0.0.1:55219` with database `/tmp/hsp-phase2f-browser/demo.sqlite3`, branch `codex/phase-2f-student-navigation`, and viewport `1600x900`.

Setup:

- Demo database was initialized through `python3 -m highschoolphysics.server --demo`.
- `q-newton-1` was confirmed with knowledge tag `kn-pep2019-r1-c04-s03`, ability tag `ab-force-analysis`, and literacy tag `lit-thinking-model`.
- Demo assessment `assess-week-1` was resolved and published before browser checks.
- The student's wrong question `wq-assess-week-1-stu-1001-q-newton-1` remained pending so navigation could show the corresponding redo task.

Verified flows:

- Student login `stu_1001 / student123` reached `/app` and showed “知识导航”, “能力导航”, “核心素养导航”, “当前掌握证据”, and “待重做”.
- Student page exposed `knowledge`, `ability`, and `literacy` tag-family panels; the initial active panel was `knowledge`.
- Student page did not expose the unpublished draft text “未发布教师草稿题不能出现在学生导航。”.
- Student page had no duplicate HTML `id` attributes.
- The ability link for `ab-force-analysis` and `q-newton-1` carried `data-target-tab="graph"`, `data-target-panel="ability"`, and target id `nav-ability-ab-force-analysis-question-q-newton-1`.
- Clicking the ability navigation link activated the ability panel, deactivated the knowledge panel, and highlighted the `q-newton-1` ability card.
- Clicking the literacy navigation link activated the literacy panel and highlighted the `q-newton-1` literacy card.
- Teacher login `teacher_li / teacher123` reached `/teacher` and showed “组卷与答题卡”, “批改修订”, “班级诊断”, and “A4 错题本”.
- Admin login `admin / admin123` reached `/admin` and showed “管理员设定”, “用户与班级”, “知识图谱与能力标签”, and “核心素养管理”.
- At `1600x900`, student, teacher, and admin pages all reported `documentElement.scrollWidth=1600` and `window.innerWidth=1600`, so no horizontal document overflow was observed.

Browser tooling note: the in-app Browser connected far enough to expose its control documentation, but navigating to the local page returned `native pipe closed before response`. The successful acceptance used Google Chrome headless through the bundled Playwright package with the same URL, database, and viewport.

## Phase 2G Automated Acceptance Note

Date: 2026-06-12

Implemented in the `codex/phase-2g-teacher-admin-analytics` branch through the automated acceptance checkpoint:

- repository analytics now bulk-load `student_mastery_metrics` rows for class and grade aggregates;
- teacher analytics expose knowledge, ability, and literacy class mastery rows with own-class student drilldown;
- teacher grade comparison is aggregate-only and omits other-class student rows;
- admin analytics expose grade-level mastery aggregates and score-rate trend rows;
- admin rendering is aggregate-only and does not emit student-detail attributes;
- error rates use `wrong_count / eligible_attempts`;
- blank rates use `blank_count / eligible_attempts` and remain separate from nonblank wrong attempts;
- teacher and admin dashboards render Phase 2G mastery analytics with state distributions and tag-family tables.

Automated evidence:

- `PYTHONPYCACHEPREFIX=/private/tmp/hsp-phase2g-verify python3 -m compileall -q highschoolphysics tools tests` -> passed.
- `node --check highschoolphysics/assets/app.js` -> passed.
- `python3 -m unittest discover -s tests -v` -> 123 tests passed.
- `git diff --check` -> passed.

Remaining non-goals and residual limits:

- Phase 2G does not replace the current demo-coordinate graph renderer; deterministic layout, zoom/detail rules, and tablet interaction remain Phase 3+.
- Phase 2G does not add backup restore or database migration exercises; those remain Phase 3+ operational maturity work.
- External PaddleOCR, MarkItDown, MinerU, LLM provider, and identity-provider credentials remain configuration boundaries rather than bundled dependencies.

## Phase 2G Browser Acceptance Note

Date: 2026-06-12

Browser acceptance ran against `http://127.0.0.1:61315` with database `/private/tmp/hsp-phase2g-browser.stHqsa/demo.sqlite3`, branch `codex/phase-2g-teacher-admin-analytics`, and viewport `1600x900`.

Setup:

- Demo database was initialized from the committed seed data.
- `q-newton-1` assessment snapshot was given literacy tag `lit-thinking-model` so all three tag families had visible mastery evidence.
- Demo assessment `assess-week-1` was resolved and published before browser checks.

Verified flows:

- Student login `stu_1001 / student123` reached `/app` and still showed graph-first Phase 2F navigation: “知识图谱”, “知识导航”, “能力导航”, “核心素养导航”, and “当前掌握证据”.
- Teacher login `teacher_li / teacher123` reached `/teacher` and showed “Phase 2G 掌握度分析”, “班级掌握图谱”, “学生明细”, “年级均值对比”, “知识掌握”, “能力掌握”, and “核心素养掌握”.
- Admin login `admin / admin123` reached `/admin` and showed “Phase 2G 年级掌握分析”, “年级掌握趋势”, “聚合标签掌握”, `aggregate-only`, and “高二”.
- Admin HTML did not include `data-admin-analytics-student-id="stu-1001"`.
- Student, teacher, and admin pages had no duplicate HTML `id` attributes in the browser check.
- At `1600x900`, student, teacher, and admin pages all reported `documentElement.scrollWidth=1600`, `document.body.scrollWidth=1600`, and `window.innerWidth=1600`, so no horizontal document overflow was observed.

Browser tooling note: this session did not expose a usable in-app Browser navigation tool through tool discovery. The successful acceptance used Google Chrome headless through the bundled Playwright package and the system Chrome executable.

## Phase 3+ Automated Acceptance Note

Date: 2026-06-12

Implemented in the `codex/phase-3-graph-ops-maturity` branch through the automated acceptance checkpoint:

- student relation graph rendering now uses `deterministic-layered-v1` layout from `highschoolphysics.graph_layout`;
- imported non-demo graph nodes are positioned by level and stable sort keys, without hard-coded demo node IDs;
- graph nodes expose `role="button"`, `tabindex="0"`, `aria-label`, `data-detail-level`, and `data-min-label-scale`;
- frontend graph handling records `data-graph-scale-state` as `low`, `medium`, or `high`;
- zoom detail rules hide or de-emphasize child labels at low/medium scale and show them at high scale;
- keyboard `Enter`/`Space` can select graph nodes;
- pointer capture/cancel/lost-capture handling keeps tablet pan state bounded;
- backup export now uses a single dependency-ordered table list and includes Phase 2D/2E/2G operational tables;
- backup restore can rebuild a fresh SQLite database and replace redacted password hashes with forced-change placeholders;
- `consistency_check` verifies foreign keys, required core tables, response/snapshot/question/user links, mastery/user links, and snapshot tag JSON;
- schema version is `6`, with a migration exercise proving Phase 2G history survives forward initialization.

Automated evidence:

- `PYTHONPYCACHEPREFIX=/private/tmp/hsp-phase3-verify python3 -m compileall -q highschoolphysics tools tests` -> passed.
- `node --check highschoolphysics/assets/app.js` -> passed.
- `python3 -m unittest discover -s tests -v` -> 129 tests passed.
- `git diff --check` -> passed.

Remaining non-goals and residual limits:

- Phase 3+ does not add production SSO, production LLM credentials, external OCR credentials, or a third-party graph rendering library.
- Backup restore is intended as an operational recovery and verification path; exported password hashes remain redacted, and restored users are forced through placeholder password reset/change workflows.
- Visual polish beyond readable diagnostic graph behavior remains a later design-system task.

## Phase 3+ Browser Acceptance Note

Date: 2026-06-12

Browser acceptance ran against `http://127.0.0.1:51177` with database `/private/tmp/hsp-phase3-browser.knaWB9/demo.sqlite3`, branch `codex/phase-3-graph-ops-maturity`, and student viewports `1366x1024` plus `1600x900`.

Setup:

- Demo database was initialized from committed seed data.
- `q-newton-1` was confirmed with knowledge tag `kn-pep2019-r1-c04-s03`, ability tag `ab-force-analysis`, and literacy tag `lit-thinking-model`.
- Demo assessment `assess-week-1` was resolved and published before browser checks.

Verified flows:

- Student login `stu_1001 / student123` reached `/app` at both tested viewports.
- Student graph SVG exposed `data-layout="deterministic-layered-v1"`.
- Zooming in moved graph state to `high`; zooming out moved graph state to `low`.
- Keyboard focus on a graph node followed by `Enter` selected a graph node.
- Pointer drag on the graph changed the graph-stage transform, proving pan behavior works through the browser.
- Student pages at `1366x1024` and `1600x900` both reported `documentElement.scrollWidth == window.innerWidth` and `body.scrollWidth == window.innerWidth`.
- Admin login `admin / admin123` reached `/admin` at `1600x900` and downloaded `/backup/download`.
- Downloaded backup included `_metadata`, `questions`, and `student_mastery_metrics`.
- The downloaded backup restored into `/private/tmp/hsp-phase3-browser.knaWB9/restored.sqlite3`; restore summary was `584` rows across `50` tables, and `consistency_check` returned `{"status": "ok", "issues": []}`.

Browser tooling note: the successful acceptance used Google Chrome headless through the bundled Playwright package and the system Chrome executable.

## Final Completion Audit

Date: 2026-06-12

The staged completion target is now satisfied by current branch evidence:

- Phase 2A.1 security and data-integrity gate: completed and browser-verified in its acceptance note.
- Phase 2B default taxonomy, ability, and literacy systems: completed with schema/import/admin visibility tests and acceptance notes.
- Phase 2C real question bank and document parsing: completed with parser, provenance, tag-family, and route tests plus acceptance notes.
- Phase 2D assessment, OCR payload, grading revision, and wrong-question redo loop: completed with assessment snapshot, OCR review, revision, redo, export, and browser evidence.
- Phase 2E deterministic mastery metrics: completed with schema v5 history-preserving tests, mastery threshold tests, student graph evidence, and browser evidence.
- Phase 2F student knowledge, ability, and literacy navigation: completed with graph-first navigation, published-content visibility, unique IDs, and browser evidence.
- Phase 2G teacher and admin analytics: completed with class-scoped drilldown, aggregate-only grade/admin views, denominator tests, and browser evidence.
- Phase 3+ graph and operational maturity: completed with deterministic graph layout, zoom/detail/keyboard/pointer behavior, schema v6 migration exercise, backup restore, consistency checks, and browser evidence.

The remaining exclusions are outside the accepted completion target: production external OCR/LLM credentials, production SSO, PDF generation service integration, and broader visual design-system polish.
