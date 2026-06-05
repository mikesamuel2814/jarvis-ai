#!/bin/bash
# Start Jarvis microservices via Docker Compose (stops conflicting systemd units).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not installed. Run: bash $DIR/install-docker.sh"
  exit 1
fi

if [[ ! -f .env ]]; then
  id -u &>/dev/null && {
    echo "UID=$(id -u)" > .env
    echo "GID=$(id -g)" >> .env
    cat .env.example >> .env 2>/dev/null || true
  }
fi

# Prefer 'docker compose' plugin, fall back to standalone docker-compose
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
else
  COMPOSE="docker-compose"
fi
COMPOSE="${COMPOSE_OVERRIDE:-$COMPOSE}"

# Build directly with docker build so we can pass --security-opt seccomp=unconfined.
# docker-compose build / BuildKit do NOT forward this flag from daemon.json on Kali.
echo "Building images..."
if ! docker build --security-opt seccomp=unconfined -t jarvis-app:1.0 "$DIR"; then
  echo "Docker build failed — systemd services unchanged."
  exit 1
fi

# Now safe to hand over from systemd to Docker
if systemctl is-active --quiet jarvis 2>/dev/null; then
  echo "Stopping systemd jarvis services (Docker takes over)..."
  sudo systemctl stop jarvis jarvis-telegram 2>/dev/null || true
  sudo systemctl disable jarvis jarvis-telegram 2>/dev/null || true
fi
pkill -f "$HOME/.jarvis/api.py" 2>/dev/null || true
pkill -f "$HOME/.jarvis/telegram_bot.py" 2>/dev/null || true

$COMPOSE up -d jarvis-api jarvis-telegram

echo "Waiting for API health..."
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${API_PORT:-8181}/health" >/dev/null 2>&1; then
    curl -s "http://127.0.0.1:${API_PORT:-8181}/health" | python3 -m json.tool 2>/dev/null || true
    echo ""
    echo "Jarvis Docker stack is up."
    $COMPOSE ps
    exit 0
  fi
  sleep 2
done
echo "API did not become healthy — check: $COMPOSE logs jarvis-api"
exit 1
