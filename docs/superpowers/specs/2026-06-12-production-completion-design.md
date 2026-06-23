# HighSchoolPhysics Production Completion Design

## Context

The current project has completed the accepted MVP, Phase 2A.1 through
Phase 2G, and Phase 3+ graph/operations maturity. The remaining objective is to
finish the previously excluded production work:

1. Bundle PaddleOCR, MarkItDown, and MinerU as project installation dependencies
   and make the local parsing/OCR paths actually runnable.
2. Add production-ready MinerU API and LLM provider credential, quota, cost,
   and operations interfaces.
3. Complete unified identity and SSO readiness.
4. Complete PDF generation service integration.
5. Use Product Design to deepen the current visual style into a complete design
   system.

This phase changes the definition of completion: configuration placeholders and
fail-closed adapters are no longer enough. Each item must have current-state
evidence from dependency manifests, runtime checks, HTTP/API behavior, tests,
and browser-visible UI.

## Product Design Brief

- Product: HighSchoolPhysics, a single-school high school physics knowledge
  graph, assessment, wrong-question, and diagnostic system.
- Visual source: deepen the current project style. The existing source is
  `highschoolphysics/assets/app.css`: quiet light theme, teal primary actions,
  amber/red/green mastery states, dense teacher/admin tables, and app-like
  student navigation.
- Interactivity: full interactivity for production screens. Tabs, forms,
  provider tests, parse/OCR/PDF job controls, SSO configuration, and design
  system examples must be functional in the existing server-rendered app.
- Tone: calm, classroom-ready, clear under repeated daily use, not a marketing
  homepage.

## External Source Calibration

- PaddleOCR official documentation exposes `pip install paddleocr` and
  `pip install "paddleocr[all]"`, plus environment verification through
  `import paddleocr`.
- MarkItDown official README requires Python 3.10+ and installs with
  `pip install 'markitdown[all]'`; it also documents narrower extras such as
  `markitdown[pdf, docx, pptx]`.
- MinerU official README supports Python 3.10-3.13, installs through
  `uv pip install -U "mineru[all]"`, and can run local parsing via
  `mineru -p <input_path> -o <output_path>` with `-b pipeline` for CPU.
- Authlib supports OAuth2/OIDC authorization-code clients with state validation
  and PKCE.
- `cryptography.fernet.Fernet` is the selected symmetric encryption primitive
  for stored provider secrets.
- Playwright Python `page.pdf()` is the selected HTML-to-PDF engine because the
  project already produces print-ready HTML and Playwright supports A4 output,
  CSS print media, margins, backgrounds, and tagged PDF options.

## Completion Target

The phase is complete only when all of the following are true:

- A fresh project install can opt into production extras that include local OCR,
  document parsing, PDF generation, SSO, and provider encryption dependencies.
- Admin diagnostics can prove whether PaddleOCR, MarkItDown, MinerU local,
  MinerU API, Playwright PDF, secret encryption, and OIDC config are ready.
- MarkItDown and MinerU paths parse real uploaded files into normalized parsed
  items instead of only failing closed or falling back to deterministic text.
- PaddleOCR can create scan batches from image/PDF inputs with recognized text,
  confidence, bounding boxes where available, and low-confidence review items.
- MinerU API credentials and LLM provider credentials can be stored encrypted,
  tested, disabled, rotated, audited, and budget-limited without exposing raw
  secrets to teachers or UI tables.
- Provider usage is recorded per call with request type, provider, model,
  prompt version, token or page counts when known, estimated cost, outcome, and
  error category.
- Quota and budget policy can block new provider calls before spending exceeds
  configured limits, while preserving a clear fallback reason.
- OIDC SSO can be configured, can generate an authorization redirect, can
  validate callback state, can bind an external identity to an existing local
  user, and can create or block users according to admin policy.
- PDF generation can turn authorized wrong-book and report exports into stored
  PDF artifacts with job status, failure reason, audit trail, and downloadable
  output.
- The visual design system exists as reusable CSS tokens/components plus
  documented examples and is applied to student, teacher, admin, production
  operations, SSO, provider, and PDF screens.
- The final verification includes unit tests, HTTP integration tests,
  dependency health checks, compile checks, JavaScript syntax checks, and
  browser acceptance at classroom tablet and desktop viewports.

## Selected Approach

### Approach 1: Install Everything Directly In Core Dependencies

This would make `pip install .` pull in heavy OCR, document parsing, PDF, SSO,
and crypto dependencies. It is simple to explain but risky for local developer
machines, CI, and small school servers. PaddleOCR and MinerU can be large and
hardware-sensitive. Rejected.

### Approach 2: Keep External Services As Manual Operator Setup

