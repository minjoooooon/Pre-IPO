#!/bin/bash
# Codex Stop wrapper.
#
# Codex launches matching hook handlers concurrently. For Stop checks we
# want deterministic ordering and at most one continuation prompt, so this
# wrapper runs the shared policies serially and emits the first block.

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
INPUT=$(cat)
MESSAGES=""

run_shared_stop_hook() {
  local hook="$1"
  printf '%s' "$INPUT" | bash "$ROOT/.claude/hooks/$hook"
}

for HOOK in stop-verify.sh state-enforcement.sh sensory-reminder.sh; do
  OUT=$(run_shared_stop_hook "$HOOK")
  [ -z "$OUT" ] && continue

  if echo "$OUT" | jq -e '.decision == "block"' >/dev/null 2>&1; then
    printf '%s\n' "$OUT"
    exit 0
  fi

  MSG=$(echo "$OUT" | jq -r '.hookSpecificOutput.additionalContext // .systemMessage // empty' 2>/dev/null || true)
  if [ -n "$MSG" ]; then
    if [ -n "$MESSAGES" ]; then
      MESSAGES="${MESSAGES}

${MSG}"
    else
      MESSAGES="$MSG"
    fi
  fi
done

if [ -n "$MESSAGES" ]; then
  jq -n --arg m "$MESSAGES" '{systemMessage: $m}'
fi
