#!/usr/bin/env bash
# ontoprism dev process manager — start/stop/restart the backend + frontend.
# Invoked via `pdm run start-all|stop-all|restart-all|start-backend|...`.
#
# It signals only a process it started itself: the pid it recorded in
# .dev-logs/<target>.pid, and only while that pid still has the start time recorded
# beside it. Picking the victim out of a port lookup instead, or sweeping a range, is
# what took the Podman VM's gvproxy down on 2026-09-18 (docs/DATA_SETUP.md); AGENTS.md
# carries the rule, and backend/tests/test_dev_script.py keeps this script to it.
set -euo pipefail
# lsof is how the script sees a port held by something it did not start; without it
# every lookup comes back empty and a stranger on the port goes unmentioned.
command -v lsof >/dev/null || { echo "dev.sh needs lsof on PATH" >&2; exit 1; }
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# A port the caller exported wins over .env: a command aimed at one port must never
# act on whatever holds another.
requested_backend_port="${BACKEND_PORT:-}"
requested_frontend_port="${FRONTEND_PORT:-}"
if [ -f .env ]; then
  set -a
  # The supported local environment is intentionally user-owned.
  # shellcheck disable=SC1091
  source .env
  set +a
fi

BACKEND_PORT="${requested_backend_port:-${BACKEND_PORT:-8011}}"     # 8001 is the sibling fairdata backend
FRONTEND_PORT="${requested_frontend_port:-${FRONTEND_PORT:-5175}}"  # 5173 is the sibling fairdata frontend
# lsof exits 1 for a port that is not a number exactly as it does for "no listener".
for name in BACKEND_PORT FRONTEND_PORT; do
  case "${!name}" in
    *[!0-9]*) echo "dev.sh: $name='${!name}' is not a number (environment or .env)" >&2; exit 1 ;;
  esac
done
export ONTOPRISM_FASTAPI_ORIGIN="${ONTOPRISM_FASTAPI_ORIGIN:-http://127.0.0.1:$BACKEND_PORT}"
export ONTOPRISM_FASTAPI_TIMEOUT_MS="${ONTOPRISM_FASTAPI_TIMEOUT_MS:-5000}"
LOG_DIR=".dev-logs"
mkdir -p "$LOG_DIR"

green() { printf '\033[0;32m%s\033[0m\n' "$1"; }
yellow() { printf '\033[1;33m%s\033[0m\n' "$1"; }
red() { printf '\033[0;31m%s\033[0m\n' "$1"; }

# Listeners only: a bare `lsof -i :PORT` also lists every client of the port (a browser
# tab on the dev server, a proxy). Nothing here is ever signalled because of it; the
# port only tells the user who is in the way.
port_pids() { lsof -nP -t -iTCP:"$1" -sTCP:LISTEN 2>/dev/null || true; }

pid_file() { printf '%s/%s.pid' "$LOG_DIR" "$1"; }

# Pids wrap, so a pidfile alone cannot tell our process from a stranger that inherited
# its number. `ps` pads the field and the pidfile is read back line by line, so the
# value written by `start` and the one read now go through the same normalisation.
process_start_time() {
  ps -o lstart= -p "$1" 2>/dev/null | tr -s '[:space:]' ' ' | sed 's/^ *//; s/ *$//' || true
}

record_pid() { # $1=target $2=pid
  printf '%s\n%s\n' "$2" "$(process_start_time "$2")" >"$(pid_file "$1")"
}

recorded_pid() { # $1=target — echoes the pid only while it still names our process
  local file pid started
  file="$(pid_file "$1")"
  [ -f "$file" ] || return 1
  pid="$(sed -n '1p' "$file")"
  started="$(sed -n '2p' "$file")"
  [ -n "$pid" ] && [ -n "$started" ] || return 1
  [ "$(process_start_time "$pid")" = "$started" ] || return 1
  printf '%s' "$pid"
}

listening_pids_line() { # $1=port — the holders of the port on one line, empty if free
  local pids holders
  pids="$(port_pids "$1")"
  [ -n "$pids" ] || return 0
  holders="$(tr '\n' ' ' <<<"$pids")"
  printf '%s' "${holders% }"
}

