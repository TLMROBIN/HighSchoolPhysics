#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/yub/Documents/trae_projects/HighSchoolPhysics}"
GITHUB_URL="${GITHUB_URL:-https://github.com/TLMROBIN/HighSchoolPhysics.git}"
BRANCH="${BRANCH:-main}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVER_HOST="${SERVER_HOST:-0.0.0.0}"
SERVER_PORT="${SERVER_PORT:-8765}"
SERVER_DB="${SERVER_DB:-data/school.sqlite3}"
SERVER_LOG="${SERVER_LOG:-data/server-auto-update.log}"
PID_FILE="${PID_FILE:-data/server.pid}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8765/}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "$1"
}

fail() {
  log "FAIL: $1" >&2
  exit 1
}

server_pids() {
  pgrep -f "${PYTHON_BIN} -m highschoolphysics.server.*--port ${SERVER_PORT}" || true
}

stop_server() {
  local pids
  pids="$(server_pids)"
  if [[ -z "$pids" ]]; then
    log "server is not running"
    return 0
  fi

  log "stopping server pids: ${pids//$'\n'/ }"
  kill $pids || true
  sleep 2

  pids="$(server_pids)"
  if [[ -n "$pids" ]]; then
    log "force stopping server pids: ${pids//$'\n'/ }"
    kill -9 $pids || true
  fi
}

start_server() {
  mkdir -p "$(dirname "$SERVER_LOG")"
  log "starting server on ${SERVER_HOST}:${SERVER_PORT} db=${SERVER_DB}"
  nohup "$PYTHON_BIN" -m highschoolphysics.server \
    --host "$SERVER_HOST" \
    --port "$SERVER_PORT" \
    --db "$SERVER_DB" \
    >> "$SERVER_LOG" 2>&1 < /dev/null &
  printf '%s\n' "$!" > "$PID_FILE"
}

wait_for_health() {
  "$PYTHON_BIN" - "$HEALTH_URL" <<'PY'
import sys
import time
from urllib.request import urlopen

url = sys.argv[1]
last_error = None
for _ in range(20):
    try:
        with urlopen(url, timeout=5) as response:
            if response.status == 200:
                print("[PASS] health check", url)
                raise SystemExit(0)
            last_error = f"status={response.status}"
    except Exception as exc:
        last_error = f"{type(exc).__name__}: {exc}"
    time.sleep(1)
print("[FAIL] health check", url, last_error, file=sys.stderr)
raise SystemExit(1)
PY
}

main() {
  cd "$PROJECT_DIR"
  mkdir -p data

  local before after
  before="$(git rev-parse HEAD)"
  log "current HEAD=${before}"
  log "fetching ${GITHUB_URL} ${BRANCH}"
  git fetch --prune "$GITHUB_URL" "+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}"
  after="$(git rev-parse "refs/remotes/origin/${BRANCH}")"
  log "fetched HEAD=${after}"

  if [[ "$before" != "$after" ]]; then
    log "updating checkout to ${after}"
    git reset --hard FETCH_HEAD
  else
    log "checkout already current"
  fi

  if [[ "$before" != "$after" || -z "$(server_pids)" ]]; then
    stop_server
    start_server
  else
    log "server already running on current checkout"
  fi

  wait_for_health
  log "auto-update complete"
}

main "$@"
