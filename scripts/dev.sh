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
#
# On a dev machine `ps` and `lsof` either answer or the machine is broken, so there is
# one policy for a question that cannot be answered: `die` aborts the whole command with
# the tool's own error. Nothing is signalled and no record is deleted on the way out.
# The alternative, which this script carried until review, is for every caller to decide
# what an unanswered question means -- and every one of them has to be right.
set -euo pipefail
# `die` runs inside command substitutions, where `exit` would end only the subshell, and
# an `if`/`while` condition would swallow even that. Signalling the script itself is the
# one form that escapes both; the trap turns it into an ordinary exit status.
trap 'exit 1' TERM
# One clear message instead of a "command not found" from whichever lookup runs first.
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
# lsof rejects a malformed port with a usage dump; this names the variable that is wrong.
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

die() { red "✗ dev.sh: $1" >&2; kill -TERM $$; }

# Both tools exit 1 with nothing on stderr when nothing matched, and that is the only
# failure that means "nothing there". Anything else -- including a lookup that never ran
# -- is unanswered: the capture is opened and checked before the command, because a
# redirection that fails leaves no file, and afterwards an absent file cannot be told
# from empty stderr.
lookup() { # $@ = the lookup command
  local out rc errors
  errors="$(mktemp 2>/dev/null)" || errors=""
  if [ -z "$errors" ] || ! exec 9>"$errors"; then
    die "cannot capture '$1' stderr, so nothing was looked up"
  fi
  out="$("$@" 2>&9)" && rc=0 || rc=$?
  exec 9>&-
  if [ "$rc" -gt 1 ] || { [ "$rc" -eq 1 ] && [ -s "$errors" ]; }; then
    rc="the '$1' lookup failed (exit $rc): $(cat "$errors")"
    rm -f "$errors"
    die "$rc"
  fi
  rm -f "$errors"
  printf '%s' "$out"
}

# Listeners only: a bare `lsof -i :PORT` also lists every client of the port (a browser
# tab on the dev server, a proxy). Nothing is ever signalled because of this lookup; the
# port only tells the user who is in the way. `-w` drops lsof's mount warnings, which
# would otherwise arrive as stderr beside an ordinary "nothing listening".
port_pids() { lookup lsof -w -nP -t -iTCP:"$1" -sTCP:LISTEN; }

pid_file() { printf '%s/%s.pid' "$LOG_DIR" "$1"; }

record_line() { # $1=target $2=line number — a record that cannot be read is not a verdict
  local file value
  file="$(pid_file "$1")"
  value="$(sed -n "$2p" "$file")" || die "cannot read $file"
  printf '%s' "$value"
}

# Pids wrap, so a pidfile alone cannot tell our process from a stranger that inherited
# its number. `ps` pads its fields and the pidfile is read back line by line, so the
# value written by `start` and the one read now go through the same normalisation.
process_start_time() { # $1=pid — the start time, empty when no live process has that pid
  local fields state
  fields="$(lookup ps -o state=,lstart= -p "$1")"
  fields="$(printf '%s' "$fields" | tr -s '[:space:]' ' ' | sed 's/^ *//; s/ *$//')"
  state="${fields%% *}"
  # A process that has exited but has not been reaped yet still answers `ps`. It holds
  # no port and no signal reaches it, so it is gone for every purpose this script has.
  case "$state" in ''|Z*) return 0 ;; esac
  printf '%s' "${fields#* }"
}

record_pid() { # $1=target $2=pid — fails when $2 is already gone
  local started
  started="$(process_start_time "$2")"
  [ -n "$started" ] || return 1
  printf '%s\n%s\n' "$2" "$started" >"$(pid_file "$1")"
}

recorded_pid_field() { # $1=target — the recorded pid, empty when it is unusable as one
  local pid
  pid="$(record_line "$1" 1)"
  # A pid of 0 would signal dev.sh's own process group and 1 every process of the user,
  # so the field is bounded before it can reach `kill -TERM -<pid>` below.
  case "$pid" in ''|*[!0-9]*) return 0 ;; esac
  [ "$pid" -gt 1 ] || return 0
  printf '%s' "$pid"
}

recorded_pid() { # $1=target — echoes the pid while the record still names our process
  local pid started
  [ -f "$(pid_file "$1")" ] || return 1
  pid="$(recorded_pid_field "$1")"
  [ -n "$pid" ] || return 1
  started="$(record_line "$1" 2)"
  [ -n "$started" ] || return 1
  [ "$(process_start_time "$pid")" = "$started" ] || return 1
  printf '%s' "$pid"
}

group_member_pids() { # $1=pgid — the members that have not exited, on one line
  local listing group pid state members=""
  listing="$(lookup ps -axo pgid=,pid=,state=)"
  while read -r group pid state; do
    [ "$group" = "$1" ] || continue
    case "$state" in Z*) continue ;; esac
    members="$members $pid"
  done <<<"$listing"
  printf '%s' "${members# }"
}

# `kill -0 -<pgid>` alone is not the question: on Linux a group whose members have all
# exited but not been reaped still answers it, while macOS reports it gone. A process
# that has exited holds no port and receives no signal, so the group counts as alive
# only while it has a member that has not exited.
group_alive() { # $1=pgid
  kill -0 -"$1" 2>/dev/null || return 1
  [ -n "$(group_member_pids "$1")" ]
}

# A pid is not reused while it is still a process group id, so a group with this id is
# still the one `start` created. The second test catches a recorded pid that never led a
# group, where the group signal would reach nothing at all.
target_alive() { # $1=pid $2=recorded start time
  if group_alive "$1"; then return 0; fi
  [ "$(process_start_time "$1")" = "$2" ]
}

