#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/private/tmp/hsp-release-check-pycache}"
VERIFY_TARGET="${VERIFY_TARGET:-remote}"
REMOTE_HOST="${REMOTE_HOST:-yub@10.50.159.62}"
REMOTE_DIR="${REMOTE_DIR:-/home/yub/Documents/trae_projects/HighSchoolPhysics}"
REMOTE_BASE_URL="${REMOTE_BASE_URL:-http://127.0.0.1:8765}"
REMOTE_PUBLIC_BASE_URL="${REMOTE_PUBLIC_BASE_URL:-http://10.50.159.62}"
REMOTE_ENTRY_PATH="${REMOTE_ENTRY_PATH:-/physics/login}"
REMOTE_GITHUB_URL="${REMOTE_GITHUB_URL:-https://github.com/TLMROBIN/HighSchoolPhysics.git}"
RUN_COMPILEALL="${RUN_COMPILEALL:-1}"
RUN_NODE_CHECK="${RUN_NODE_CHECK:-1}"
RUN_UNITTEST="${RUN_UNITTEST:-1}"
RUN_DIFF_CHECK="${RUN_DIFF_CHECK:-1}"
RUN_RUNTIME_CHECK="${RUN_RUNTIME_CHECK:-1}"
RUN_HTTP_SMOKE="${RUN_HTTP_SMOKE:-0}"
HSP_BASE_URL="${HSP_BASE_URL:-http://127.0.0.1:8765}"
REQUIRE_CLEAN_WORKTREE="${REQUIRE_CLEAN_WORKTREE:-0}"
REQUIRE_UPSTREAM_PARITY="${REQUIRE_UPSTREAM_PARITY:-0}"
REQUIRE_REMOTE_HEAD_MATCH="${REQUIRE_REMOTE_HEAD_MATCH:-0}"

pass() {
  printf '[PASS] %s\n' "$1"
}

warn() {
  printf '[WARN] %s\n' "$1" >&2
}

fail() {
  printf '[FAIL] %s\n' "$1" >&2
  exit 1
}

run_step() {
  local label="$1"
  shift
  printf '\n==> %s\n' "$label"
  "$@"
  pass "$label"
}

check_git_state() {
  local status upstream counts
  status="$(git status --short)"
  if [[ -z "$status" ]]; then
    pass "git worktree clean"
  elif [[ "$REQUIRE_CLEAN_WORKTREE" == "1" ]]; then
    printf '%s\n' "$status" >&2
    fail "git worktree has uncommitted changes"
  else
    warn "git worktree has uncommitted changes; continuing because REQUIRE_CLEAN_WORKTREE=0"
    printf '%s\n' "$status"
  fi

  if upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)"; then
    counts="$(git rev-list --left-right --count "HEAD...${upstream}")"
    printf 'upstream=%s\n' "$upstream"
    printf 'HEAD...upstream=%s\n' "$counts"
    if [[ "$counts" == "0	0" || "$counts" == "0 0" ]]; then
      pass "git upstream parity"
    elif [[ "$REQUIRE_UPSTREAM_PARITY" == "1" ]]; then
      fail "git upstream parity check failed"
    else
      warn "git upstream parity is not 0 0; continuing because REQUIRE_UPSTREAM_PARITY=0"
    fi
  else
    warn "no upstream branch configured; skipping upstream parity"
  fi
}

node_check() {
  if ! command -v node >/dev/null 2>&1; then
    warn "node is not available; skipping browser asset syntax checks"
    return 0
  fi
  local asset
  local assets=(
    highschoolphysics/assets/app.js
    highschoolphysics/assets/learning.js
    highschoolphysics/assets/document-import.js
    highschoolphysics/assets/question-rendering.js
    highschoolphysics/assets/question-bank.js
  )
  for asset in "${assets[@]}"; do
    node --check "$asset"
  done
}

