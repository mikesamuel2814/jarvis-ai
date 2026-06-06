#!/bin/bash
# Jarvis 2.0 — Enhanced 6-Hour Training Pipeline
# Cron: 0 */6 * * * ~/.jarvis/venv/bin/python3 ~/.jarvis/train_v2.sh
# Logs to: ~/.jarvis/logs/training.log

set -euo pipefail
export PYTHONUNBUFFERED=1

JARVIS_HOME="${JARVIS_HOME:-$HOME/.jarvis}"
VENV="$JARVIS_HOME/venv"
PYTHON="$VENV/bin/python3"
LOG="$JARVIS_HOME/logs/training.log"
LOCK="$JARVIS_HOME/data/training.lock"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ===== Jarvis Training Cycle Starting =====" | tee -a "$LOG"

# Prevent overlapping runs
if [ -f "$LOCK" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -lt 21600 ]; then
        echo "[$(date)] Training already running (lock age: ${LOCK_AGE}s). Skipping." | tee -a "$LOG"
        exit 0
    fi
    echo "[$(date)] Stale lock detected — removing." | tee -a "$LOG"
    rm -f "$LOCK"
fi
touch "$LOCK"
trap "rm -f '$LOCK'" EXIT

# ── 1. Incremental re-index ──────────────────────────────────────
echo "[$(date '+%H:%M:%S')] Step 1/5: Incremental indexing..." | tee -a "$LOG"
"$PYTHON" "$JARVIS_HOME/indexer.py" \
    --sources=claude,cursor,git,shell,vps \
    --embed-model=mxbai-embed-large \
    >> "$LOG" 2>&1 || echo "[WARN] Indexer returned non-zero" | tee -a "$LOG"

# ── 2. Process learning queue ────────────────────────────────────
echo "[$(date '+%H:%M:%S')] Step 2/5: Processing learning queue..." | tee -a "$LOG"
"$PYTHON" -c "
from learning.queue_processor import run
import json, sys
sys.path.insert(0, '$JARVIS_HOME')
result = run()
print(json.dumps(result, indent=2))
" >> "$LOG" 2>&1 || echo "[WARN] Queue processor returned non-zero" | tee -a "$LOG"

# ── 3. Kimi cloud pattern analysis ───────────────────────────────
echo "[$(date '+%H:%M:%S')] Step 3/5: Kimi cloud training..." | tee -a "$LOG"
"$PYTHON" -c "
from kimi.trainer import run_training_cycle
import json, sys
sys.path.insert(0, '$JARVIS_HOME')
result = run_training_cycle()
print(json.dumps(result, indent=2))
" >> "$LOG" 2>&1 || echo "[WARN] Kimi trainer returned non-zero" | tee -a "$LOG"

# ── 4. Sync to OpenClaw ──────────────────────────────────────────
echo "[$(date '+%H:%M:%S')] Step 4/5: Syncing to OpenClaw workspace..." | tee -a "$LOG"
"$PYTHON" -c "
from memory.sync_engine import full_sync
import json, sys
sys.path.insert(0, '$JARVIS_HOME')
result = full_sync()
print(json.dumps(result, indent=2))
" >> "$LOG" 2>&1 || echo "[WARN] Sync returned non-zero" | tee -a "$LOG"

# ── 5. Copy skills to OpenClaw skills dir ────────────────────────
echo "[$(date '+%H:%M:%S')] Step 5/5: Publishing skills to OpenClaw..." | tee -a "$LOG"
SKILLS_SRC="$JARVIS_HOME/data/extracted_skills.jsonl"
SKILLS_DST="${HOME}/.openclaw/workspace/skills/jarvis-system"
mkdir -p "$SKILLS_DST"

"$PYTHON" -c "
from openclaw.skill_generator import generate_skill_files
import sys
sys.path.insert(0, '$JARVIS_HOME')
n = generate_skill_files()
print(f'Generated {n} skill files')
" >> "$LOG" 2>&1 || echo "[WARN] Skill generator returned non-zero" | tee -a "$LOG"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ===== Training Cycle Complete. Jarvis is smarter. =====" | tee -a "$LOG"