await_exit() { # $1=pid $2=recorded start time $3=how many tenths of a second to wait
  local waited=0
  while target_alive "$1" "$2" && [ "$waited" -lt "$3" ]; do
    sleep 0.1
    waited=$((waited + 1))
  done
  ! target_alive "$1" "$2"
}

holders_of() { # $1=port — the listening pids on one line, empty when the port is free
  local pids holders
  pids="$(port_pids "$1")"
  [ -n "$pids" ] || return 0
  holders="$(tr '\n' ' ' <<<"$pids")"
  printf '%s' "${holders% }"
}

report_port_holders() { # $1=port
  local holders
  holders="$(holders_of "$1")"
  [ -n "$holders" ] || return 0
  yellow "⚠ :$1 is held by pid(s) $holders, which dev.sh has no record of starting — not signalled"
}

stop_target() { # $1=target $2=port
  local file pid started
  file="$(pid_file "$1")"
  if pid="$(recorded_pid "$1")"; then
    started="$(record_line "$1" 2)"
    # The group, not the pid: the process that serves is a descendant of the launcher,
    # so the group is the only handle that reaches it whether or not `pdm` and `npm`
    # forward the signal, and whether or not they outlive it.
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
    pid="$(recorded_pid_field "$1")"
    # No process has that pid any more, yet its group still runs: the launcher is gone
    # and the server it started is not. Nothing is signalled -- once the group empties
    # the pid can be reused, and a reused pid leading a group of its own would look the
    # same from here -- so the record is kept and the survivors are named for the user.
    # A pid that *is* in use with a different start time is a stranger, not our group,
    # and falls through to the stale-record branch below.
    if [ -n "$pid" ] && [ -z "$(process_start_time "$pid")" ] && group_alive "$pid"; then
      red "✗ $1: the process in $file is gone but its group still runs pid(s) $(group_member_pids "$pid"). Nothing was signalled and $file is kept."
      return 1
    fi
    rm -f "$file"
    green "✓ $1 not running (stale pidfile)"
  else
    green "✓ $1 was not running"
  fi
  report_port_holders "$2"
}

refuse_a_foreign_port() { # $1=target $2=port
  local holders
  holders="$(holders_of "$2")"
  [ -n "$holders" ] || return 0
  red "✗ $1 not started: :$2 is held by pid(s) $holders, which dev.sh has no record of starting"
  return 1
}

already_running() { # $1=target $2=port
  local pid
  pid="$(recorded_pid "$1")" || return 1
  yellow "⚠ $1 already running (pid $pid) on :$2"
}

# `start` is only honest once the port answers: a server that fails after the first
# moment -- a bad config, a port taken inside a container -- would otherwise be reported
# as a working URL.
await_listening() { # $1=target $2=pid $3=port
  local started waited=0
  record_pid "$1" "$2" || { red "✗ $1 exited immediately — see $LOG_DIR/$1.log"; return 1; }
  started="$(record_line "$1" 2)"
  while [ "$waited" -lt 100 ]; do
    if ! target_alive "$2" "$started"; then
      rm -f "$(pid_file "$1")"
      red "✗ $1 exited while starting — see $LOG_DIR/$1.log"
      return 1
    fi
    [ -z "$(holders_of "$3")" ] || return 0
    sleep 0.1
    waited=$((waited + 1))
  done
  # `uvicorn --reload` can legitimately be slow, so the record is kept and the wait is
  # reported rather than called a failure.
  yellow "⚠ $1 (pid $2) is running but :$3 is not listening yet — see $LOG_DIR/$1.log"
}

data_services_are_up() {
  case "$(lookup docker ps --format '{{.Names}}')" in
    *ontoprism-postgres*) return 0 ;;
  esac
  red "✗ data services are not running — start them with: pdm run up"
  return 1
}

# Not fatal: compose is a convenience here and `data_services_are_up` is the real gate,
# but a compose failure is the reason that gate is about to fail, so it has to be seen.
start_data_services() {
  docker compose up -d >"$LOG_DIR/compose.log" 2>&1 ||
    yellow "⚠ docker compose did not start the data services — see $LOG_DIR/compose.log"
}

start_backend() {
  local pid
  already_running backend "$BACKEND_PORT" && return 0
  refuse_a_foreign_port backend "$BACKEND_PORT" || return 1
  data_services_are_up || return 1
  set -m  # give the job its own process group, so stop reaches the server it spawns
  nohup pdm run uvicorn backend.main:app --reload --port "$BACKEND_PORT" \
    >"$LOG_DIR/backend.log" 2>&1 &
  pid=$!
  set +m
  await_listening backend "$pid" "$BACKEND_PORT" || return 1
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
  await_listening frontend "$pid" "$FRONTEND_PORT" || return 1
  green "✓ frontend → http://localhost:$FRONTEND_PORT   (pid $pid, logs: $LOG_DIR/frontend.log)"
}

restart_target() { # $1=target $2=port
  stop_target "$1" "$2" || { red "✗ $1 not restarted: it could not be stopped"; return 1; }
  "start_$1"
}

action="${1:-}"
target="${2:-all}"
case "$action:$target" in
  start:backend) start_backend ;;
  start:frontend) start_frontend ;;
  start:all)
    start_data_services
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
  restart:backend) restart_target backend "$BACKEND_PORT" ;;
  restart:frontend) restart_target frontend "$FRONTEND_PORT" ;;
  restart:all)
    failed=0
    stop_target frontend "$FRONTEND_PORT" || failed=1
    stop_target backend "$BACKEND_PORT" || failed=1
    [ "$failed" -eq 0 ] || { red "✗ not restarted: a target could not be stopped"; exit 1; }
    start_data_services
    start_backend
    start_frontend
    ;;
  *)
    echo "usage: dev.sh {start|stop|restart} {backend|frontend|all}"
    exit 2
    ;;
esac