This keeps the current architecture light, but it preserves the exact gap the
objective asks us to close. Rejected.

### Approach 3: Production Extras With Runtime Health And Graceful Capability Gates

This is selected. Core install remains testable and lightweight. Production
extras are first-class project dependencies:

- `.[ocr]` for PaddleOCR.
- `.[parsing]` for MarkItDown and MinerU local clients.
- `.[pdf]` for Playwright PDF generation.
- `.[sso]` for OIDC/Authlib.
- `.[providers]` for encrypted provider secrets and OpenAI-compatible clients.
- `.[production]` for all production capabilities together.

The app exposes capability health checks and refuses to claim a feature is
ready unless the dependency import, command, credential, and smoke test pass.

## Architecture

### Dependency Runtime

Create `highschoolphysics/runtime.py` as the single capability registry. It
must report:

- package import status and version;
- executable availability for CLI-backed tools;
- platform and Python compatibility warnings;
- required model/cache directories;
- whether the capability is enabled, disabled, missing dependency, missing
  credential, degraded, or ready.

Runtime checks must be pure and safe by default. Heavy model downloads or real
remote calls require an explicit admin test action.

### Document Parsing And OCR

Extend `highschoolphysics/parsing.py` and create `highschoolphysics/ocr.py`:

- MarkItDown adapter uses the Python API when available and falls back to CLI
  only when configured.
- MinerU local adapter writes input to a controlled task directory, invokes the
  local CLI or Python package, then normalizes Markdown/JSON output into the
  existing parsed-item shape.
- MinerU API adapter calls the configured endpoint through the provider
  operations layer, records usage, and falls back according to admin policy.
- PaddleOCR adapter accepts scan files, runs local OCR, maps results into the
  existing scan-batch/student-response review model, and preserves raw payloads.
- Every adapter must keep deterministic tests through injectable fake runners;
  real dependency smoke tests live in explicit health commands.

### Provider Operations

Create `highschoolphysics/providers.py`:

- secret encryption through Fernet with local key files or environment-provided
  root key;
- provider records for OpenAI-compatible LLMs and MinerU API;
- masked-secret UI only;
- connection tests that never log raw secrets;
- usage ledger and quota checks before calls;
- budget windows: daily calls, monthly cents, per-call max cents, and
  provider/model enable flags;
- fallback reasons returned to parsing/candidate-generation flows.

The existing deterministic LLM candidate generator remains as the offline
fallback and test oracle.

### SSO And Identity

Create `highschoolphysics/sso.py`:

- OIDC discovery and manual endpoint configuration;
- authorization-code flow with state and nonce records;
- callback handling with issuer, subject, email, display name, and external ID;
- binding policy: existing-user-only, auto-create-student-disabled,
  auto-create-teacher-disabled, or admin-reviewed pending binding;
- role remains local and scoped through existing `AuthService` policy;
- local password login remains available unless admin disables it after SSO is
  verified.

The first implementation uses OIDC. SAML is documented as a later adapter
behind the same provider-config table unless a real school IdP requires it.

### PDF Generation

Create `highschoolphysics/pdf_export.py`:

- input is already-authorized HTML from existing export/report builders;
- engine is Playwright Chromium;
- job output goes under a configured export directory outside source code;
- generated files are recorded in `export_tasks` with status, file name,
  content type, byte size, engine version, failure reason, actor, and audit;
- route permissions reuse the existing export authorization boundary;
- tests use a fake PDF engine for deterministic output plus one optional
  production smoke test when Playwright browsers are installed.

### Visual Design System

Use Product Design with the confirmed brief: deepen current project style.

Deliverables:

- `docs/product-design/highschoolphysics-design-system.md` with tokens,
  component inventory, page templates, interaction states, accessibility
  guidance, and print/PDF rules.
- CSS token layer in `app.css`: color, type scale, spacing, radii, elevation,
  semantic states, mastery colors, focus rings, data-density modes, and print
  tokens.
- Component classes for buttons, forms, field groups, cards, panels, tabs,
  tables, status chips, job timelines, provider cards, health rows, empty
  states, alert banners, metrics, graph cards, and PDF preview panels.
- Applied screens: student dashboard, teacher workspace, admin dashboard,
  production health, provider credentials, SSO settings, parser/OCR status, and
  PDF export jobs.
- Browser QA at 1366x1024 tablet and 1600x900 desktop: no horizontal overflow,
  visible focus, usable dense tables, readable graph, and printable export.

## Data Model Changes

Additive schema version 7:

- `runtime_capability_checks`: persisted admin-triggered health-check results.
- `provider_usage_events`: provider calls, units, cost estimate, outcome.
- `provider_budget_windows`: optional current-window counters for fast checks.
- `sso_login_states`: short-lived OIDC state/nonce records.
- `external_identity_bindings`: normalized binding review state if the existing
  `identity_accounts` fields are not enough.
