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
#   Slice 5 F71: after launch we sleep 2s and re-check the PID — if the
#   child died (bind failure, import error, missing dep), we tail
#   bootstrap.err to stderr and exit non-zero instead of leaving a stale
#   PID file behind. Catches the "everything looked fine but nothing
#   actually started" failure mode that bit on a fresh checkout.
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

# Slice 5 F76: prefer `backend/.venv` first — that's the conventional VPS
# location since pyproject.toml lives in backend/. The root `.venv` is more
# often a tooling venv (ruff/black) without the app dependencies. Fall back
# to root .venv, then system python3.
if [[ -x "$ROOT/backend/.venv/bin/python" ]]; then
    PY="$ROOT/backend/.venv/bin/python"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
    PY="$ROOT/.venv/bin/python"
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

# Wait long enough for an import error / bind failure to actually kill the
# child. 2s covers the OKX/Bitget/Gate/HL/BingX bootstrap fan-out timing.
sleep 2

if ! kill -0 "$PID" 2>/dev/null; then
    echo "FAILED: pid=$PID exited within 2s of launch." >&2
    if [[ -s "$LOG_DIR/bootstrap.err" ]]; then
        echo "--- bootstrap.err (last 40 lines) ---" >&2
        tail -n 40 "$LOG_DIR/bootstrap.err" >&2
    fi
    rm -f "$PID_FILE"
    exit 1
fi

echo "started pid=$PID host=${HOST:-127.0.0.1} port=${PORT:-8000}"
echo "logs:   $LOG_DIR/app.log"
echo "token:  $LOG_DIR/admin_token.txt"
