#!/bin/bash
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SESSION_FILE="$HOME/.jarvis/data/claude/session_${TIMESTAMP}.txt"
echo "=== JARVIS CAPTURE: Claude Session ${TIMESTAMP} ===" > "$SESSION_FILE"
echo "Working Directory: $(pwd)" >> "$SESSION_FILE"
echo "Git Branch: $(git branch --show-current 2>/dev/null || echo 'not a git repo')" >> "$SESSION_FILE"
claude "$@" 2>&1 | tee -a "$SESSION_FILE"
echo "[Jarvis] Session saved: $SESSION_FILE"