http_smoke() {
  local status
  status="$(curl -s -o /dev/null -w '%{http_code}' "${HSP_BASE_URL%/}/")"
  [[ "$status" == "200" ]] || fail "HTTP smoke failed for ${HSP_BASE_URL%/}/ with status ${status}"
}

check_local() {
  printf '\n== Local HighSchoolPhysics release check ==\n'
  printf 'PYTHON_BIN=%s\n' "$PYTHON_BIN"
  printf 'PYTHONPYCACHEPREFIX=%s\n' "$PYTHONPYCACHEPREFIX"

  check_git_state

  if [[ "$RUN_COMPILEALL" == "1" ]]; then
    run_step "compileall" env PYTHONPYCACHEPREFIX="$PYTHONPYCACHEPREFIX" "$PYTHON_BIN" -m compileall -q highschoolphysics tools tests
  else
    printf '[SKIP] compileall disabled\n'
  fi

  if [[ "$RUN_NODE_CHECK" == "1" ]]; then
    run_step "Node syntax checks for application and document-ingestion assets" node_check
  else
    printf '[SKIP] node check disabled\n'
  fi

  if [[ "$RUN_UNITTEST" == "1" ]]; then
    run_step "unit tests" "$PYTHON_BIN" -m unittest discover -s tests -v
  else
    printf '[SKIP] unit tests disabled\n'
  fi

  if [[ "$RUN_RUNTIME_CHECK" == "1" ]]; then
    run_step "runtime readiness report" "$PYTHON_BIN" -m highschoolphysics.runtime_check --json
  else
    printf '[SKIP] runtime readiness disabled\n'
  fi

  if [[ "$RUN_HTTP_SMOKE" == "1" ]]; then
    run_step "HTTP smoke ${HSP_BASE_URL}" http_smoke
  else
    printf '[SKIP] HTTP smoke disabled; set RUN_HTTP_SMOKE=1 when a server is running\n'
  fi

  if [[ "$RUN_DIFF_CHECK" == "1" ]]; then
    run_step "git diff --check" git diff --check
  else
    printf '[SKIP] git diff --check disabled\n'
  fi

  pass "local HighSchoolPhysics release check"
}

check_remote() {
  local local_head local_github_head
  local_head="$(git rev-parse HEAD)"
  # A fresh query through the local authenticated origin can verify the same
  # GitHub repository when the deployment host has a broken HTTPS connection.
  local_github_head="$(python3 - "$REMOTE_GITHUB_URL" <<'PY_GITHUB'
import subprocess, sys
from urllib.parse import urlparse

def canonical(value):
    value=value.strip().replace('git@github.com:', 'https://github.com/')
    parsed=urlparse(value)
    path=parsed.path.rstrip('/')
    if path.endswith('.git'): path=path[:-4]
    return parsed.hostname, path
try:
    origin=subprocess.check_output(['git','remote','get-url','origin'],text=True).strip()
    if canonical(origin)==canonical(sys.argv[1]):
        result=subprocess.run(['git','ls-remote',origin,'refs/heads/main'],capture_output=True,text=True,timeout=25,check=True)
        print(result.stdout.split()[0])
except (subprocess.SubprocessError, IndexError):
    pass
PY_GITHUB
)"
  # ssh concatenates command arguments before invoking the remote shell, so an
  # empty final argument would be dropped and shift the positional parameters.
  # Preserve the absence explicitly; the remote check can still validate the
  # checkout and service, while reporting GitHub parity as unproven.
  if [[ -z "$local_github_head" ]]; then
    local_github_head="__unknown__"
  fi

  printf '\n== Remote HighSchoolPhysics deploy check ==\n'
  printf 'REMOTE_HOST=%s\n' "$REMOTE_HOST"
  printf 'REMOTE_DIR=%s\n' "$REMOTE_DIR"
  printf 'REMOTE_BASE_URL=%s\n' "$REMOTE_BASE_URL"
  printf 'REMOTE_PUBLIC_BASE_URL=%s\n' "$REMOTE_PUBLIC_BASE_URL"
  printf 'REMOTE_ENTRY_PATH=%s\n' "$REMOTE_ENTRY_PATH"
  printf 'REMOTE_GITHUB_URL=%s\n' "$REMOTE_GITHUB_URL"

  ssh -o BatchMode=yes "$REMOTE_HOST" bash -s -- \
    "$REMOTE_DIR" \
    "$local_head" \
    "$REMOTE_BASE_URL" \
    "$REMOTE_PUBLIC_BASE_URL" \
    "$REMOTE_ENTRY_PATH" \
    "$REMOTE_GITHUB_URL" \
    "$REQUIRE_REMOTE_HEAD_MATCH" \
    "$local_github_head" <<'REMOTE_HSP_VERIFY'
