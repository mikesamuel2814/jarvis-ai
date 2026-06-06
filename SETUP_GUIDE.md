# Jarvis 2.0 — Complete Setup Guide
**Hybrid Cortex Architecture: Local Ollama + Kimi K2.6 + OpenClaw**

> For the local Claude Code session (ID: `53b5481c-f87f-4225-83c0-f53563b6c000`) to follow step by step.

---

## Quick Start (TL;DR)

```bash
# 1. Pull latest from GitHub
cd ~/.jarvis && git pull origin main

# 2. Run setup (idempotent, safe to re-run)
bash ~/.jarvis/setup_v2.sh

# 3. Verify everything
bash ~/.jarvis/verify.sh
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     JARVIS 2.0 SYSTEM                       │
│                                                             │
│  Telegram ──► OpenClaw Gateway (18789) ──► Jarvis API (8181)│
│                      │                         │            │
│                      │                    ┌────▼─────────┐  │
│                      │                    │  3-Tier Brain │  │
│                      │                    │  brain.py     │  │
│                      │                    └─┬──┬──┬──────┘  │
│                      │                      │  │  │         │
│                      │                   Edge Hyb Cloud      │
│                      │                      │  │  │         │
│                      │                  Ollama  │  Kimi K2.6│
│                      │                   7B  │  │  (256K)   │
│                      │                      │  │           │
│                      │                    ChromaDB (local)  │
│                      │                    8,621+ chunks     │
│                      │                                      │
│                 HEARTBEAT.md ◄──────────── /webhook/openclaw│
│                 (every 30min)                               │
└─────────────────────────────────────────────────────────────┘
```

### Three-Tier Brain

| Tier | What | When | Cost |
|------|------|------|------|
| **Edge** | Local Ollama (phi4-mini / qwen2.5-coder / deepseek-r1 / llava) | Greetings, sysinfo, short code questions, `!local` prefix | $0 |
| **Hybrid** | Local pre-analysis → Kimi reasoning | Medium-complexity code, multi-step reasoning, `!hybrid` prefix | Low |
| **Cloud** | Kimi K2.6 (262K ctx, thinking ON) | Architecture, security review, long context, `!cloud` prefix | ~$0.02/query |

---

## Prerequisites

Before running setup, confirm you have:

| Tool | Version | Check | Install |
|------|---------|-------|---------|
| Python | 3.11+ | `python3 --version` | Already on Kali |
| Node.js | 24 (or 22.19+) | `node --version` | `nvm install 24` |
| Ollama | Latest | `curl localhost:11434/api/tags` | Already installed |
| Required models | — | see below | `bash pull-special-models.sh` |

### Required Ollama Models

```bash
ollama pull phi4-mini
ollama pull qwen2.5-coder:7b
ollama pull deepseek-r1:7b
ollama pull llava:7b
ollama pull mxbai-embed-large
```

---

## Phase 1: Credentials Setup

### 1.1 Get Kimi K2.6 API Key

1. Go to **https://platform.kimi.ai**
2. Sign up / log in
3. Go to API Keys → Create new key
4. Copy the key (starts with `sk-...`)

### 1.2 Get Telegram Bot Token (if not already set)

1. Message **@BotFather** on Telegram
2. Send `/newbot` → follow prompts
3. Copy the bot token

### 1.3 Get Your Telegram User ID

1. Message **@userinfobot** on Telegram
2. Copy your numeric user ID (e.g., `123456789`)

### 1.4 Generate API Keys

```bash
# Jarvis API key
openssl rand -hex 32

# OpenClaw webhook secret
openssl rand -hex 32
```

### 1.5 Fill in secrets.env

```bash
nano ~/.jarvis/config/secrets.env
```

Fill in every `REPLACE_ME`:

```env
MOONSHOT_API_KEY=sk-your-kimi-key-here
TELEGRAM_BOT_TOKEN=1234567890:your-bot-token
TELEGRAM_USER_ID=123456789
JARVIS_API_KEY=generated-32-byte-hex
JARVIS_WEBHOOK_SECRET=another-32-byte-hex
JARVIS_VPS_HOST=38.47.35.16
```

---

## Phase 2: Run Setup Script

```bash
bash ~/.jarvis/setup_v2.sh
```

This script (idempotent, safe to re-run) does:
1. Verifies prerequisites
2. Creates directory structure
3. Sets up Python virtualenv + installs all deps
4. Creates secrets.env (if missing)
5. Installs OpenClaw globally via npm
6. Configures `~/.openclaw/openclaw.json` from template
7. Copies AGENTS.md, SOUL.md, HEARTBEAT.md to OpenClaw workspace
8. Installs jarvis-system skill
9. Installs systemd services (openclaw, jarvis-cursor)
10. Updates crontab with full 2.0 schedule
11. Tests Kimi K2.6 connection
12. Starts all services

---

## Phase 3: OpenClaw Manual Configuration

After setup_v2.sh runs, edit `~/.openclaw/openclaw.json` to add your Telegram user ID:

```bash
nano ~/.openclaw/openclaw.json
```

Find `allowFrom` and replace the placeholder:

```json5
allowFrom: ["tg:123456789"],   // Your actual Telegram user ID
```

Then verify OpenClaw can see your bot:

```bash
openclaw gateway status
# Should show: telegram channel active
```

Send a test message from Telegram to your bot:
```
/status
```

OpenClaw should reply with system status.

### 3.1 Pair with OpenClaw (if dmPolicy=pairing)

If you set `dmPolicy: "pairing"`, the bot will send a pairing code when you first message it:

```bash
openclaw pairing approve telegram <CODE>
```

---

## Phase 4: Verify API + Brain

### 4.1 Check API health

```bash
curl -s http://127.0.0.1:8181/health | jq .
```

Expected:
```json
{"status": "ok", "ollama": "ok", "chromadb": "ok"}
```

### 4.2 Test Edge tier

```bash
curl -s -X POST http://127.0.0.1:8181/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $JARVIS_API_KEY" \
  -d '{"query": "!edge disk space", "user_id": "test"}' | jq '{tier: .tier, response: .response[:100]}'
```

Expected: `"tier": "edge"`, instant response.

### 4.3 Test Cloud tier (Kimi K2.6)

```bash
curl -s -X POST http://127.0.0.1:8181/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $JARVIS_API_KEY" \
  -d '{"query": "!cloud explain what deepseek-r1 is", "user_id": "test"}' | jq '{tier: .tier, response: .response[:200]}'
```

Expected: `"tier": "cloud"`, deep Kimi response.

### 4.4 Test metrics

```bash
curl -s http://127.0.0.1:8181/metrics \
  -H "X-API-Key: $JARVIS_API_KEY" | jq .
```

### 4.5 Test memory sync

```bash
curl -s -X POST http://127.0.0.1:8181/memory/sync \
  -H "X-API-Key: $JARVIS_API_KEY" | jq .
```

---

## Phase 5: OpenClaw + ClawHub Skills

### 5.1 Verify jarvis-system skill is loaded

```bash
ls ~/.openclaw/workspace/skills/jarvis-system/
# Should show: SKILL.md  learned_lessons.md (after first training)
```

### 5.2 Browse ClawHub skills (optional)

```bash
# Open OpenClaw dashboard
# http://127.0.0.1:18789

# Or browse skills via CLI
openclaw skills list
```

### 5.3 Install a ClawHub skill

```bash
# Only install skills after reviewing their SKILL.md
openclaw skills install <skill-name>
# You'll be prompted for approval (approvalRequired: true in config)
```

### 5.4 Test heartbeat manually

```bash
# Trigger a heartbeat check right now
openclaw agent --message "Run the heartbeat checklist" --thinking high
```

---

