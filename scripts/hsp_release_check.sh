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
    warn "node is not available; skipping highschoolphysics/assets/app.js syntax check"
    return 0
  fi
  node --check highschoolphysics/assets/app.js
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
    run_step "node --check highschoolphysics/assets/app.js" node_check
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
  local local_head
  local_head="$(git rev-parse HEAD)"

  printf '\n== Remote HighSchoolPhysics deploy check ==\n'
  printf 'REMOTE_HOST=%s\n' "$REMOTE_HOST"
  printf 'REMOTE_DIR=%s\n' "$REMOTE_DIR"
  printf 'REMOTE_BASE_URL=%s\n' "$REMOTE_BASE_URL"

  ssh -o BatchMode=yes "$REMOTE_HOST" bash -s -- \
    "$REMOTE_DIR" \
    "$local_head" \
    "$REMOTE_BASE_URL" \
    "$REQUIRE_REMOTE_HEAD_MATCH" <<'REMOTE_HSP_VERIFY'
set -euo pipefail

remote_dir="$1"
local_head="$2"
base_url="$3"
require_remote_head_match="$4"

pass() { printf '[PASS] %s\n' "$1"; }
warn() { printf '[WARN] %s\n' "$1" >&2; }
fail() { printf '[FAIL] %s\n' "$1" >&2; exit 1; }

cd "$remote_dir"

remote_head="$(git rev-parse HEAD)"
origin_head="$(git rev-parse origin/main 2>/dev/null || true)"
github_head="$(git ls-remote origin refs/heads/main 2>/dev/null | awk '{print $1}' || true)"

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

python3 - "$base_url" <<'PY'
import json
import sys
from urllib.request import urlopen

base_url = sys.argv[1].rstrip("/")

def fail(message):
    print(f"[FAIL] {message}", file=sys.stderr)
    raise SystemExit(1)

def passed(message):
    print(f"[PASS] {message}")

try:
    with urlopen(base_url + "/", timeout=8) as response:
        body = response.read(200)
        status = response.status
except Exception as exc:
    fail(f"HTTP smoke failed: {type(exc).__name__}: {exc}")
if status != 200:
    fail(f"HTTP smoke returned status={status}")
passed("remote HTTP /")
PY

python3 -m highschoolphysics.runtime_check --json
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
