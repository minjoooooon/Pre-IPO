#!/usr/bin/env bash
# ----------------------------------------------------------------
# Pre-IPO Normalizer launcher (Linux / macOS).
#
#   Background-launches `python -m app.main` from backend/, records the PID
#   to backend/logs/server.pid for stop.sh to read, and redirects stdout +
#   stderr to backend/logs/bootstrap.{out,err}. Once _configure_logging runs,
#   the app's own RotatingFileHandler takes over for backend/logs/app.log;
#   bootstrap.{out,err} only catches the brief pre-logging window.
#
#   Env vars (optional):
#     ADMIN_TOKEN  pin the admin token across restarts (else auto-generated;
#                  retrieve from backend/logs/admin_token.txt).
#     HOST/PORT    override listen interface (default 127.0.0.1:8000). On a
#                  VPS that needs to expose publicly: HOST=0.0.0.0 ./start.sh
# ----------------------------------------------------------------

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT/backend/logs"
PID_FILE="$LOG_DIR/server.pid"

mkdir -p "$LOG_DIR"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "already running (pid=$(cat "$PID_FILE"))" >&2
    exit 1
fi
# Stale PID file (process gone) — clean up so we don't refuse to start.
rm -f "$PID_FILE"

# Prefer a project venv if present; fall back to system python3. Matches the
# Windows pattern of running whatever `python` resolves to in PATH.
if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PY="$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/backend/.venv/bin/python" ]]; then
    PY="$ROOT/backend/.venv/bin/python"
else
    PY="$(command -v python3 || command -v python || true)"
fi
if [[ -z "$PY" ]]; then
    echo "python not found on PATH and no .venv detected" >&2
    exit 1
fi

cd "$ROOT/backend"
nohup "$PY" -m app.main \
    >"$LOG_DIR/bootstrap.out" \
    2>"$LOG_DIR/bootstrap.err" &
PID=$!
echo "$PID" >"$PID_FILE"

echo "started pid=$PID host=${HOST:-127.0.0.1} port=${PORT:-8000}"
echo "logs:   $LOG_DIR/app.log"
echo "token:  $LOG_DIR/admin_token.txt"