## Phase 6: Cursor IDE Bridge

The cursor bridge watches `~/.cursor/logs/` and enriches new sessions with Kimi analysis.

### 6.1 Check watcher is running

```bash
systemctl status jarvis-cursor
```

### 6.2 View enrichment log

```bash
tail -f ~/.jarvis/data/cursor_enriched.jsonl | python3 -m json.tool
```

### 6.3 Manual enrichment of existing sessions

```bash
source ~/.jarvis/config/secrets.env
~/.jarvis/venv/bin/python3 -c "
import sys
sys.path.insert(0, '$HOME/.jarvis')
from cursor.bridge import process_session_file
from pathlib import Path
import glob

for f in sorted(glob.glob('$HOME/.cursor/logs/**/*.jsonl', recursive=True))[-5:]:
    result = process_session_file(Path(f))
    if result:
        print(f'Processed: {f}')
"
```

---

## Phase 7: Automation Schedule

After setup, these run automatically:

| When | What | Owns |
|------|------|------|
| Every 5 min | `monitor.py` — CPU/RAM/GPU/disk/service alerts | Jarvis |
| Every 10 min | `healer.py` — service recovery, log rotation | Jarvis |
| Every 30 min | OpenClaw heartbeat — git/PM2/queue/sync | OpenClaw |
| Every 6 hr | `train_v2.sh` — index + Kimi training + skill gen | Kimi |
| 9 AM daily | `analyze.py` — daily briefing → Telegram | Jarvis |
| Sunday 9 AM | `analyze.py --weekly` — weekly summary | Jarvis |
| Sunday 2 AM | `selftrain_v2.py` — full evolution | Kimi |
| 1st of month | `kimi/cost_tracker.py --audit` — cost report | Kimi |

---

## Phase 8: Telegram Commands

Send these to your Jarvis Telegram bot:

| Command | Tier | Description |
|---------|------|-------------|
| `/start` | Edge | Welcome |
| `/help` | Edge | Full command list |
| `/health` | Edge | Service status |
| `/stats` | Edge | Memory + model stats |
| `/sysinfo` | Edge | Live CPU/RAM/GPU/disk |
| `/selfcheck` | Hybrid | Full system report |
| `/index` | Hybrid | Trigger re-index |
| `/learn` | Cloud | Run training now |
| `/correct TEXT` | Cloud | Correct last response |
| `/remember KEY VALUE` | Edge | Save a fact |
| `/voice on\|off` | Edge | Toggle TTS |
| `/approve ID` | Edge | Approve pending action |
| `/deny ID` | Edge | Deny pending action |

**Tier prefixes (inline):**
- `!local` or `!edge` → force local Ollama
- `!cloud` or `!kimi` → force Kimi K2.6
- `!hybrid` → force Hybrid

---

## Troubleshooting

### OpenClaw not starting

```bash
# Check logs
journalctl -u openclaw -n 50 --no-pager

# Validate config
openclaw doctor --fix

# Test config syntax
node -e "JSON.parse(require('fs').readFileSync(process.env.HOME+'/.openclaw/openclaw.json','utf8').replace(/\/\/.*/g,'').replace(/,(\s*[}\]])/g,'$1'))"
```

### Kimi API errors

```bash
# Test connection directly
source ~/.jarvis/config/secrets.env
~/.jarvis/venv/bin/python3 -c "
from openai import OpenAI
client = OpenAI(api_key='$MOONSHOT_API_KEY', base_url='https://api.moonshot.ai/v1')
r = client.chat.completions.create(model='kimi-k2.6', messages=[{'role':'user','content':'Say OK'}], max_tokens=10)
print(r.choices[0].message.content)
"
```

Common errors:
- `401 Unauthorized` → wrong MOONSHOT_API_KEY
- `400 Bad Request` → check model ID is exactly `kimi-k2.6`
- `429 Too Many Requests` → rate limit; reduce concurrent calls

