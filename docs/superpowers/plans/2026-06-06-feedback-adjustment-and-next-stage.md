# Feedback Adjustment And Next Stage Plan

## Current-Stage Adjustments Completed

- Teacher-side LLM candidate review now includes an explanation: LLM candidates are provisional knowledge/ability tag suggestions for a question, and teacher confirmation is required before tags become official.
- The `q-newton-1` action is documented in the UI as a seeded demo question candidate generator.
- Teacher-side grading now explains that `批改并发布` checks low-confidence scan review first, grades objective answers, generates wrong-question records, publishes student results, and refreshes diagnostics.
- Teacher actions use a visible status bar so clicks no longer feel silent.
- Teacher wrong-book export supports filtering by class and student.
- Teacher wrong-book export is print-oriented: one student per page section, original question content, knowledge path, ability tags, no correct answer, and no analysis.
- Admin user management now shows grade and class for students and teachers.
- Admin now owns the displayed knowledge graph and ability-tag setup surface.
- Teacher diagnostics include own-class detail plus grade average summary.
- Student home is graph-first instead of wrong-question-first.
- Student graph includes module expansion, relation-graph view, per-knowledge mastery marking, and related-question entry points.
- Student wrong book is secondary and can be filtered by knowledge point; wrong cards include answer, analysis, knowledge paths, and ability tags.

## Questions Answered By Current Product Direction

### Who Defines The Knowledge Graph And Ability Tags?

Administrators define and version the school-level knowledge graph and ability tags. Teachers use those definitions to tag questions, audit LLM suggestions, and view diagnostics. This separation prevents each teacher from quietly creating incompatible graph versions.

### Is Knowledge Graph And Ability Management A Later Phase?

Yes. The MVP now exposes admin ownership and seed data. Full CRUD, version comparison, merge/split history, import from curriculum/catalog sources, and permissioned release workflow belong to Phase 2.

### Should UI Beautification Wait?

Partly. The current stage should keep layout usable and explain workflows clearly. Full visual polish should wait until Phase 2/3 data entry flows stabilize, otherwise we risk polishing the wrong interaction model.

## Next-Stage Development Plan

### Completion Route

The authoritative completion design is:
`docs/superpowers/specs/2026-06-10-project-completion-roadmap-design.md`.

Execution order:

1. Phase 2A.1 security and data-integrity gate.
2. Phase 2B default taxonomy, ability, and literacy systems.
3. Phase 2C real question bank and document parsing.
4. Phase 2D assessment, OCR, grading revision, and wrong-question redo.
5. Phase 2E deterministic mastery metrics.
6. Phase 2F student knowledge, ability, and literacy navigation.
7. Phase 2G teacher and admin analytics.
8. Phase 3+ graph and operational maturity.

The June 10 review is evidence for this route, not an implementation plan by itself. Review suggestions were corrected where necessary: the permission problem includes student writes and unpublished-result visibility; published regrading is rejected before an explicit revision workflow exists; SQLite timeout must not be reduced below the existing default; and Phase 2B is treated as schema/import/UI/provenance work rather than data-only work.

### Phase 2A: Admin Knowledge Graph And Ability CRUD

Status: completed locally and merged into `main` on 2026-06-10.

- Admin CRUD for knowledge nodes with parent selection, aliases, source, status, and version note exists.
- Admin CRUD for semantic edges such as prerequisite, related, confusing, transfer, contains, and model-similar exists.
- Admin CRUD for ability tags with source and active/inactive state exists.
- Ontology version release workflow now supports draft, review, active, and archived states.
- Audit records are written for node, edge, ability-tag, and ontology-version changes.
- CSV/Excel import for initial knowledge trees and ability tags remains a later import convenience, not a blocker for the next stage.

### Phase 2A.1: Security And Data Integrity Gate

Status: completed and browser-verified on 2026-06-10.

Acceptance record:

- `55` automated tests passed; compile and diff checks exited 0.
- Browser verification ran at `1600x900` against `http://127.0.0.1:8877/` using `/tmp/hsp-phase2a1-acceptance.kMOI9G/demo.sqlite3`.
- Non-demo bootstrap verification ran at `http://127.0.0.1:8878/` using `/tmp/hsp-phase2a1-acceptance.kMOI9G/school.sqlite3`.
- Cross-class teacher export/reset returned 403, malformed admin input returned structured 400, and repeated publication returned 409 without deleting the persisted mastery mark or wrong-question record.

