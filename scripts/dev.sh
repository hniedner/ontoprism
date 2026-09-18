#!/usr/bin/env bash
# ontoprism dev process manager — start/stop/restart the backend + frontend.
# Invoked via `pdm run start-all|stop-all|restart-all|start-backend|...`.
set -euo pipefail
# Every port lookup below is an lsof call; without it each one would come back empty and
# `stop` would report "was not running" beside a live server.
command -v lsof >/dev/null || { echo "dev.sh needs lsof on PATH" >&2; exit 1; }
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# A port the caller exported wins over .env: a command aimed at one port must never
# signal whatever listens on another.
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
# lsof exits 1 for a malformed port exactly as it does for "no listener".
for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  case "$port" in
    '' | *[!0-9]*) echo "dev.sh: port '$port' is not a number" >&2; exit 1 ;;
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
# tab on the dev server, a proxy), and stop_port signals whatever this returns. The same
# class of defect as the port sweep that killed the Podman VM's gvproxy (docs/DATA_SETUP.md).
port_pids() { lsof -nP -t -iTCP:"$1" -sTCP:LISTEN 2>/dev/null || true; }

stop_port() { # $1=port $2=name
  local pids
  pids="$(port_pids "$1")"
  if [ -n "$pids" ]; then
    echo "$pids" | xargs kill -TERM 2>/dev/null || true
    sleep 1
    pids="$(port_pids "$1")"
    [ -n "$pids" ] && echo "$pids" | xargs kill -9 2>/dev/null || true
    green "✓ $2 stopped"
  else
    green "✓ $2 was not running"
  fi
}

start_backend() {
  if [ -n "$(port_pids "$BACKEND_PORT")" ]; then
    yellow "⚠ backend already running on :$BACKEND_PORT"
    return 0
  fi
  if ! docker ps --format '{{.Names}}' | grep -q ontoprism-postgres; then
    red "✗ data services are not running — start them with: pdm run up"
    return 1
  fi
  nohup pdm run uvicorn backend.main:app --reload --port "$BACKEND_PORT" \
    >"$LOG_DIR/backend.log" 2>&1 &
  green "✓ backend  → http://localhost:$BACKEND_PORT   (logs: $LOG_DIR/backend.log)"
}

start_frontend() {
  if [ -n "$(port_pids "$FRONTEND_PORT")" ]; then
    yellow "⚠ frontend already running on :$FRONTEND_PORT"
    return 0
  fi
  [ -d frontend/node_modules ] || (cd frontend && npm install --silent)
  nohup npm --prefix frontend run dev -- --port "$FRONTEND_PORT" --strictPort \
    >"$LOG_DIR/frontend.log" 2>&1 &
  green "✓ frontend → http://localhost:$FRONTEND_PORT   (logs: $LOG_DIR/frontend.log)"
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
  stop:backend) stop_port "$BACKEND_PORT" backend ;;
  stop:frontend) stop_port "$FRONTEND_PORT" frontend ;;
  stop:all)
    stop_port "$FRONTEND_PORT" frontend
    stop_port "$BACKEND_PORT" backend
    ;;
  restart:backend)
    stop_port "$BACKEND_PORT" backend
    sleep 1
    start_backend
    ;;
  restart:frontend)
    stop_port "$FRONTEND_PORT" frontend
    sleep 1
    start_frontend
    ;;
  restart:all)
    stop_port "$FRONTEND_PORT" frontend
    stop_port "$BACKEND_PORT" backend
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