### brain.py tier routing wrong

```bash
# Check routing decisions log
tail -20 ~/.jarvis/data/routing_decisions.jsonl | python3 -m json.tool

# Check routing_rules.yaml
cat ~/.jarvis/config/routing_rules.yaml

# Test routing directly
~/.jarvis/venv/bin/python3 -c "
import sys; sys.path.insert(0, '$HOME/.jarvis')
from brain import route
queries = ['hello', 'analyze my architecture', 'fix this bug in my code']
for q in queries:
    print(f'{route(q).value:8} ← {q}')
"
```

### ChromaDB empty or corrupted

```bash
# Check chunk count
~/.jarvis/venv/bin/python3 -c "
import chromadb
c = chromadb.PersistentClient(path='$HOME/.jarvis/memory')
col = c.get_or_create_collection('jarvis_memory')
print(f'Chunks: {col.count()}')
"

# Force full re-index
~/.jarvis/venv/bin/python3 ~/.jarvis/indexer.py --force
```

### Webhook HMAC failures

```bash
# Check webhook secret matches in both secrets.env and config
grep JARVIS_WEBHOOK_SECRET ~/.jarvis/config/secrets.env
grep webhookSecret ~/.openclaw/openclaw.json  # if webhook mode
```

### Services not starting

```bash
# Check all service statuses
for svc in ollama jarvis jarvis-telegram openclaw jarvis-cursor; do
    echo -n "$svc: "
    systemctl is-active $svc 2>/dev/null || echo "not running"
done
```

---

## File Structure Reference

```
~/.jarvis/
├── brain.py                    # 3-tier router (Entry point for all queries)
├── api_v2.py                   # FastAPI REST server (2.0 — secure, authenticated)
├── api.py                      # FastAPI REST server (1.0 — keep as fallback)
├── telegram_bot.py             # Telegram bot
├── train_v2.sh                 # 6-hour training pipeline
├── selftrain_v2.py             # Weekly deep evolution
├── metrics.py                  # Dashboard data
├── setup_v2.sh                 # Setup script
├── requirements_v2.txt         # Python deps
│
├── kimi/
│   ├── client.py               # Kimi K2.6 API wrapper
│   ├── cost_tracker.py         # Cost monitoring
│   ├── trainer.py              # Cloud training pipeline
│   └── file_manager.py         # 256K context file uploads
│
├── openclaw/
│   ├── bridge.py               # ChromaDB ↔ OpenClaw sync
│   ├── heartbeat_handler.py    # Process heartbeat events
│   └── skill_generator.py      # Generate SKILL.md files
│
├── cursor/
│   ├── bridge.py               # Session parsing + Kimi enrichment
│   └── watcher.py              # File system watcher (systemd service)
│
├── learning/
│   ├── rapid_learner.py        # Real-time feedback (Loop A)
│   ├── queue_processor.py      # 6-hour queue (Loop B)
│   └── pattern_extractor.py    # Pattern analysis
│
├── memory/
│   ├── sync_engine.py          # Bidirectional sync coordinator
│   └── compressor.py           # Weekly compression
│
├── config/
│   ├── jarvis_v2.yaml          # Main 2.0 config
│   ├── kimi.yaml               # Kimi settings + privacy blocklist
│   ├── routing_rules.yaml      # Brain tier rules (auto-updated)
│   ├── mike_profile.yaml       # Always injected into prompts
│   ├── secrets.env             # API keys (never commit)
│   └── credentials.yaml        # (existing 1.0)
│
├── openclaw_config/
│   ├── openclaw.json.template  # Template → ~/.openclaw/openclaw.json
│   ├── HEARTBEAT.md            # 30-min checklist for OpenClaw
│   ├── AGENTS.md               # OpenClaw system prompt
│   └── SOUL.md                 # Personality definition
│
├── skills/
│   └── jarvis-system/
│       └── SKILL.md            # Source skill (copied to OpenClaw workspace)
│
├── systemd/
│   ├── openclaw.service        # OpenClaw gateway service
│   └── jarvis-cursor.service   # Cursor bridge service
│
├── data/
│   ├── interactions.jsonl      # All Q&A pairs
│   ├── routing_decisions.jsonl # Tier decisions (for accuracy tracking)
│   ├── learning_queue.jsonl    # Pending training items
│   ├── golden_examples.jsonl   # 👍 rated interactions
│   ├── lessons.jsonl           # Extracted lessons
│   ├── corrections.jsonl       # Direct corrections
│   ├── kimi_cost_history.jsonl # API cost tracking
│   └── openclaw_sync.jsonl     # Sync audit trail
│
└── logs/
    ├── jarvis.log
    ├── kimi_api.log
    ├── openclaw.log
    ├── cursor_bridge.log
    ├── training.log
    └── selftrain.log
```