- Add real HTTP integration tests for login, cookie handling, authorization, validation errors, and publication visibility.
- Enforce class scope for teacher dashboards, assessment lists, review queues, grading, diagnostics, and wrong-book exports.
- Enforce student ownership for wrong-book export and mastery changes at both HTTP and repository boundaries.
- Hide assessment scores and wrong-question records until the assessment is published.
- Reject ordinary grading for published or archived assessments and preserve existing wrong-question/mastery records.
- Add structured 400, 403, 404, 409, and 500 JSON error responses with transaction rollback.
- Enforce temporary-password change and add audited scoped password reset.
- Enable SQLite WAL without reducing the existing 5000 ms busy timeout, then verify concurrent writes.
- Repair the redo tab and related-question navigation so links activate the correct student panel and use unique element IDs.

Acceptance:

- A student cannot read or modify another student's data through a crafted URL or payload.
- A teacher cannot list, export, review, diagnose, or grade another class.
- Students cannot see scores or wrong questions for an unpublished assessment.
- A repeated grade request after publication returns 409 and does not remove mastery records.
- Invalid payloads return structured JSON errors without breaking the connection.
- Temporary-password users must change their password before entering role pages.
- HTTP, repository, concurrency, compile, and browser acceptance checks pass on a fresh database.

### Phase 2B: Default Physics Taxonomy, Ability, And Literacy Systems

Goal: replace demo seed tags with editable default systems grounded in local textbooks and verified education sources, while preserving later teacher/admin editing.

- Discover and verify the local 人教版 physics textbooks in `/Users/binyu/我的云端硬盘` before importing defaults. Do not assume a path without checking the actual files.
- Parse textbook structure into the default knowledge graph using textbook theme/module, chapter, and section as the first three levels.
- Reserve the fourth-level knowledge-point position but leave it empty by default. Fourth-level nodes should be added later by admin import, teacher curation, or question-tagging evidence.
- Store source metadata for imported textbook nodes: textbook title, file path, volume, chapter/section text, page range when available, parser used, and import timestamp.
- Build a default physics ability-tag system from physics subject characteristics, curriculum standards, authoritative assessment documents, and reviewed academic papers.
- Build a default physics literacy/core-competency tag system from curriculum standards, authoritative assessment documents, and reviewed academic papers.
- Ability tags and literacy tags must be editable by admins, versioned with the ontology, and visible as separate tag families from knowledge nodes.
- Add source/evidence fields to ability and literacy tags so later audits can see why a default tag exists.

Acceptance:

- A fresh demo database can seed editable knowledge, ability, and literacy defaults from verified source records.
- Knowledge defaults contain three populated levels and an intentionally empty fourth-level slot.
- Admin pages distinguish knowledge nodes, ability tags, and literacy tags.
- Tests prove disabled or revised default tags remain visible to admin but do not leak into student/teacher active tagging views.

### Phase 2C: Real Question Bank Management And Document Parsing

- Build question create/edit UI for stem, options, answer, analysis, question type, source, grade, chapter, difficulty, and media placeholders.
- Preserve original paper information for every imported question: original document, paper title, page, question number, source school/publisher when known, exam/use type, and import batch.
- Deploy Microsoft MarkItDown for Word/document parsing.
- Deploy MinerU in both local and API modes, with admin-configured mode selection, fallback policy, task status, and failure reason.
- Keep parser outputs in a unified intermediate format: page, question number, stem, options, answer area, images/formulas/tables, coordinates, confidence, and parser version.
- Automatically split imported papers into question records after parsing, with low-confidence or structurally ambiguous items entering a review queue.
- Add teacher tag review that supports editing, deleting, replacing, and limiting tags before confirmation.
- After question split, generate candidate knowledge, ability, and literacy tags automatically.
- Each tag family may contain multiple parallel tags, but no more than three formal tags per family after teacher/admin confirmation.
- Display same-family tags side by side instead of hiding them in nested detail views.
- Add related-question browsing from each knowledge node and ability tag.
- Add related-question browsing from each literacy tag.
- Add question-bank filters by grade, chapter, knowledge node, ability tag, source, difficulty, and quality state.
- Add question-bank filters by literacy tag, original paper, parser batch, review status, and source confidence.

Acceptance:

- A Word or PDF paper can be imported into a parse task, split into candidate questions, reviewed, and saved as question-bank records.
- A saved question retains original paper metadata and parser provenance.
- Teacher review can confirm up to three knowledge tags, three ability tags, and three literacy tags for a question.
- Knowledge-node, ability-tag, and literacy-tag detail pages show related question lists.

### Phase 2D: Assessment Paper, Answer Card OCR, And Wrong-Question Loop

Goal: connect the real question bank to paper-based testing and the existing wrong-question workflow.

