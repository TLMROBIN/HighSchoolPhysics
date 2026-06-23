# Production Runtime Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the production readiness foundation for dependency extras, schema v7 capability records, runtime checks, and the admin health surface.

**Architecture:** Keep the current stdlib server-rendered app. Add a focused `runtime.py` capability registry, a small `runtime_check.py` CLI, additive schema v7 tables, repository methods for storing admin health-check evidence, and admin rendering/API routes. Heavy OCR/parser/PDF/SSO provider work comes in later plans, but this plan creates the verified runway those implementations will use.

**Tech Stack:** Python 3.10+ optional production extras, SQLite, stdlib HTTP server, unittest, vanilla CSS/JS.

---

## File Structure

**Create**

- `highschoolphysics/runtime.py`: dependency/capability registry with safe import and executable checks.
- `highschoolphysics/runtime_check.py`: CLI entrypoint for JSON health output and explicit smoke checks.
- `tests/test_runtime.py`: unit tests for capability states and CLI JSON contracts.

**Modify**

- `pyproject.toml`: package metadata, optional dependency groups, and module install config.
- `highschoolphysics/db.py`: schema version 7 and `runtime_capability_checks`.
- `highschoolphysics/repository.py`: read/write capability check summaries.
- `highschoolphysics/server.py`: admin readiness panel and `/api/admin/runtime-check`.
- `highschoolphysics/assets/app.js`: admin runtime-check form handler.
- `highschoolphysics/assets/app.css`: production readiness and design-system component classes.
- `tests/test_database.py`: schema v7 migration tests.
- `tests/test_workflow.py`: repository runtime-check persistence tests.
- `tests/test_http_integration.py`: admin-only runtime-check endpoint tests.
- `tests/test_server.py`: admin readiness panel rendering tests.
- `README.md`: installation extras and runtime check commands.

## Contracts

Capability IDs:

```python
CAPABILITY_IDS = (
    "paddleocr",
    "markitdown",
    "mineru-local",
    "mineru-api",
    "playwright-pdf",
    "oidc-sso",
    "secret-encryption",
)
```

Capability statuses:

```python
CAPABILITY_STATUSES = (
    "ready",
    "missing_dependency",
    "missing_executable",
    "missing_credential",
    "disabled",
    "degraded",
    "failed",
)
```

Runtime check row shape:

```python
{
    "capability_id": "markitdown",
    "status": "missing_dependency",
    "label": "MarkItDown",
    "detail": "Python package markitdown is not importable",
    "version": "",
    "checked_at": "2026-06-12 10:00:00",
}
```

## Task 1: Add Runtime Capability Registry

**Files:**

- Create: `highschoolphysics/runtime.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write failing tests**

Add `tests/test_runtime.py`:

```python
import unittest

from highschoolphysics.runtime import (
    CAPABILITY_IDS,
    check_runtime_capabilities,
    check_single_capability,
)


class RuntimeCapabilityTests(unittest.TestCase):
    def test_runtime_capabilities_include_production_targets(self):
        self.assertEqual(
            CAPABILITY_IDS,
            (
                "paddleocr",
                "markitdown",
                "mineru-local",
                "mineru-api",
                "playwright-pdf",
                "oidc-sso",
                "secret-encryption",
            ),
        )

    def test_missing_import_is_reported_without_raising(self):
        result = check_single_capability(
            {
                "id": "missing-test",
                "label": "Missing Test",
                "module": "definitely_missing_hsp_module",
            }
        )
        self.assertEqual(result["status"], "missing_dependency")
        self.assertEqual(result["version"], "")
        self.assertIn("definitely_missing_hsp_module", result["detail"])

    def test_disabled_credential_capability_is_explicit(self):
        result = check_single_capability(
            {
                "id": "api-test",
                "label": "API Test",
                "requires_credential": True,
                "enabled": False,
            }
        )
        self.assertEqual(result["status"], "disabled")

    def test_runtime_summary_is_stable_and_contains_all_capabilities(self):
        result = check_runtime_capabilities()
        self.assertEqual(
            [item["capability_id"] for item in result],
            list(CAPABILITY_IDS),
        )
        for item in result:
            self.assertIn("status", item)
            self.assertIn("label", item)
            self.assertIn("detail", item)
            self.assertIn("version", item)
```

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_runtime -v
```

Expected: import failure because `highschoolphysics.runtime` does not exist.

- [ ] **Step 3: Implement minimal registry**

Create `highschoolphysics/runtime.py`:

```python
"""Runtime capability checks for production dependencies."""

from importlib import import_module, metadata
import shutil


CAPABILITY_IDS = (
    "paddleocr",
    "markitdown",
    "mineru-local",
    "mineru-api",
    "playwright-pdf",
    "oidc-sso",
    "secret-encryption",
)

CAPABILITY_DEFINITIONS = (
    {
        "id": "paddleocr",
        "label": "PaddleOCR 本地识别",
        "module": "paddleocr",
        "package": "paddleocr",
    },
    {
        "id": "markitdown",
        "label": "MarkItDown 文档解析",
        "module": "markitdown",
        "package": "markitdown",
    },
    {
        "id": "mineru-local",
        "label": "MinerU 本地解析",
        "module": "mineru",
        "package": "mineru",
        "executable": "mineru",
    },
    {
        "id": "mineru-api",
        "label": "MinerU API",
        "requires_credential": True,
        "enabled": False,
    },
    {
        "id": "playwright-pdf",
        "label": "Playwright PDF",
        "module": "playwright",
        "package": "playwright",
    },
    {
        "id": "oidc-sso",
        "label": "OIDC SSO",
        "module": "authlib",
        "package": "Authlib",
    },
    {
        "id": "secret-encryption",
        "label": "密钥加密",
        "module": "cryptography.fernet",
        "package": "cryptography",
    },
)

CAPABILITY_STATUSES = (
    "ready",
    "missing_dependency",
    "missing_executable",
    "missing_credential",
    "disabled",
    "degraded",
    "failed",
)


def _package_version(package_name):
    if not package_name:
        return ""
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return ""


def check_single_capability(definition):
    capability_id = definition["id"]
    label = definition.get("label", capability_id)
    if definition.get("enabled") is False:
        return {
            "capability_id": capability_id,
            "label": label,
            "status": "disabled",
            "detail": "%s is disabled until configured" % label,
            "version": "",
        }
    module_name = definition.get("module")
    package_name = definition.get("package") or module_name
    version = _package_version(package_name)
    if module_name:
        try:
            import_module(module_name)
        except Exception:
            return {
                "capability_id": capability_id,
                "label": label,
                "status": "missing_dependency",
                "detail": "Python package %s is not importable" % module_name,
                "version": version,
            }
    executable = definition.get("executable")
    if executable and not shutil.which(executable):
        return {
            "capability_id": capability_id,
            "label": label,
            "status": "missing_executable",
            "detail": "Executable %s is not on PATH" % executable,
            "version": version,
        }
    if definition.get("requires_credential"):
        return {
            "capability_id": capability_id,
            "label": label,
            "status": "missing_credential",
            "detail": "%s requires admin credentials" % label,
            "version": version,
        }
    return {
        "capability_id": capability_id,
        "label": label,
        "status": "ready",
        "detail": "%s dependency is importable" % label,
        "version": version,
    }


def check_runtime_capabilities(definitions=CAPABILITY_DEFINITIONS):
    return [check_single_capability(definition) for definition in definitions]
```

- [ ] **Step 4: Run GREEN**

```bash
python3 -m unittest tests.test_runtime -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/runtime.py tests/test_runtime.py
git commit -m "feat: add production runtime capability checks"
```

## Task 2: Add Dependency Extras And Runtime CLI

**Files:**

- Modify: `pyproject.toml`
- Create: `highschoolphysics/runtime_check.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write failing CLI and metadata tests**

Append to `tests/test_runtime.py`:

```python
import json
import subprocess
import sys
from pathlib import Path
import tomllib


