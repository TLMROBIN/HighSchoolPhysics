#!/usr/bin/env bash

set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${REMOTE_HOST:-yub@10.50.159.62}"
REMOTE_DIR="${REMOTE_DIR:-/home/yub/Documents/trae_projects/HighSchoolPhysics}"
REMOTE_GITHUB_URL="${REMOTE_GITHUB_URL:-https://github.com/TLMROBIN/HighSchoolPhysics.git}"

section() {
  printf '\n== %s ==\n' "$1"
}

run() {
  printf '+ %s\n' "$*"
  "$@"
  local status=$?
  if [[ "$status" -ne 0 ]]; then
    printf '[WARN] command exited with %s: %s\n' "$status" "$*" >&2
  fi
  return 0
}

run_with_timeout() {
  local seconds="$1"
  shift
  printf '+ timeout %s %s\n' "$seconds" "$*"
  if command -v timeout >/dev/null 2>&1; then
    timeout "$seconds" "$@"
  else
    "$@"
  fi
  local status=$?
  if [[ "$status" -ne 0 ]]; then
    printf '[WARN] command exited with %s: %s\n' "$status" "$*" >&2
  fi
  return 0
}

section "HighSchoolPhysics local context"
cd "$ROOT_DIR" || exit 1
printf 'local_dir=%s\n' "$ROOT_DIR"
run git status --short --branch
run git worktree list
run git log --oneline --decorate -n 10
run git remote -v
run git rev-parse HEAD
GIT_TERMINAL_PROMPT=0 run git ls-remote origin refs/heads/main

section "HighSchoolPhysics local changed files"
run git diff --name-status
run git diff --cached --name-status

section "HighSchoolPhysics remote context"
ssh -o BatchMode=yes "$REMOTE_HOST" bash -s -- "$REMOTE_DIR" "$REMOTE_GITHUB_URL" <<'REMOTE_RECOVER'
set -u
remote_dir="$1"
github_url="$2"
section() { printf '\n-- %s --\n' "$1"; }
run() {
  printf '+ %s\n' "$*"
  "$@"
  local status=$?
  if [[ "$status" -ne 0 ]]; then
    printf '[WARN] command exited with %s: %s\n' "$status" "$*" >&2
  fi
  return 0
}
run_with_timeout() {
  local seconds="$1"
  shift
  printf '+ timeout %s %s\n' "$seconds" "$*"
  if command -v timeout >/dev/null 2>&1; then
    timeout "$seconds" "$@"
  else
    "$@"
  fi
  local status=$?
  if [[ "$status" -ne 0 ]]; then
    printf '[WARN] command exited with %s: %s\n' "$status" "$*" >&2
  fi
  return 0
}

cd "$remote_dir" || exit 1
section "remote git"
printf 'remote_dir=%s\n' "$remote_dir"
run git status --short --branch
run git rev-parse HEAD
run git rev-parse origin/main
GIT_TERMINAL_PROMPT=0 run_with_timeout 12 git ls-remote "$github_url" refs/heads/main

section "remote auto-update timer"
run systemctl --user is-enabled highschoolphysics-auto-update.timer
run systemctl --user is-active highschoolphysics-auto-update.timer
run systemctl --user --no-pager list-timers highschoolphysics-auto-update.timer
run journalctl --user -u highschoolphysics-auto-update.service -n 12 --no-pager

section "remote service"
run pgrep -af "python3 -m highschoolphysics.server"
python3 - <<'PY'
from urllib.request import urlopen

for url in ["http://127.0.0.1:8765/"]:
    try:
        with urlopen(url, timeout=8) as response:
            print(url, response.status, response.headers.get("Content-Type"))
    except Exception as exc:
        print(url, type(exc).__name__, exc)
PY

section "remote runtime readiness"
run python3 -m highschoolphysics.runtime_check --json
REMOTE_RECOVER
