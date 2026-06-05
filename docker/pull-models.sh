#!/bin/bash
# Pull specialty Ollama models (embeddings, vision, code).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
MODELS=(
  nomic-embed-text
  mxbai-embed-large
  llava:7b
  codellama:7b
  starcoder2:7b
)

pull_one() {
  local m="$1"
  echo "=== Pulling $m ==="
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^jarvis-ollama$'; then
    docker exec jarvis-ollama ollama pull "$m"
  elif command -v ollama >/dev/null 2>&1; then
    ollama pull "$m"
  else
    echo "No ollama CLI and jarvis-ollama container not running."
    exit 1
  fi
}

for m in "${MODELS[@]}"; do
  pull_one "$m"
done

echo "=== Installed models ==="
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^jarvis-ollama$'; then
  docker exec jarvis-ollama ollama list
else
  ollama list
fi
