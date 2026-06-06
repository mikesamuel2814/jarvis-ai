# Jarvis Heartbeat Checklist
# Executed by OpenClaw every 30 minutes via the jarvis-system skill.
# OpenClaw OWNS these checks. monitor.py owns CPU/RAM/GPU/disk/service health.

## Learning Pipeline

- Check if `$JARVIS_API_URL/learning-stats` shows queue > 10 items
  - If yes: `curl -s -X POST "$JARVIS_API_URL/learn" -H "X-API-Key: $JARVIS_API_KEY"`
  - Report result to Telegram if triggered

- Check if new golden examples > 5 since last heartbeat
  - If yes: note in status summary

## Project Monitoring

- Check git status on VPS projects:
  ```bash
  curl -s -X POST "$JARVIS_API_URL/action" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $JARVIS_API_KEY" \
    -d '{"action": "git_status_all"}'
  ```
  - If any repos are dirty for > 3 hours, send Telegram alert

- Check VPS PM2 processes:
  ```bash
  curl -s -X POST "$JARVIS_API_URL/action" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $JARVIS_API_KEY" \
    -d '{"action": "pm2_status"}'
  ```
  - If any process is not "online", alert immediately

## Memory Sync

- Sync ChromaDB → OpenClaw workspace:
  ```bash
  curl -s -X POST "$JARVIS_API_URL/memory/sync" \
    -H "X-API-Key: $JARVIS_API_KEY"
  ```

## Communication

- If any alerts were triggered: send summary to Telegram with details
- If all clear: complete silently (HEARTBEAT_OK — no message)
- Only alert if something actually needs attention

## Notes

- JARVIS_API_URL = http://127.0.0.1:8181
- All Jarvis endpoints require X-API-Key header
- This checklist runs every 30 minutes; do not duplicate monitor.py hardware checks
- monitor.py runs every 5 min for CPU/RAM/GPU/disk/service health
