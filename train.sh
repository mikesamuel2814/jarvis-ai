#!/bin/bash
echo "[$(date)] Starting Jarvis training cycle..."
LOG="$HOME/.jarvis/logs/training.log"
mkdir -p "$HOME/.jarvis/logs"
export PYTHONUNBUFFERED=1
DOCKER_DIR="$HOME/.jarvis/docker"
if command -v docker >/dev/null 2>&1 \
  && docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^jarvis-api$'; then
  echo "[$(date)] Indexing via Docker..." | tee -a "$LOG"
  (cd "$DOCKER_DIR" && docker compose run --rm jarvis-train) >> "$LOG" 2>&1
else
  echo "[$(date)] Indexing new data..." | tee -a "$LOG"
  "$HOME/.jarvis/venv/bin/python3" "$HOME/.jarvis/indexer.py" --now >> "$LOG" 2>&1
  echo "[$(date)] Current memory stats:" | tee -a "$LOG"
  "$HOME/.jarvis/venv/bin/python3" "$HOME/.jarvis/indexer.py" --stats >> "$LOG" 2>&1
fi
echo "[$(date)] Running brain learner..." | tee -a "$LOG"
"$HOME/.jarvis/venv/bin/python3" "$HOME/.jarvis/learner.py" >> "$LOG" 2>&1
echo "[$(date)] Training cycle complete." | tee -a "$LOG"
