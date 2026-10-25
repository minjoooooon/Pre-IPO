#!/bin/bash
# Codex PreToolUse wrapper. Reuses the Claude Bash safety policy because
# Codex accepts the same permissionDecision deny shape for PreToolUse.

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
bash "$ROOT/.claude/hooks/block-destructive.sh"