set -euo pipefail

remote_dir="$1"
local_head="$2"
base_url="$3"
public_base_url="$4"
entry_path="$5"
github_url="$6"
require_remote_head_match="$7"
local_github_head="$8"

pass() { printf '[PASS] %s\n' "$1"; }
warn() { printf '[WARN] %s\n' "$1" >&2; }
fail() { printf '[FAIL] %s\n' "$1" >&2; exit 1; }

cd "$remote_dir"

remote_head="$(git rev-parse HEAD)"
origin_head="$(git rev-parse origin/main 2>/dev/null || true)"
github_head="$(timeout 25 git ls-remote "$github_url" refs/heads/main 2>/dev/null | awk '{print $1}' || true)"
if [[ -z "$github_head" && "$local_github_head" != "__unknown__" ]]; then
  github_head="$local_github_head"
  printf '[INFO] GitHub main verified through a fresh local query to the same repository\n'
fi

printf 'remote_head=%s\n' "$remote_head"
printf 'local_head=%s\n' "$local_head"
printf 'origin_main=%s\n' "${origin_head:-unknown}"
printf 'github_main=%s\n' "${github_head:-unknown}"

if [[ "$remote_head" == "$local_head" ]]; then
  pass "remote checkout matches local HEAD"
elif [[ "$require_remote_head_match" == "1" ]]; then
  fail "remote checkout does not match local HEAD"
else
  warn "remote checkout does not match local HEAD; continuing because REQUIRE_REMOTE_HEAD_MATCH=0"
fi

if [[ -n "$origin_head" && -n "$github_head" && "$origin_head" == "$github_head" ]]; then
  pass "remote origin/main matches GitHub main"
else
  warn "could not prove origin/main and GitHub main match"
fi

if pgrep -af "python3 -m highschoolphysics.server" >/dev/null; then
  pass "highschoolphysics server process"
else
  fail "highschoolphysics server process not found"
fi

if systemctl --user is-active --quiet highschoolphysics-document-worker.service; then
  pass "highschoolphysics document worker process"
elif [[ "$require_remote_head_match" == "1" ]]; then
  fail "highschoolphysics document worker service is not active"
else
  warn "highschoolphysics document worker service is not active"
fi

python3 - "$base_url" "$public_base_url" "$entry_path" "$require_remote_head_match" "$remote_dir" <<'PY'
import os
import sqlite3
import sys
from urllib.error import HTTPError
from urllib.request import urlopen

base_url = sys.argv[1].rstrip("/")
public_base_url = sys.argv[2].rstrip("/")
entry_path = sys.argv[3]
require_feature_release = sys.argv[4] == "1"
remote_dir = sys.argv[5]
if not entry_path.startswith("/"):
    entry_path = "/" + entry_path
entry_url = public_base_url + entry_path

def fail(message):
    print(f"[FAIL] {message}", file=sys.stderr)
    raise SystemExit(1)

def passed(message):
    print(f"[PASS] {message}")

try:
    with urlopen(base_url + "/", timeout=8) as response:
        status = response.status