- `generated_export_files`: durable PDF artifact metadata if `export_tasks`
  should remain generic.

Existing tables remain valid:

- `document_parser_configs`
- `auth_provider_configs`
- `llm_provider_configs`
- `identity_accounts`
- `export_tasks`
- `audit_events`
- `identity_audit_logs`

## Security And Privacy

- Raw API keys, client secrets, OIDC client secrets, and MinerU tokens are never
  rendered after save.
- Secrets are encrypted at rest with an authenticated cipher.
- Secret rotation produces audit events and invalidates cached test status.
- Provider calls must exclude student names and class names unless the admin
  explicitly enables a policy allowing that data class.
- OIDC state and nonce are single-use and time-limited.
- PDF files inherit the same authorization rules as the HTML export source.
- Health checks cannot leak environment variables, command arguments containing
  secrets, or provider response bodies that may include sensitive data.

## Testing And Verification

Common gate:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q highschoolphysics tools tests
node --check highschoolphysics/assets/app.js
git diff --check
```

Production capability gate:

```bash
python3 -m highschoolphysics.runtime_check --json
python3 -m highschoolphysics.runtime_check --capability markitdown --smoke
python3 -m highschoolphysics.runtime_check --capability mineru-local --smoke
python3 -m highschoolphysics.runtime_check --capability paddleocr --smoke
python3 -m highschoolphysics.runtime_check --capability pdf --smoke
```

Browser gate:

- Admin production readiness screen shows all capability states and last smoke
  test result.
- Admin can save, test, rotate, disable, and audit provider credentials without
  seeing raw secrets.
- Admin can configure OIDC and generate an authorization URL with stored state.
- Teacher can run MarkItDown/MinerU parse tasks from uploaded documents.
- Teacher can run PaddleOCR scan import and see low-confidence review payloads.
- Teacher/admin can generate and download a PDF wrong book.
- Student, teacher, and admin redesigned pages work at tablet and desktop
  widths without horizontal document overflow.

## Implementation Slices

1. Schema v7, runtime capability registry, dependency extras, and health UI.
2. Secure provider secret storage, usage ledger, quota/budget checks, and admin
   provider operations UI.
3. MarkItDown and MinerU real local/API adapters.
4. PaddleOCR real local OCR adapter.
5. OIDC SSO configuration and callback/binding flow.
6. Playwright PDF generation service and download routes.
7. Product Design visual system tokens/components and screen application.
8. Full browser and runtime acceptance pass, docs update, commit, and push.

Each slice must include tests first, then implementation, then verification.

## Residual Risks

- MinerU local mode has high disk/RAM requirements. The app must report
  capability readiness honestly instead of assuming every school server can run
  it.
- PaddleOCR model downloads can be large and slow. Smoke tests must be explicit.
- OIDC cannot be fully end-to-end verified without a real IdP, so tests use a
  fake provider and the production UI must show provider discovery/test state.
- Playwright PDF requires a browser binary. The project must document
  `playwright install chromium` and expose a clear missing-browser health state.
- Visual system completion must be browser-verified, not just documented.

## Runtime Foundation Acceptance Note

Date: 2026-06-12

Implemented the first production completion slice:

- `pyproject.toml` now declares production extras for OCR, parsing, PDF, SSO,
  provider encryption, and combined production installation.
- schema version is `7` and adds `runtime_capability_checks` for admin-triggered
  readiness evidence.
- `highschoolphysics.runtime` exposes safe capability checks for PaddleOCR,
  MarkItDown, MinerU local, MinerU API, Playwright PDF, OIDC SSO, and secret
  encryption.
- `python3 -m highschoolphysics.runtime_check --json` outputs machine-readable
  runtime readiness.
- admin dashboard renders a "生产化就绪度" panel and can record readiness checks
  through `/api/admin/runtime-check`.
- non-admin users receive a structured forbidden response for runtime checks.

Verification:

- `python3 -m unittest discover -s tests -v` -> 140 tests passed.
- `python3 -m compileall -q highschoolphysics tools tests` -> passed.
- `node --check highschoolphysics/assets/app.js` -> passed.
- `python3 -m highschoolphysics.runtime_check --json` -> passed.
- `git diff --check` -> passed.

Observed current-machine runtime state:

- MarkItDown is importable as version `0.0.1a1`.
- secret encryption dependency `cryptography` is importable as version `48.0.0`.
- PaddleOCR, MinerU local, Playwright PDF, and Authlib are currently missing
  from the active interpreter until production extras are installed.
- MinerU API is disabled until credentials and provider configuration are
  implemented in the provider-operations slice.
