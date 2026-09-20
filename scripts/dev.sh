#!/usr/bin/env bash
# ontoprism dev process manager — start/stop/restart the backend + frontend.
# Invoked via `pdm run start-all|stop-all|restart-all|start-backend|...`.
#
# `start` records the pid it launched in .dev-logs/<target>.pid together with that
# process's start time, and gives the job its own process group. `stop` signals that
# group, and only after the recorded start time confirmed the pid is still ours.
# Picking the victim out of a port lookup instead, or sweeping a range, is what took the
# Podman VM's gvproxy down on 2026-09-18 (docs/DATA_SETUP.md); AGENTS.md carries the
# rule, and backend/tests/test_dev_script.py keeps this script to it.
set -euo pipefail
# The two lookups this script cannot work without. Missing, every lookup comes back
# empty, and an empty answer reads as "port free" and "process gone".
for tool in lsof ps; do
  command -v "$tool" >/dev/null || { echo "dev.sh needs $tool on PATH" >&2; exit 1; }
done
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
# A malformed port makes lsof exit 1 exactly as a free port does, so a foreign holder
# would be neither reported by `stop` nor refused by `start`.
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

# Both lookup tools exit 1 with nothing on stderr when nothing matched, and that is the
# only failure that means "nothing there". Anything else is a broken lookup, and a broken
# lookup that read as "port free" or "process gone" is how `stop` would report success
# over a live server and delete the only record of it.
lookup() { # $@ = the lookup command
  local out rc errors
  errors="$LOG_DIR/.lookup-stderr.$$"
  out="$("$@" 2>"$errors")" && rc=0 || rc=$?
  if [ "$rc" -gt 1 ] || { [ "$rc" -eq 1 ] && [ -s "$errors" ]; }; then
    red "✗ dev.sh: '$1' lookup failed (exit $rc): $(cat "$errors")" >&2
    rm -f "$errors"
    return 1
  fi
  rm -f "$errors"
  printf '%s' "$out"
}

# Listeners only: a bare `lsof -i :PORT` also lists every client of the port (a browser
# tab on the dev server, a proxy). Nothing is ever signalled because of this lookup; the
# port only tells the user who is in the way.
port_pids() { lookup lsof -nP -t -iTCP:"$1" -sTCP:LISTEN; }

pid_file() { printf '%s/%s.pid' "$LOG_DIR" "$1"; }

# Pids wrap, so a pidfile alone cannot tell our process from a stranger that inherited
# its number. `ps` pads its fields and the pidfile is read back line by line, so the
# value written by `start` and the one read now go through the same normalisation.
process_start_time() { # $1=pid — the start time, empty when no live process has that pid
  local fields state
  fields="$(lookup ps -o state=,lstart= -p "$1")" || return 1
  fields="$(printf '%s' "$fields" | tr -s '[:space:]' ' ' | sed 's/^ *//; s/ *$//')"
  state="${fields%% *}"
  # A process that has exited but has not been reaped yet still answers `ps`. It holds
  # no port and no signal reaches it, so it is gone for every purpose this script has.
  case "$state" in ''|Z*) return 0 ;; esac
  printf '%s' "${fields#* }"
}

record_pid() { # $1=target $2=pid — fails when $2 is already gone
  local started
  started="$(process_start_time "$2")" || return 1
  [ -n "$started" ] || return 1
  printf '%s\n%s\n' "$2" "$started" >"$(pid_file "$1")"
}

# 0 = the pidfile still names our process (echoed), 1 = it does not, 2 = a lookup broke
# and the question is unanswered, which is never the same as "not ours".
recorded_pid() { # $1=target
  local file pid started now
  file="$(pid_file "$1")"
  [ -f "$file" ] || return 1
  pid="$(sed -n '1p' "$file")"
  started="$(sed -n '2p' "$file")"
  # `kill -0` and `kill -1` are broadcasts, not process groups, and a pid field that is
  # not a plain number would be pasted straight into the signal below.
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  [ "$pid" -gt 1 ] || return 1
  [ -n "$started" ] || return 1
  now="$(process_start_time "$pid")" || return 2
  [ "$now" = "$started" ] || return 1
  printf '%s' "$pid"
}

group_alive() { kill -0 -"$1" 2>/dev/null; }

# A pid is not reused while it is still a process group id, so a group with this id is
# still the one `start` created. The second test catches a recorded pid that never led a
# group, where the group signal reaches nothing at all. A broken `ps` counts as alive, so
# the caller keeps the pidfile instead of dropping a live server's only record.
target_alive() { # $1=pid $2=recorded start time
  local now
  if group_alive "$1"; then return 0; fi
  now="$(process_start_time "$1")" || return 0
  [ "$now" = "$2" ]
}