except Exception as exc:
    fail(f"HTTP smoke failed: {type(exc).__name__}: {exc}")
if status != 200:
    fail(f"HTTP smoke returned status={status}")
passed("remote HTTP /")

try:
    with urlopen(entry_url, timeout=8) as response:
        status = response.status
except HTTPError as exc:
    status = exc.code
except Exception as exc:
    fail(f"remote public entry failed for {entry_url}: {type(exc).__name__}: {exc}")
if status not in (200, 302, 303, 307, 308):
    fail(f"remote public entry returned status={status} for {entry_url}")
passed(f"remote public entry {entry_url}")

if require_feature_release:
    required_assets = (
        "/physics/assets/document-import.css",
        "/physics/assets/document-import.js",
        "/physics/assets/question-rendering.css",
        "/physics/assets/question-rendering.js",
        "/physics/assets/vendor/katex/katex.min.js",
    )
    for asset_path in required_assets:
        try:
            with urlopen(public_base_url + asset_path, timeout=8) as response:
                if response.status != 200:
                    fail(f"document-ingestion asset returned status={response.status}: {asset_path}")
        except Exception as exc:
            fail(f"document-ingestion asset unavailable {asset_path}: {type(exc).__name__}: {exc}")
        passed(f"document-ingestion asset {asset_path}")

    database_path = os.path.join(remote_dir, "data", "school.sqlite3")
    try:
        database = sqlite3.connect("file:" + database_path + "?mode=ro", uri=True, timeout=10)
        core_version = database.execute("pragma user_version").fetchone()[0]
        migration_table = database.execute(
            "select 1 from sqlite_master where type='table' and name='app_schema_migrations'"
        ).fetchone()
        feature_version = 0
        tagging_version = 0
        if migration_table:
            row = database.execute(
                "select version from app_schema_migrations where feature='document_ingestion'"
            ).fetchone()
            feature_version = row[0] if row else 0
            row = database.execute(
                "select version from app_schema_migrations where feature='automatic_tagging_queue'"
            ).fetchone()
            tagging_version = row[0] if row else 0
        row = database.execute("select version from app_schema_migrations where feature='response_evidence'").fetchone()
        response_version = row[0] if row else 0
        row = database.execute("select version from app_schema_migrations where feature='question_bank'").fetchone()
        bank_version = row[0] if row else 0
        integrity = database.execute("pragma integrity_check").fetchone()[0]
        foreign_key_errors = database.execute("pragma foreign_key_check").fetchall()
        database.close()
    except Exception as exc:
        fail(f"document-ingestion schema inspection failed: {type(exc).__name__}: {exc}")
    if core_version != 11 or feature_version != 12 or tagging_version != 13 or response_version != 14 or bank_version != 15 or integrity != "ok" or foreign_key_errors:
        fail(
            "schema gate failed: core=%s ingestion=%s tagging=%s response=%s bank=%s integrity=%s fk_errors=%d"
            % (core_version, feature_version, tagging_version, response_version, bank_version, integrity, len(foreign_key_errors))
        )
    passed("document-ingestion v12, automatic-tagging v13, response-evidence v14, question-bank v15 and database integrity")
PY

python3 -m highschoolphysics.runtime_check --json --db data/school.sqlite3
pass "remote runtime readiness report"
REMOTE_HSP_VERIFY

  pass "remote HighSchoolPhysics deploy check"
}

main() {
  printf 'HighSchoolPhysics release check\n'
  printf 'VERIFY_TARGET=%s\n' "$VERIFY_TARGET"

  case "$VERIFY_TARGET" in
    local)
      check_local
      ;;
    remote)
      check_remote
      ;;
    all)
      check_local
      check_remote
      ;;
    *)
      fail "VERIFY_TARGET must be one of: local, remote, all"
      ;;
  esac

  printf 'HighSchoolPhysics release check passed.\n'
}

main "$@"
