---
name: jarvis-system
description: "Core Jarvis local system control — query AI brain, execute actions, check health, sync memory"
version: "2.0.0"
emoji: 🤖
os: ["linux"]
metadata:
  openclaw:
    requires:
      env:
        - JARVIS_API_KEY
        - JARVIS_API_URL
      bins:
        - curl
        - jq
    primaryEnv: JARVIS_API_KEY
    envVars:
      JARVIS_API_KEY:
        description: "Jarvis API authentication key (set as env var on Kali)"
        required: true
      JARVIS_API_URL:
        description: "Jarvis API base URL"
        required: false
      JARVIS_VPS_HOST:
        description: "VPS hostname or IP for SSH checks"
        required: false
---

# Jarvis System Control Skill

Full access to Jarvis 2.0 — your local AI system with 3-tier routing (Edge/Hybrid/Cloud).

## Quick Reference

### Query the AI Brain

```bash
# Auto-routing (Edge/Hybrid/Cloud selected automatically)
curl -s -X POST "${JARVIS_API_URL:-http://127.0.0.1:8181}/query" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $JARVIS_API_KEY" \
  -d '{"query": "YOUR QUESTION HERE", "user_id": "openclaw"}' | jq -r .response

# Force Cloud tier (Kimi K2.6)
curl -s -X POST "${JARVIS_API_URL:-http://127.0.0.1:8181}/query" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $JARVIS_API_KEY" \
  -d '{"query": "YOUR QUESTION HERE", "force_tier": "cloud"}' | jq -r .response

# Force Edge tier (local Ollama, fastest)
curl -s -X POST "${JARVIS_API_URL:-http://127.0.0.1:8181}/query" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $JARVIS_API_KEY" \
  -d '{"query": "YOUR QUESTION HERE", "force_tier": "edge"}' | jq -r .response
```

### System Health

```bash
curl -s "${JARVIS_API_URL:-http://127.0.0.1:8181}/health" | jq .
```

### System Info (CPU/RAM/GPU/Disk — instant, no LLM)

```bash
curl -s "${JARVIS_API_URL:-http://127.0.0.1:8181}/sysinfo" \
  -H "X-API-Key: $JARVIS_API_KEY" | jq .
```

### Memory Stats

```bash
curl -s "${JARVIS_API_URL:-http://127.0.0.1:8181}/stats" \
  -H "X-API-Key: $JARVIS_API_KEY" | jq .
```

### Execute Whitelisted Action

```bash
curl -s -X POST "${JARVIS_API_URL:-http://127.0.0.1:8181}/action" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $JARVIS_API_KEY" \
  -d '{"action": "ACTION_NAME", "arg": "OPTIONAL_ARG"}' | jq .
```

**AUTO actions** (no approval needed):
`ps`, `disk`, `memory`, `uptime`, `gpu`, `ports`, `logs_jarvis`, `docker_ps`,
`pm2_status`, `git_status_all`, `file_read`, `nginx_status`

**CONFIRM actions** (Telegram button):
`restart_jarvis`, `restart_ollama`, `reindex`, `docker_restart_api`, `git_pull_gw`

**APPROVE actions** (requires explicit "yes"):
`deploy_vps`, `ssh_cmd`, `shell`, `claude_task`, `file_write`, `reboot`

### Learning Stats

```bash
curl -s "${JARVIS_API_URL:-http://127.0.0.1:8181}/learning-stats" \
  -H "X-API-Key: $JARVIS_API_KEY" | jq .
```

### Trigger Training Now

```bash
curl -s -X POST "${JARVIS_API_URL:-http://127.0.0.1:8181}/learn" \
  -H "X-API-Key: $JARVIS_API_KEY" | jq .
```

### Sync Memory with OpenClaw

```bash
curl -s -X POST "${JARVIS_API_URL:-http://127.0.0.1:8181}/memory/sync" \
  -H "X-API-Key: $JARVIS_API_KEY" | jq .
```

### Metrics Dashboard

```bash
curl -s "${JARVIS_API_URL:-http://127.0.0.1:8181}/metrics" \
  -H "X-API-Key: $JARVIS_API_KEY" | jq .
```

### Send Feedback

```bash
# Thumbs up (marks as golden example)
curl -s -X POST "${JARVIS_API_URL:-http://127.0.0.1:8181}/feedback" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $JARVIS_API_KEY" \
  -d '{"interaction_id": "ID", "rating": "thumbs_up"}' | jq .

# Correct last response
curl -s -X POST "${JARVIS_API_URL:-http://127.0.0.1:8181}/correct" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $JARVIS_API_KEY" \
  -d '{"interaction_id": "ID", "correction": "CORRECT TEXT"}' | jq .
```

## Notes

- Jarvis API runs at port 8181, bound to 127.0.0.1 (not exposed to internet)
- All endpoints except `/health` require `X-API-Key` header
- OpenClaw webhook endpoint: `/webhook/openclaw` (requires HMAC signature)
- 3-tier routing: Edge (~70% queries, local Ollama) | Hybrid | Cloud (Kimi K2.6)
- Kimi K2.6: 262K context, $0.95/M input, $4.00/M output
