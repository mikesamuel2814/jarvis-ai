# Jarvis — Personal AI Brain

> **"Jarvis is the brain. Claude is every neuron."**

Jarvis is a fully autonomous personal AI assistant running locally on a Kali Linux workstation. It combines a local LLM brain, semantic memory, Telegram interface, voice responses, proactive decision-making, and self-healing — all running on consumer hardware.

---

## Owner

**Mike Samuel** — Full-Stack Developer  
Machine: Kali Linux · i9-14900KF · 64GB RAM · RTX 3050 6GB VRAM  
Telegram Bot: [@MikePiJarvisBot](https://t.me/MikePiJarvisBot)

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     JARVIS BRAIN                        │
│                                                         │
│  Telegram Bot ──► API (FastAPI :8181) ──► Ollama LLMs  │
│       │                  │                              │
│       │              ChromaDB                          │
│       │           Semantic Memory                      │
│       │            (8,621+ chunks)                     │
│       │                  │                             │
│  Voice (TTS) ◄── Decision Engine ◄── Monitor (5min)    │
│                                                         │
│  Healer (10min) ────────────────► Self-Recovery        │
└─────────────────────────────────────────────────────────┘
```

| Component        | Detail                                         |
|------------------|------------------------------------------------|
| Brain            | DeepSeek-R1 7B via Ollama (GPU, port 11434)   |
| Memory           | ChromaDB at `~/.jarvis/memory/` (8,621 chunks)|
| API              | FastAPI at `http://localhost:8181`             |
| Telegram         | @MikePiJarvisBot (python-telegram-bot)         |
| Embed Model      | mxbai-embed-large                              |
| Voice            | edge-tts (en-GB-RyanNeural) → MP3 via mpg123  |
| Self-Healer      | healer.py — runs every 10 min via cron         |

---

## Model Routing

Jarvis automatically selects the best model for each query:

| Trigger | Model | Purpose |
|---------|-------|---------|
| Short greetings, casual chat | `phi4-mini` | Fast, lightweight |
| Code keywords (bug, function, class…) | `qwen2.5-coder:7b` | Code specialist |
| Reasoning keywords (why, analyze, explain…) | `deepseek-r1:7b` | Deep reasoning |
| Vision / image analysis | `llava:7b` | Multimodal |
| Default / fallback | `deepseek-r1:7b` | Primary brain |

---

## Core Features

### 1. Conversational AI (Telegram + CLI)

- **Telegram Bot** — full chat interface with per-user conversation history (30 messages)
- **CLI (`jarvis_cli.py`)** — interactive terminal REPL with streaming responses
- **Context-aware** — every query is augmented with relevant memories from ChromaDB (RAG)
- **Smart routing** — casual queries use fast model, complex ones use the brain model
- **`!cmd`** prefix in CLI — run shell commands and inject output into context
- **Image analysis** — send photos to Telegram bot for vision analysis (llava:7b)

### 2. Semantic Memory (RAG)

- **ChromaDB vector database** — 8,621+ embedded chunks across all knowledge sources
- **Cosine distance retrieval** — finds most relevant context for every query
- **Priority retrieval** — `lesson` and `golden` chunks surface first
- **Distance threshold** — 0.55 filter removes low-relevance noise
- **Knowledge sources indexed:**
  - Claude Code session transcripts (`~/.jarvis/data/claude/`)
  - Cursor IDE session logs (`~/.jarvis/data/cursor/`)
  - All git repositories in `~/Projects/` (Python, JS, TS, Go, Rust, Bash, YAML, JSON)
  - Shell history (`~/.zsh_history`)
  - VPS deployment logs (`~/.jarvis/data/deployments/`)

### 3. Continuous Brain Training (Learning Loop)

Every interaction can make Jarvis smarter:

- **👍 / 👎 buttons** on every Telegram response — rate quality instantly
- **`/correct TEXT`** — correct a wrong answer; Jarvis extracts the lesson via LLM
- **`/learn`** — manually trigger brain training run
- **Automatic training every 6 hours** via cron (`train.sh`)
- **learner.py processing:**
  - 👍 rated → stored as `golden` examples (high priority in memory)
  - 👎 rated → DeepSeek-R1 extracts rules → stored as `lesson` (high priority)
  - Corrections → stored as `correction` (highest priority)
- **Weekly full re-index** every Sunday 2am (`selftrain.py`)

### 4. Action Execution (44 Whitelisted Actions)

Jarvis understands natural language requests and executes actions with permission gates:

**Permission Tiers:**

| Tier | Confirmation Required | Examples |
|------|-----------------------|---------|
| `AUTO` | None — executes immediately | Show disk space, memory usage, logs |
| `CONFIRM` | One-tap Telegram button | Restart Jarvis, restart Ollama, re-index |
| `APPROVE` | Type "yes" confirmation | Deploy to VPS, SSH commands, shell, reboot |

**Read-Only Actions (AUTO):**
`ps` · `disk` · `memory` · `uptime` · `gpu` · `ports` · `who` · `services` · `logs_jarvis` · `logs_telegram` · `logs_ollama` · `crontab` · `network` · `ollama_models` · `top5_cpu` · `top5_mem` · `tailscale` · `docker_ps` · `docker_stats`

**Medium Risk (CONFIRM):**
`restart_jarvis` · `restart_telegram` · `restart_ollama` · `restart_monitor` · `reindex` · `stop_jarvis` · `clear_history` · `docker_restart_*` · `docker_up/down`

**High Risk (APPROVE):**
`deploy_vps` · `ssh_cmd` · `shell` · `claude_task` · `reboot` · `update_system`

### 5. Proactive Decision Engine

Jarvis monitors systems every 5 minutes and acts autonomously on safe issues:

- **Disk alert** — warns when disk >90%, critical at >92%
- **RAM alert** — warns at >85%, critical at >90%
- **CPU alert** — warns at sustained >88%
- **GPU temperature** — alerts at >85°C (via nvidia-smi)
- **Service monitoring** — detects downed services, sends approval request
- **VPS PM2 monitoring** — auto-restarts crashed processes via SSH
- **Git uncommitted check** — notifies if repos dirty >3 hours
- **All decisions logged** to `~/.jarvis/data/decisions.jsonl`
- **AI-driven** — uses DeepSeek-R1 to choose best action from options

### 6. Voice System

- **Text-to-speech** — edge-tts with British male voice (en-GB-RyanNeural)
- **Local playback** — mpg123 plays audio locally on workstation
- **Telegram audio** — sends MP3 voice messages inline in chat
- **`/voice on|off`** — toggle per-user voice responses in Telegram
- **API endpoints:**
  - `POST /voice/speak` — speak text locally
  - `POST /voice/audio` — return MP3 bytes
  - `POST /voice/notify` — send TTS as Telegram audio message
- **Auto-trimmed** — markdown stripped, max 500 chars for speech

### 7. Self-Healing System

Jarvis monitors and heals itself automatically:

- **`healer.py`** runs every 10 minutes via cron, **independent of the Jarvis API**
- **Service recovery** — detects downed services, attempts `systemctl start`, sends direct Telegram alert bypassing the bot
- **Re-alert** — notifies every 30 min if a service stays down
- **Log rotation** — rotates any log file exceeding 5MB (keeps last 2MB + 2 archives)
- **Interactions archiving** — archives `interactions.jsonl` if it exceeds 50MB
- **Disk space guard** — alerts at >92% disk usage
- **Model integrity** — verifies required Ollama models are present
- **Approval cleanup** — removes expired approval requests automatically
- **`GET /selfcheck`** API endpoint — returns JSON health report
- **`/selfcheck`** Telegram command — formatted health report on demand
- **Heal history** logged to `~/.jarvis/data/heal_history.jsonl`

### 8. Daily Briefings

- **9am daily** — `analyze.py` generates a briefing:
  - Git commits across all projects (last 24h)
  - Shell command count
  - Recent errors from all logs
  - System snapshot (CPU, RAM, disk, uptime)
  - Memory stats (ChromaDB chunk count)
- **Sunday 9am** — weekly summary (last 7 days)
- Sent via Telegram or printed to console

### 9. Mike's Profile Always Loaded

Every prompt includes Mike's full profile:

- Identity, email, role, location
- Full tech stack (Python, JS/TS, React, Node.js, FastAPI, PostgreSQL, MongoDB)
- Hardware specs and VRAM constraints
- Active projects (AsthaCash, Starline-Final-web)
- Infrastructure (VPS IP, Tailscale, PM2)
- Preferences (direct comms, high autonomy, approval gates)
- Stored via `mike_profile.yaml` + `profile.py`

### 10. VPS Integration (OpenClaw)

- **VPS monitoring** — checks PM2 process health on `38.47.35.16` every 5 min
- **Auto PM2 restart** — downed processes restarted automatically via SSH
- **Deploy action** — `deploy_vps` triggers git pull + npm install + pm2 restart
- **Arbitrary SSH** — `ssh_cmd` action (APPROVE tier)
- **Webhook endpoint** — `POST /webhook` receives events from VPS → Jarvis
- **Tailscale VPN** — secure internal network at `100.110.210.103`

### 11. User Facts Memory

- `/remember KEY VALUE` — save explicit facts about Mike
- Auto-detects facts in chat ("my favourite X is Y", "I like X")
- Facts injected into every prompt as context block
- Stored in `user_facts.json` (flat key-value)

### 12. Cursor IDE Tracking

- `cursor_monitor.py` watches Cursor IDE session files for AI conversation data
- Captures `.log`, `.json`, `.jsonl` files from Cursor's config directories
- Converts to text, stores in `~/.jarvis/data/cursor/`
- 5-second debounce per file to avoid flooding
- Initial scan on startup + live watchdog

---

## API Endpoints

`FastAPI running at http://localhost:8181`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Ollama + ChromaDB status + model info |
| `POST` | `/query` | Main query with RAG + smart model routing |
| `GET` | `/sysinfo` | CPU, RAM, GPU, disk, network, Python stats |
| `GET` | `/stats` | Memory chunks + source type breakdown |
| `GET` | `/selfcheck` | Full system health report (JSON) |
| `POST` | `/index` | Trigger indexer.py re-index |
| `GET` | `/models` | List available Ollama models |
| `POST` | `/telegram/send` | Send Telegram message directly |
| `POST` | `/telegram/alert` | Send alert with 🚨 prefix |
| `POST` | `/action` | Execute whitelisted action (permission-gated) |
| `POST` | `/claude-plan` | Claude-style action planning via LLM |
| `POST` | `/feedback` | Rate an interaction good/bad |
| `POST` | `/correct` | Submit a correction for training |
| `POST` | `/learn` | Trigger learner.py brain training |
| `GET` | `/learning-stats` | Rated/corrected interaction counts |
| `POST` | `/approve/{req_id}` | Approve a pending action |
| `POST` | `/deny/{req_id}` | Deny a pending action |
| `POST` | `/webhook` | VPS → Jarvis event webhook |
| `POST` | `/voice/audio` | Generate TTS → MP3 bytes |
| `POST` | `/voice/speak` | Speak TTS locally via mpg123 |
| `POST` | `/voice/notify` | Send TTS as Telegram voice message |

---

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | Full command list |
| `/health` | Service health status |
| `/stats` | Memory + model statistics |
| `/sysinfo` | Live system info (CPU, RAM, GPU, disk) |
| `/selfcheck` | Full system self-check report |
| `/index` | Trigger memory re-indexing |
| `/clear` | Clear conversation history |
| `/remember KEY VALUE` | Save a personal fact |
| `/voice on\|off` | Toggle voice audio responses |
| `/correct TEXT` | Correct last response (trains brain) |
| `/learn` | Run brain training now |
| `/actions` | List all available actions |
| `/pending` | Show pending approval requests |
| `/approve ID` | Approve a pending action |
| `/deny ID` | Deny a pending action |

**Inline:** After every response, tap 👍 or 👎 to train Jarvis.

---

## File Structure

```
~/.jarvis/
├── api.py               # FastAPI REST server (44+ endpoints)
├── telegram_bot.py      # Telegram bot interface
├── jarvis_cli.py        # Interactive terminal REPL
├── indexer.py           # RAG indexing (Claude, git, shell, VPS)
├── learner.py           # Brain self-training from feedback
├── monitor.py           # System health watchdog (every 5 min)
├── decision_engine.py   # AI-powered proactive decisions
├── healer.py            # Autonomous self-healing (every 10 min)
├── executor.py          # Whitelisted action execution
├── permissions.py       # Permission request / approval flow
├── claude_planner.py    # LLM-based action planning
├── profile.py           # Mike's profile → system prompt injection
├── user_facts.py        # Persistent user fact storage
├── voice.py             # TTS via edge-tts + mpg123
├── cursor_monitor.py    # Cursor IDE session capture
├── analyze.py           # Daily/weekly briefing generator
├── selftrain.py         # Weekly full memory re-index
├── train.sh             # 6-hour training cycle script
├── verify.sh            # Full system check (23 items)
│
├── config/
│   ├── jarvis.yaml      # Main configuration
│   ├── mike_profile.yaml # Owner profile (always loaded)
│   └── telegram.json    # Bot token
│
├── memory/              # ChromaDB vector database (~85MB)
├── data/
│   ├── interactions.jsonl   # All Q&A pairs (training data)
│   ├── decisions.jsonl      # AI decision log
│   ├── heal_history.jsonl   # Self-healer action log
│   ├── telegram_history.json # Per-user conversation history
│   ├── pending_approvals.json # Pending action requests
│   └── user_facts.json      # Persisted user facts
│
├── logs/
│   ├── jarvis.log           # API server
│   ├── telegram_bot.log     # Telegram bot
│   ├── monitor.log          # Monitor + decision engine
│   ├── healer.log           # Self-healer
│   ├── indexer.log          # Indexing operations
│   ├── executor.log         # Action execution audit
│   ├── permissions.log      # Permission audit trail
│   └── training.log         # Training / indexing
│
└── docker/
    ├── docker-compose.yml   # API + bot + indexer containers
    ├── Dockerfile
    └── up.sh
```

---

## Cron Schedule

```
PYTHONUNBUFFERED=1

*/5  * * * *   monitor.py          # Health checks + AI decisions + voice alerts
*/10 * * * *   healer.py           # Self-healing + log rotation + service recovery
0    9 * * *   analyze.py          # Daily briefing → Telegram
0    9 * * 0   analyze.py --weekly # Weekly summary → Telegram
0    2 * * 0   selftrain.py        # Full force re-index (Sunday 2am)
0    */6 * * * train.sh            # Index new data + run learner.py
```

---

## Systemd Services

```
jarvis.service           → Jarvis API       (Restart=on-failure, RestartSec=5)
jarvis-telegram.service  → Telegram Bot     (Restart=on-failure, RestartSec=10)
ollama.service           → Ollama LLM       (GPU inference)
```

---

## Hardware Requirements

| Component | Minimum | Mike's Setup |
|-----------|---------|--------------|
| CPU | Any modern multi-core | i9-14900KF (24 cores) |
| RAM | 16GB | 64GB DDR5 |
| GPU | NVIDIA (4GB+ VRAM) | RTX 3050 6GB |
| Storage | 20GB free | SSD |

**VRAM Optimizations (RTX 3050 6GB):**
- `OLLAMA_FLASH_ATTENTION=1` — 40% KV cache reduction
- `OLLAMA_MAX_LOADED_MODELS=1` — one model in VRAM at a time
- `OLLAMA_NUM_CTX=2048` — context cap
- Models ≤ 7B parameters

**Planned upgrade:** NVIDIA Project DIGITS / RTX Spark (~128GB unified memory) for 70B+ models.

---

## Quick Start

```bash
# Start services
sudo systemctl start ollama jarvis jarvis-telegram

# Chat via CLI
jarvis

# Check health
curl http://localhost:8181/health

# Run full system check
~/.jarvis/venv/bin/python3 ~/.jarvis/verify.sh

# Trigger memory re-index
curl -X POST http://localhost:8181/index
```

---

## Active Projects in Memory

Jarvis has full semantic memory of these codebases:

**AsthaCash** — Payment Gateway  
`/home/kali/Projects/kalimike/Payment-Gateway/`  
React admin dashboard + Node.js WebSocket backend · VPS via PM2

**Starline-Final-web** — Real Estate Platform  
`/home/kali/Projects/kalimike/Starline-Final-web/`  
React (conztru) + Express API + PostgreSQL · pnpm monorepo · GitHub Actions CI/CD

---

## Known Constraints

| Issue | Mitigation |
|-------|------------|
| `ollama run` CLI segfaults on RTX 3050 | Use REST API only |
| Bun segfaults in Claude Code | `MALLOC_ARENA_MAX=2` in `~/.zshrc` |
| ChromaDB import fails without env | `PYTHONUNBUFFERED=1` in all cron/service envs |
| No ffmpeg binary | MP3 format throughout (no OGG conversion) |
| 6GB VRAM limit | Max 7B models, one at a time |
| sudo needs terminal | Service restarts via Telegram approval flow |

---

*Built and maintained by Mike Samuel · Powered by DeepSeek-R1, ChromaDB, FastAPI, and Claude Code*