- Let teachers assemble or select an original paper as an `AssessmentSession`.
- Preserve the paper-question snapshot at assessment creation so later question edits do not change historical tests.
- Add answer-card template management for objective questions and supported fill-in questions.
- Deploy PaddleOCR-based answer-card recognition with scan batches, confidence scores, bounding boxes, and low-confidence review.
- Grade objective answers automatically after low-confidence review is resolved.
- Keep large subjective problems in manual-score mode until a later scoring design is approved.
- Automatically create wrong-question records for each student after grading.
- Add explicit grading-revision records for published corrections; never silently rebuild published wrong-question history.
- Store wrong-question redo attempts separately with submitted answer, result, attempt number, and timestamp.
- Add teacher-managed error-reason tags and blank-answer classification.
- Keep the wrong-question export behavior print-oriented: original question, knowledge path, ability/literacy tags, no answer or analysis on student practice pages unless explicitly selected.

Acceptance:

- A teacher can choose a paper, create a test, upload or simulate answer-card scans, resolve low-confidence OCR, grade, publish, and see wrong questions in student wrong books.
- Tests prove historical paper snapshots remain stable after question-bank edits.
- Tests prove grading revisions retain original evidence and redo attempts do not overwrite assessment responses.
- OCR review and grading actions write audit events.

### Phase 2E: Mastery Metrics Across Knowledge, Ability, And Literacy

- For each completed response, update per-student counts for every confirmed tag attached to that question: correct count, wrong count, blank count, total eligible attempts, and correct rate.
- Keep assessment attempts and redo attempts distinguishable and document how each contributes to calculated mastery.
- Track mastery independently for knowledge nodes, ability tags, and literacy tags.
- Use the initial mastery thresholds requested by the user:
  - Correct rate below 30%: 未掌握.
  - Correct rate below 60%: 有困难.
  - Correct rate below 80%: 不熟练.
  - Correct rate at or above 80%: 已掌握.
- Define zero-attempt state separately as 未练习 so it is not confused with 未掌握.
- Use distinct colors for the four mastery states in student, teacher, and admin graph views.
- Preserve the existing manual mastery mark as a teacher/student override or note, not as the only mastery source.

Acceptance:

- After grading a test, student/tag statistics update deterministically from responses.
- Student graph and ability/literacy modules show mastery color based on the thresholds above.
- Tests cover boundary rates: 29%, 30%, 59%, 60%, 79%, 80%, and zero attempts.

### Phase 2F: Student Knowledge, Ability, And Literacy Navigation

- Keep student home graph-first.
- When a student clicks a knowledge point, show related questions, wrong questions, redo tasks, and current mastery evidence for that node.
- Add an ability module where students can view ability tags, related questions, wrong questions, redo tasks, and mastery state.
- Add a literacy module where students can view literacy tags, related questions, wrong questions, redo tasks, and mastery state.
- Keep same-family tags parallel and easy to scan; avoid making students dig through admin-style tables.
- Related-question links must activate the destination tab before scrolling, and repeated cards must not produce duplicate HTML IDs.

Acceptance:

- Student can navigate from a knowledge node, ability tag, or literacy tag to corresponding questions.
- Related-question lists respect published/visible content rules and do not expose unpublished teacher-only drafts.

### Phase 2G: Teacher And Admin Mastery Analytics

- Shift teacher knowledge graph emphasis from ontology management to mastery diagnosis.
- Let teachers view class-level mastery graph by knowledge node, ability tag, and literacy tag.
- Let teachers drill from class graph to individual student mastery for students in their own classes.
- Let teachers compare own class mastery against grade aggregate averages without exposing other classes' student-level rows.
- Let admins view grade-level mastery graphs and aggregate trends.
- Enforce teacher data access at repository/API level for own classes.
- Show grade averages as aggregate-only data, without exposing other classes' student-level rows.
- Add comparison views: own class vs grade average by knowledge node and ability tag.
- Extend comparison views to literacy tags.
- Calculate tag error rates from eligible tagged response attempts rather than class participant count, and report blank responses separately.
- Bulk-load graph, tag, and question relationships so diagnostics do not issue per-node or per-question queries.

Acceptance:

- Teacher cannot query another class student's mastery rows through repository or API calls.
- Admin can view grade aggregates without opening individual student personal data by default.
- Graph colors and table summaries agree on mastery state counts.

### Phase 3+: Interactive Graph Maturity And Visual Polish

- Replace the simple SVG relation graph with a stronger interactive graph implementation.
- Support zoom-level detail rules: high-level modules at low zoom, child nodes and labels at higher zoom.
- Revisit visual design once the real data-entry and graph-navigation workflows are known.
- Prioritize readable diagnostic views over decorative graph visuals.

Acceptance:

- Imported non-demo graph nodes use a readable layout without hard-coded node IDs or fallback overlap.
- Student graph interactions work at the classroom tablet viewport.
- A previous-version database migrates forward without losing historical assessment or ontology records.
- A backup can be restored into a fresh test environment and pass consistency checks.

## Execution Gate

The user approved continued execution toward the complete project state on 2026-06-10. Phase 2A.1 is now the immediate executable phase. Later phases still require their own detailed implementation plans and phase-specific acceptance evidence before implementation begins.
