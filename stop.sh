#!/usr/bin/env bash
# Pre-IPO Normalizer shutdown (Linux / macOS).
# Reads backend/logs/server.pid, sends SIGTERM, then SIGKILL after a 5s grace.
#
# Slice 5 F75: confirm the PID still belongs to our `app.main` process
# before killing — between start.sh writing the pidfile and stop.sh
# reading it the original process could have died and Linux could have
# recycled the PID for an unrelated worker. Without the cmdline check,
# stop.sh could nuke the wrong process on a busy VPS.

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

# Confirm cmdline matches our app. /proc on Linux is authoritative; fall
# back to `ps` if /proc is unavailable (macOS). Refuse to kill if neither
# confirms the identity — operator can `rm $PID_FILE` to override.
matches_app() {
    if [[ -r "/proc/$1/cmdline" ]]; then
        # /proc/N/cmdline is NUL-separated; grep -a treats as text.
        tr '\0' ' ' </proc/"$1"/cmdline | grep -q "app.main"
    elif command -v ps >/dev/null 2>&1; then
        ps -o args= -p "$1" 2>/dev/null | grep -q "app.main"
    else
        return 1
    fi
}

if ! matches_app "$PID"; then
    echo "pid $PID does not look like app.main — refusing to kill (PID reuse?)" >&2
    echo "If this is correct, rm $PID_FILE and retry." >&2
    exit 1
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
