#!/bin/bash
# Start Jarvis API + Telegram (use after reboot if systemd not running)
# Prefer Docker: bash ~/.jarvis/docker/up.sh  (jdocker-up)
set -e
JARVIS=/home/kali/.jarvis
mkdir -p "$JARVIS/logs" "$JARVIS/data"

if command -v docker >/dev/null 2>&1 \
  && docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q '^jarvis-api$'; then
  echo "Jarvis Docker containers exist — use: bash $JARVIS/docker/up.sh"
  exit 0
fi

if ! systemctl is-active --quiet ollama 2>/dev/null; then
  echo "Starting ollama..."
  sudo systemctl start ollama || true
fi

if systemctl is-active --quiet jarvis 2>/dev/null; then
  echo "jarvis.service already active"
else
  if command -v systemctl >/dev/null && [ -f /etc/systemd/system/jarvis.service ]; then
    sudo systemctl start jarvis jarvis-telegram 2>/dev/null && exit 0
  fi
  echo "Starting API on port 8181..."
  pkill -f "$JARVIS/api.py" 2>/dev/null || true
  sleep 1
  nohup /usr/bin/python3 "$JARVIS/api.py" >> "$JARVIS/logs/jarvis.log" 2>&1 &
fi

if systemctl is-active --quiet jarvis-telegram 2>/dev/null; then
  echo "jarvis-telegram.service already active"
else
  echo "Starting Telegram bot..."
  pkill -f "$JARVIS/telegram_bot.py" 2>/dev/null || true
  sleep 1
  nohup /usr/bin/python3 "$JARVIS/telegram_bot.py" >> "$JARVIS/logs/telegram_bot.log" 2>&1 &
fi

sleep 4
curl -sf "http://localhost:8181/health" | python3 -m json.tool 2>/dev/null || {
  echo "API not responding — check $JARVIS/logs/jarvis.log"
  exit 1
}
echo "Jarvis is up."