stop_target() { # $1=target $2=port
  local file pid holders waited
  file="$(pid_file "$1")"
  if pid="$(recorded_pid "$1")"; then
    # The job was started in its own process group, so `pdm run uvicorn` and
    # `npm run dev` take the child that actually serves down with them.
    kill -TERM -"$pid" 2>/dev/null || true
    waited=0
    while [ -n "$(process_start_time "$pid")" ] && [ "$waited" -lt 100 ]; do
      sleep 0.1
      waited=$((waited + 1))
    done
    [ -z "$(process_start_time "$pid")" ] || kill -KILL -"$pid" 2>/dev/null || true
    rm -f "$file"
    green "✓ $1 stopped (pid $pid)"
  elif [ -f "$file" ]; then
    rm -f "$file"
    green "✓ $1 not running (stale pidfile)"
  else
    green "✓ $1 was not running"
  fi
  holders="$(listening_pids_line "$2")"
  [ -z "$holders" ] || yellow "⚠ :$2 is still held by pid $holders, which dev.sh did not start — not signalled"
}

refuse_a_foreign_port() { # $1=target $2=port
  local holders
  holders="$(listening_pids_line "$2")"
  [ -n "$holders" ] || return 0
  red "✗ $1 not started: :$2 is held by pid $holders, which dev.sh did not start"
  return 1
}

start_backend() {
  local pid
  if pid="$(recorded_pid backend)"; then
    yellow "⚠ backend already running (pid $pid) on :$BACKEND_PORT"
    return 0
  fi
  refuse_a_foreign_port backend "$BACKEND_PORT" || return 1
  if ! docker ps --format '{{.Names}}' | grep -q ontoprism-postgres; then
    red "✗ data services are not running — start them with: pdm run up"
    return 1
  fi
  set -m  # give the job its own process group, so stop reaches the server it spawns
  nohup pdm run uvicorn backend.main:app --reload --port "$BACKEND_PORT" \
    >"$LOG_DIR/backend.log" 2>&1 &
  pid=$!
  set +m
  record_pid backend "$pid"
  green "✓ backend  → http://localhost:$BACKEND_PORT   (pid $pid, logs: $LOG_DIR/backend.log)"
}

start_frontend() {
  local pid
  if pid="$(recorded_pid frontend)"; then
    yellow "⚠ frontend already running (pid $pid) on :$FRONTEND_PORT"
    return 0
  fi
  refuse_a_foreign_port frontend "$FRONTEND_PORT" || return 1
  [ -d frontend/node_modules ] || (cd frontend && npm install --silent)
  set -m  # give the job its own process group, so stop reaches the server it spawns
  nohup npm --prefix frontend run dev -- --port "$FRONTEND_PORT" --strictPort \
    >"$LOG_DIR/frontend.log" 2>&1 &
  pid=$!
  set +m
  record_pid frontend "$pid"
  green "✓ frontend → http://localhost:$FRONTEND_PORT   (pid $pid, logs: $LOG_DIR/frontend.log)"
}

action="${1:-}"
target="${2:-all}"
case "$action:$target" in
  start:backend) start_backend ;;
  start:frontend) start_frontend ;;
  start:all)
    docker compose up -d >/dev/null 2>&1 || true
    start_backend
    start_frontend
    ;;
  stop:backend) stop_target backend "$BACKEND_PORT" ;;
  stop:frontend) stop_target frontend "$FRONTEND_PORT" ;;
  stop:all)
    stop_target frontend "$FRONTEND_PORT"
    stop_target backend "$BACKEND_PORT"
    ;;
  restart:backend)
    stop_target backend "$BACKEND_PORT"
    sleep 1
    start_backend
    ;;
  restart:frontend)
    stop_target frontend "$FRONTEND_PORT"
    sleep 1
    start_frontend
    ;;
  restart:all)
    stop_target frontend "$FRONTEND_PORT"
    stop_target backend "$BACKEND_PORT"
    docker compose up -d >/dev/null 2>&1 || true
    sleep 1
    start_backend
    start_frontend
    ;;
  *)
    echo "usage: dev.sh {start|stop|restart} {backend|frontend|all}"
    exit 2
    ;;
esac
