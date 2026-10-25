#!/bin/bash
# Codex PostToolUse wrapper. Today Codex only emits Bash post-tool hooks,
# which is exactly the scope of truncation-check.sh.

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
bash "$ROOT/.claude/hooks/truncation-check.sh"