await_exit() { # $1=pid $2=recorded start time $3=how many tenths of a second to wait
  local waited=0
  while target_alive "$1" "$2" && [ "$waited" -lt "$3" ]; do
    sleep 0.1
    waited=$((waited + 1))
  done
  ! target_alive "$1" "$2"
}

listening_pids_line() { # $1=port — the holders on one line, empty when the port is free
  local pids holders
  pids="$(port_pids "$1")" || return 1
  [ -n "$pids" ] || return 0
  holders="$(tr '\n' ' ' <<<"$pids")"
  printf '%s' "${holders% }"
}

report_port_holders() { # $1=port — after our own process is gone, anything left is not ours
  local holders
  holders="$(listening_pids_line "$1")" || return 1
  [ -n "$holders" ] || return 0
  yellow "⚠ :$1 is held by pid $holders, which dev.sh has no record of starting — not signalled"
}

stop_target() { # $1=target $2=port
  local file pid started status
  file="$(pid_file "$1")"
  pid="$(recorded_pid "$1")" && status=0 || status=$?
  if [ "$status" -eq 2 ]; then
    red "✗ $1: cannot tell whether $file still names our process; nothing signalled"
    return 1
  fi
  if [ "$status" -eq 0 ]; then
    started="$(sed -n '2p' "$file")"
    # The group, not the pid: `pdm run uvicorn` and `npm run dev` serve from a child that
    # would keep the port after its launcher is gone, and neither forwards the signal.
    kill -TERM -"$pid" 2>/dev/null || true
    if ! await_exit "$pid" "$started" 50; then
      kill -KILL -"$pid" 2>/dev/null || true
      await_exit "$pid" "$started" 20 || true
    fi
    if target_alive "$pid" "$started"; then
      red "✗ $1 did not stop: pid $pid is still there. $file is kept, so the next stop still knows it is ours."
      return 1
    fi
    rm -f "$file"
    green "✓ $1 stopped (pid $pid)"
  elif [ -f "$file" ]; then
    rm -f "$file"
    green "✓ $1 not running (stale pidfile)"
  else
    green "✓ $1 was not running"
  fi
  report_port_holders "$2"
}

refuse_a_foreign_port() { # $1=target $2=port
  local holders
  holders="$(listening_pids_line "$2")" || return 1
  [ -n "$holders" ] || return 0
  red "✗ $1 not started: :$2 is held by pid $holders, which dev.sh has no record of starting"
  return 1
}

already_running() { # $1=target $2=port — 0 = ours is up, 1 = it is not
  local pid status
  pid="$(recorded_pid "$1")" && status=0 || status=$?
  if [ "$status" -eq 2 ]; then
    red "✗ $1: cannot tell whether $(pid_file "$1") still names our process; not starting another"
    exit 1
  fi
  [ "$status" -eq 0 ] || return 1
  yellow "⚠ $1 already running (pid $pid) on :$2"
}

record_or_report() { # $1=target $2=pid
  # Half a second catches a launch that fails outright — a missing project, a bad
  # interpreter. A failure after that only shows in the log.
  sleep 0.5
  record_pid "$1" "$2" && return 0
  red "✗ $1 exited immediately — see $LOG_DIR/$1.log"
  return 1
}

start_backend() {
  local pid
  already_running backend "$BACKEND_PORT" && return 0
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
  record_or_report backend "$pid" || return 1
  green "✓ backend  → http://localhost:$BACKEND_PORT   (pid $pid, logs: $LOG_DIR/backend.log)"
}

start_frontend() {
  local pid
  already_running frontend "$FRONTEND_PORT" && return 0
  refuse_a_foreign_port frontend "$FRONTEND_PORT" || return 1
  [ -d frontend/node_modules ] || (cd frontend && npm install --silent)
  set -m  # give the job its own process group, so stop reaches the server it spawns
  nohup npm --prefix frontend run dev -- --port "$FRONTEND_PORT" --strictPort \
    >"$LOG_DIR/frontend.log" 2>&1 &
  pid=$!
  set +m
  record_or_report frontend "$pid" || return 1
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
    # Both targets are attempted even when the first could not be stopped, and the
    # command still reports the failure.
    failed=0
    stop_target frontend "$FRONTEND_PORT" || failed=1
    stop_target backend "$BACKEND_PORT" || failed=1
    exit "$failed"
    ;;
  restart:backend)
    stop_target backend "$BACKEND_PORT"
    start_backend
    ;;
  restart:frontend)
    stop_target frontend "$FRONTEND_PORT"
    start_frontend
    ;;
  restart:all)
    stop_target frontend "$FRONTEND_PORT"
    stop_target backend "$BACKEND_PORT"
    docker compose up -d >/dev/null 2>&1 || true
    start_backend
    start_frontend
    ;;
  *)
    echo "usage: dev.sh {start|stop|restart} {backend|frontend|all}"
    exit 2
    ;;
esac