class RuntimeCliTests(unittest.TestCase):
    def test_pyproject_declares_production_extras(self):
        data = tomllib.loads(Path("pyproject.toml").read_text())
        optional = data["project"]["optional-dependencies"]
        for name in ("ocr", "parsing", "pdf", "sso", "providers", "production"):
            self.assertIn(name, optional)
        self.assertTrue(any("paddleocr" in item for item in optional["ocr"]))
        self.assertTrue(any("markitdown" in item for item in optional["parsing"]))
        self.assertTrue(any("mineru" in item for item in optional["parsing"]))
        self.assertTrue(any("playwright" in item for item in optional["pdf"]))
        self.assertTrue(any("Authlib" in item for item in optional["sso"]))
        self.assertTrue(any("cryptography" in item for item in optional["providers"]))

    def test_runtime_check_cli_outputs_json(self):
        completed = subprocess.run(
            [sys.executable, "-m", "highschoolphysics.runtime_check", "--json"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertIn("capabilities", payload)
        self.assertEqual(
            [item["capability_id"] for item in payload["capabilities"]],
            list(CAPABILITY_IDS),
        )
```

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_runtime.RuntimeCliTests -v
```

Expected: fails because optional dependencies and CLI are absent.

- [ ] **Step 3: Update `pyproject.toml`**

Add:

```toml
[project.optional-dependencies]
ocr = [
  "paddleocr>=3.0.0",
]
parsing = [
  "markitdown[docx,pdf]>=0.1.0",
  "mineru[all]>=2.0.0",
]
pdf = [
  "playwright>=1.40",
]
sso = [
  "Authlib>=1.3",
]
providers = [
  "cryptography>=42",
  "openai>=1.0",
]
production = [
  "paddleocr>=3.0.0",
  "markitdown[docx,pdf]>=0.1.0",
  "mineru[all]>=2.0.0",
  "playwright>=1.40",
  "Authlib>=1.3",
  "cryptography>=42",
  "openai>=1.0",
]

[tool.setuptools.packages.find]
include = ["highschoolphysics*"]
```

- [ ] **Step 4: Add CLI**

Create `highschoolphysics/runtime_check.py`:

```python
"""CLI for HighSchoolPhysics runtime readiness checks."""

import argparse
import json

from .runtime import CAPABILITY_DEFINITIONS, check_runtime_capabilities


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--capability", choices=[item["id"] for item in CAPABILITY_DEFINITIONS])
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    definitions = CAPABILITY_DEFINITIONS
    if args.capability:
        definitions = [item for item in definitions if item["id"] == args.capability]
    capabilities = check_runtime_capabilities(definitions)
    payload = {
        "capabilities": capabilities,
        "smoke_requested": bool(args.smoke),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in capabilities:
            print("%s\t%s\t%s" % (item["capability_id"], item["status"], item["detail"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run GREEN**

```bash
python3 -m unittest tests.test_runtime.RuntimeCliTests -v
```

Expected: 2 tests pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml highschoolphysics/runtime_check.py tests/test_runtime.py
git commit -m "feat: declare production extras and runtime check cli"
```

## Task 3: Add Schema V7 Runtime Check Persistence

**Files:**

- Modify: `highschoolphysics/db.py`
- Test: `tests/test_database.py`

- [ ] **Step 1: Write failing schema tests**

Append to `tests/test_database.py`:

```python
    def test_phase_production_schema_adds_runtime_capability_checks(self):
        conn = connect(":memory:")
        initialize_database(conn)
        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        self.assertIn("runtime_capability_checks", tables)
        columns = table_columns(conn, "runtime_capability_checks")
        self.assertTrue(
            {
                "capability_id",
                "status",
                "label",
                "detail",
                "version",
                "checked_by",
                "checked_at",
            }.issubset(columns)
        )
        self.assertEqual(conn.execute("pragma user_version").fetchone()[0], 7)
```

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_database.DatabaseConfigurationTests.test_phase_production_schema_adds_runtime_capability_checks -v
```

Expected: fails because table is missing and schema version is 6.

- [ ] **Step 3: Implement schema**

Change `SCHEMA_VERSION = 7` in `highschoolphysics/db.py`, then add:

```sql
create table if not exists runtime_capability_checks (
    id text primary key,
    school_id text not null references schools(id),
    capability_id text not null,
    status text not null,
    label text not null default '',
    detail text not null default '',
    version text not null default '',
    checked_by text references users(id),
    checked_at text default current_timestamp
);

create index if not exists idx_runtime_capability_checks_latest
on runtime_capability_checks(school_id, capability_id, checked_at);
```

- [ ] **Step 4: Run GREEN**

```bash
python3 -m unittest tests.test_database.DatabaseConfigurationTests.test_phase_production_schema_adds_runtime_capability_checks -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/db.py tests/test_database.py
git commit -m "feat: persist runtime capability checks"
```

## Task 4: Repository Runtime Health Methods

**Files:**

- Modify: `highschoolphysics/repository.py`
- Test: `tests/test_workflow.py`

- [ ] **Step 1: Write failing repository tests**

Append to `tests/test_workflow.py`:

```python
    def test_admin_records_and_reads_runtime_capability_checks(self):
        result = self.repo.record_runtime_capability_checks(
            actor_id="user-admin",
            checks=[
                {
                    "capability_id": "markitdown",
                    "status": "missing_dependency",
                    "label": "MarkItDown",
                    "detail": "Python package markitdown is not importable",
                    "version": "",
                }
            ],
        )
        self.assertEqual(result[0]["capability_id"], "markitdown")
        self.assertEqual(result[0]["status"], "missing_dependency")

        dashboard = self.repo.production_readiness_dashboard("user-admin")
        check = next(
            item for item in dashboard["runtime_checks"]
            if item["capability_id"] == "markitdown"
        )
        self.assertEqual(check["status"], "missing_dependency")
        self.assertIn("markitdown", check["detail"])

    def test_runtime_capability_checks_require_admin(self):
        with self.assertRaises(PermissionDenied):
            self.repo.record_runtime_capability_checks(
                actor_id="user-teacher-li",
                checks=[
                    {
                        "capability_id": "paddleocr",
                        "status": "ready",
                        "label": "PaddleOCR",
                        "detail": "ok",
                        "version": "3.0.0",
                    }
                ],
            )
```

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_admin_records_and_reads_runtime_capability_checks tests.test_workflow.WorkflowTests.test_runtime_capability_checks_require_admin -v
```

Expected: missing method failure.

- [ ] **Step 3: Implement methods**

In `highschoolphysics/repository.py`, import:

```python
from .runtime import check_runtime_capabilities
```

Add methods:

```python
    def record_runtime_capability_checks(self, actor_id, checks=None):
        actor = self._actor(actor_id)
        if actor["role"] != "admin":
            raise PermissionDenied("Admin role required")
        checks = checks or check_runtime_capabilities()
        stored = []
        for check in checks:
            check_id = "runtime-check-" + uuid.uuid4().hex[:12]
            self.conn.execute(
                """
                insert into runtime_capability_checks(
                    id, school_id, capability_id, status, label, detail,
                    version, checked_by
                ) values(?,?,?,?,?,?,?,?)
                """,
                (
                    check_id,
                    actor["school_id"],
                    check["capability_id"],
                    check["status"],
                    check.get("label", ""),
                    check.get("detail", ""),
                    check.get("version", ""),
                    actor_id,
                ),
            )
            stored.append({**check, "id": check_id})
        self._audit(
            actor_id,
            "runtime_capability_checked",
            "runtime",
            "production-readiness",
            {"count": len(stored)},
        )
        self.conn.commit()
        return stored

    def latest_runtime_capability_checks(self, actor_id):
        actor = self._actor(actor_id)
        if actor["role"] != "admin":
            raise PermissionDenied("Admin role required")
        rows = self.conn.execute(
            """
            select r.*
            from runtime_capability_checks r
            join (
                select capability_id, max(checked_at) as checked_at
                from runtime_capability_checks
                where school_id = ?
                group by capability_id
            ) latest
              on latest.capability_id = r.capability_id
             and latest.checked_at = r.checked_at
            where r.school_id = ?
            order by r.capability_id
            """,
            (actor["school_id"], actor["school_id"]),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def production_readiness_dashboard(self, actor_id):
        actor = self._actor(actor_id)
        if actor["role"] != "admin":
            raise PermissionDenied("Admin role required")
        latest = {
            row["capability_id"]: row
            for row in self.latest_runtime_capability_checks(actor_id)
        }
        runtime_checks = []
        for check in check_runtime_capabilities():
            persisted = latest.get(check["capability_id"])
            runtime_checks.append(persisted or check)
        return {"runtime_checks": runtime_checks}
```

- [ ] **Step 4: Run GREEN**

```bash
python3 -m unittest tests.test_workflow.WorkflowTests.test_admin_records_and_reads_runtime_capability_checks tests.test_workflow.WorkflowTests.test_runtime_capability_checks_require_admin -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add highschoolphysics/repository.py tests/test_workflow.py
git commit -m "feat: record runtime readiness evidence"
```

## Task 5: Admin Runtime Health UI And Endpoint

**Files:**

- Modify: `highschoolphysics/server.py`
- Modify: `highschoolphysics/assets/app.js`
- Modify: `highschoolphysics/assets/app.css`
- Test: `tests/test_server.py`
- Test: `tests/test_http_integration.py`

- [ ] **Step 1: Write failing render and HTTP tests**

Append to `tests/test_server.py`:

```python
    def test_admin_app_exposes_production_readiness_panel(self):
        admin = self.auth.login("admin", "admin123", "unit-test").user
        html = render_admin_app(admin, self.repo.admin_dashboard(admin["id"]))

        self.assertIn("生产化就绪度", html)
        self.assertIn("PaddleOCR 本地识别", html)
        self.assertIn("MarkItDown 文档解析", html)
        self.assertIn("MinerU 本地解析", html)
        self.assertIn("Playwright PDF", html)
        self.assertIn('data-admin-form="runtime-check"', html)
        self.assertIn("runtime-health-grid", html)
```

Append to `tests/test_http_integration.py`:

```python
    def test_runtime_check_endpoint_is_admin_only(self):
        admin_status, admin_cookie, _ = self.server.login("admin", "admin123")
        teacher_status, teacher_cookie, _ = self.server.login("teacher_li", "teacher123")
        self.assertEqual(admin_status, 303)
        self.assertEqual(teacher_status, 303)

        status, _, payload = self.server.post_json(
            "/api/admin/runtime-check",
            {},
            teacher_cookie,
        )
        self.assertEqual(status, 403)

        status, _, payload = self.server.post_json(
            "/api/admin/runtime-check",
            {},
            admin_cookie,
        )
        self.assertEqual(status, 200)
        body = json.loads(payload)
        self.assertIn("checks", body)
        self.assertTrue(
            {item["capability_id"] for item in body["checks"]}.issuperset(
                {"paddleocr", "markitdown", "mineru-local", "playwright-pdf"}
            )
        )
```

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_server.ServerRenderingTests.test_admin_app_exposes_production_readiness_panel tests.test_http_integration.HttpIntegrationTests.test_runtime_check_endpoint_is_admin_only -v
```

Expected: fails because UI and route do not exist.

- [ ] **Step 3: Wire dashboard data**

In `admin_dashboard`, include:

```python
"production_readiness": self.production_readiness_dashboard(actor_id)
```

In `render_admin_app`, render a panel:

```html
<section class="panel span-2 runtime-health-panel">
  <div class="panel-head">
    <div>
      <h2>生产化就绪度</h2>
      <p class="explain">检查 OCR、文档解析、PDF、SSO 和密钥加密能力是否真实可用。</p>
    </div>
    <form data-admin-form="runtime-check">
      <button type="submit">重新检查</button>
    </form>
  </div>
  <div class="runtime-health-grid">...</div>
</section>
```

Each health card must include label, status, version, detail, and checked time
when present.

- [ ] **Step 4: Add endpoint**

In `PhysicsHandler.do_POST`, add:

```python
elif path == "/api/admin/runtime-check":
    checks = repo.record_runtime_capability_checks(user["id"])
    self._send_json({"checks": checks})
```

- [ ] **Step 5: Add JavaScript endpoint mapping**

In `ADMIN_FORM_ENDPOINTS`, add:

```javascript
"runtime-check": "/api/admin/runtime-check"
```

- [ ] **Step 6: Add CSS**

Add:

```css
.runtime-health-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.runtime-health-card {
  border: 1px solid var(--line);
  border-radius: var(--radius-md, 8px);
  background: var(--surface);
  padding: 12px;
}

.runtime-health-card strong {
  display: block;
}

.runtime-health-card[data-status="ready"] {
  border-left: 4px solid var(--green);
}

.runtime-health-card[data-status="missing_dependency"],
.runtime-health-card[data-status="missing_executable"],
.runtime-health-card[data-status="missing_credential"],
.runtime-health-card[data-status="failed"] {
  border-left: 4px solid var(--amber);
}

.runtime-health-card small,
.runtime-health-card p {
  color: var(--muted);
}
```

- [ ] **Step 7: Run GREEN**

```bash
python3 -m unittest tests.test_server.ServerRenderingTests.test_admin_app_exposes_production_readiness_panel tests.test_http_integration.HttpIntegrationTests.test_runtime_check_endpoint_is_admin_only -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add highschoolphysics/server.py highschoolphysics/assets/app.js highschoolphysics/assets/app.css tests/test_server.py tests/test_http_integration.py
git commit -m "feat: expose production readiness checks"
```

## Task 6: Documentation And Verification

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-12-production-completion-design.md`

- [ ] **Step 1: Update README**

Add a production install section:

```markdown
## 生产化依赖

基础开发安装仍可只运行核心测试。生产能力通过 extras 安装：

```bash
python3 -m pip install -e ".[production]"
python3 -m playwright install chromium
```

也可以按能力拆开安装：

```bash
python3 -m pip install -e ".[ocr]"
python3 -m pip install -e ".[parsing]"
python3 -m pip install -e ".[pdf]"
python3 -m pip install -e ".[sso,providers]"
```

安装后运行：

```bash
python3 -m highschoolphysics.runtime_check --json
```
```

- [ ] **Step 2: Run full verification**

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q highschoolphysics tools tests
node --check highschoolphysics/assets/app.js
python3 -m highschoolphysics.runtime_check --json
git diff --check
```

Expected: tests pass, compile passes, JS check passes, runtime JSON prints all
capabilities, and diff check exits 0.

- [ ] **Step 3: Record evidence**

Append a short "Runtime Foundation Acceptance Note" to
`docs/superpowers/specs/2026-06-12-production-completion-design.md` with the
exact commands and outcomes.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-12-production-completion-design.md
git commit -m "docs: record production runtime foundation acceptance"
```