---

## Security Checklist

- [ ] `secrets.env` is `chmod 600` and in `.gitignore`
- [ ] API binds to `127.0.0.1:8181` (not `0.0.0.0`)
- [ ] All action endpoints require `X-API-Key` header
- [ ] OpenClaw webhooks require HMAC signature (`X-Openclaw-Signature`)
- [ ] OpenClaw gateway binds to `127.0.0.1:18789` (not exposed)
- [ ] `dmPolicy: "allowlist"` with explicit `allowFrom` in OpenClaw config
- [ ] Privacy blocklist prevents payment codebase from reaching Kimi
- [ ] SSH keys, `.env` files, credentials never sent to Kimi API

---

## Cost Monitoring

```bash
# Today's Kimi spend
~/.jarvis/venv/bin/python3 ~/.jarvis/kimi/cost_tracker.py --today

# This month
~/.jarvis/venv/bin/python3 ~/.jarvis/kimi/cost_tracker.py --month

# Full audit by day
~/.jarvis/venv/bin/python3 ~/.jarvis/kimi/cost_tracker.py --audit
```

Expected monthly cost by usage:
- **Light** (~50 queries/day): $5–10
- **Medium** (~100 queries/day): $15–30
- **Heavy** (~200+ queries/day): $40–60

Pricing: $0.95/M input tokens, $4.00/M output tokens (Moonshot API, June 2026).

---

## Kimi K2.6 Quick Reference

| Property | Value |
|----------|-------|
| Model ID | `kimi-k2.6` |
| Base URL | `https://api.moonshot.ai/v1` |
| Context | 262,144 tokens |
| Thinking | ON by default |
| Reasoning field | `response.choices[0].message.reasoning_content` |
| Disable thinking | `extra_body={"thinking": {"type": "disabled"}}` |
| Temperature (thinking) | 1.0 |
| Temperature (instant) | 0.6 |
| Input price | $0.95/M tokens |
| Output price | $4.00/M tokens |
| SDK | `openai` (OpenAI-compatible) |

---

## OpenClaw Quick Reference

| Command | Description |
|---------|-------------|
| `openclaw onboard` | Interactive setup wizard |
| `openclaw gateway` | Start gateway |
| `openclaw gateway status` | Check gateway status |
| `openclaw doctor --fix` | Diagnose + repair config |
| `openclaw config get agent.model` | Check current model |
| `openclaw config set agent.model moonshot/kimi-k2.6` | Set model |
| `openclaw agent --message "..."` | Send message to agent |
| `openclaw skills list` | List installed skills |
| `openclaw pairing approve telegram <code>` | Approve Telegram pairing |

Control UI: **http://127.0.0.1:18789**

---

*Jarvis 2.0 — Built by Mike Samuel. Powered by Kimi K2.6, OpenClaw, ChromaDB, Ollama, FastAPI.*
*Architecture reviewed and implemented by Claude Code (session: `53b5481c-f87f-4225-83c0-f53563b6c000`).*
