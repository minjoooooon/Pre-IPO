#!/usr/bin/env bash
# Pre-IPO Normalizer shutdown (Linux / macOS).
# Reads backend/logs/server.pid, sends SIGTERM, then SIGKILL after a 5s grace.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT/backend/logs/server.pid"

if [[ ! -f "$PID_FILE" ]]; then
    echo "no pid file at $PID_FILE — nothing to stop" >&2
    exit 0
fi

PID="$(cat "$PID_FILE")"
if ! kill -0 "$PID" 2>/dev/null; then
    echo "stale pid file ($PID not running); cleaning up"
    rm -f "$PID_FILE"
    exit 0
fi

kill -TERM "$PID"
# 5s grace for the lifespan shutdown handlers to flush state.
for _ in $(seq 1 10); do
    if ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$PID_FILE"
        echo "stopped pid=$PID"
        exit 0
    fi
    sleep 0.5
done

kill -KILL "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
echo "force-killed pid=$PID"
