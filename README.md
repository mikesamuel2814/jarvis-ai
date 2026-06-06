# Jarvis — Autonomous Personal AI Brain

> **"Jarvis is the brain. Claude is every neuron."**

Jarvis is a fully autonomous personal AI assistant running locally on a Kali Linux
workstation. You give it a task in plain English — it **plans, decides, runs the
safe actions itself, chains the next step, and reports back** — all on consumer
hardware, with a local LLM brain, semantic memory, continuous self-learning, and
human-approval gates on anything destructive.

**Telegram:** [@MikePiJarvisBot](https://t.me/MikePiJarvisBot) · **API:** `http://127.0.0.1:8181` · **Repos:** [GitHub](https://github.com/mikesamuel2814/jarvis-ai) · [GitLab](https://gitlab.com/programmerhimel/jarvis)

---

## Owner

**Mike Samuel** — Full-Stack Developer & Entrepreneur
Machine: Kali Linux · i9-14900KF (24 cores) · 64GB RAM · RTX 3050 6GB VRAM
Telegram Bot: [@MikePiJarvisBot](https://t.me/MikePiJarvisBot)

---

## What's New (June 2026) — The Autonomous Brain

Jarvis evolved from a chat-and-actions assistant into a genuine autonomous agent.
Five new intelligence layers now sit on top of the raw model calls:

| Layer | Module | What it does |
|-------|--------|--------------|
| 🤖 **Agent Loop** | `jarvis_agent.py` | Plain-English task → plan → act → observe → answer (ReAct, structured outputs) |
| 🧠 **Thinking Engine** | `thinking_engine.py` | Intent classification, response scoring, goal tracking, proactive advisor, daily briefings, nightly self-reflection |
| 📜 **Skillset Engine** | `skillset.py` | Learned rules, autonomy map, approval tracking, skill gaps |
| 💉 **Brain Injector** | `brain_injector.py` | Injects learned rules into every prompt (5s cache, zero query overhead) |
| 🔓 **Autonomy Engine** | `autonomy.py` | Decides what runs without asking; promotes actions to "learned-safe" after 3 approvals |
| 🔍 **Web Search Trainer** | `web_search_trainer.py` | Silently searches the web when uncertain, extracts lessons, trains itself |

---

## The Agent Loop — How Jarvis Actually Does Tasks

```
You: "/do are both my projects healthy?"
        │
        ▼
  ┌──────────────────────────────────────────────────────────┐
  │  jarvis_agent.run_agent()                                 │
  │                                                          │
  │   1. PLAN   qwen2.5-coder:7b  (structured JSON output)   │
  │             picks next action from a relevance-filtered   │
  │             catalog of your whitelisted actions           │
  │                                                          │
  │   2. ACT    autonomy gate decides:                       │
  │               • read-only      → run now                 │
  │               • pre-approved   → run now                 │
  │               • destructive    → PAUSE, ask via Telegram │
  │                                                          │
  │   3. OBSERVE feed the action's output back into context  │
  │                                                          │
  │   4. LOOP   until done or max_steps (default 6)          │
  │                                                          │
  │   5. ANSWER synthesise a clean "Sir, …" reply           │
  └──────────────────────────────────────────────────────────┘
        │
        ▼
Jarvis: "Sir, ran vps_ps + vps_disk — both projects are healthy."
```

**Why structured outputs, not native function-calling?** Ollama's native `tools`
API **segfaults llama-server on the RTX 3050**. The JSON-schema `format` path is
rock-solid. The agent uses it exclusively.

**Why a relevance-filtered catalog?** Listing all 69 actions in the prompt also
crashes the 7B on 6GB VRAM. Jarvis ranks actions by verb-weighted keyword match
to the task and shows only the top ~10 — which keeps the model **stable and more
accurate** (fewer distractors). If the 7B ever crashes, it falls back to the 3B.

**Entry points:** `POST /agent` · Telegram `/do <task>`

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                        JARVIS BRAIN                             │
│                                                                │
│  Telegram Bot ──┐                                              │
│  CLI / API ─────┼──► FastAPI (api_v2.py :8181, X-API-Key)      │
│                 │            │                                  │
│                 │            ├──► Agent Loop (jarvis_agent.py)  │
│                 │            ├──► Brain Router (brain.py)       │
│                 │            │      EDGE / CURSOR / HYBRID/CLOUD│
│                 │            │            │                     │
│                 │       Thinking Engine   ├──► Ollama LLMs      │
│                 │       + Skillset        ├──► Claude (cloud)   │
│                 │       + Web Trainer     │                     │
│                 │            │            ▼                     │
│                 │       ChromaDB Semantic Memory (~10.7k chunks)│
│                 │                                               │
│  Voice (TTS) ◄── Decision Engine ◄── Monitor (every 5 min)     │
│  Self-Healer (every 10 min) ──────► Self-Recovery              │
└────────────────────────────────────────────────────────────────┘
```

| Component        | Detail                                               |
|------------------|------------------------------------------------------|
| API server       | `api_v2.py` — FastAPI, binds `127.0.0.1:8181`, X-API-Key auth |
| Brain router     | `brain.py` — 4-tier (Edge / Cursor / Hybrid / Cloud) |
| Agent planner    | `qwen2.5-coder:7b` (→ 3b fallback) via structured outputs |
| Reasoning brain  | DeepSeek-R1 7B via Ollama (GPU, port 11434)         |
| Memory           | ChromaDB at `~/.jarvis/memory/` (~10,700 chunks)    |
| Embeddings       | mxbai-embed-large (1024-dim)                         |
| Telegram         | @MikePiJarvisBot (python-telegram-bot)              |
| Voice            | edge-tts (en-GB-RyanNeural) → MP3 via mpg123        |
| Self-Healer      | `healer.py` — every 10 min via cron                 |

---

## Model Routing (brain.py — 4 Tiers)

Jarvis picks the cheapest capable tier for each query:

| Tier | Model | When |
|------|-------|------|
| **EDGE** | `phi4-mini` / `qwen2.5-coder:7b` / `deepseek-r1:7b` | Casual chat, sysinfo, trivial code lookups — local, instant, free |
| **CURSOR** | Cursor + Claude (falls back to Cloud) | Concrete code/ops on Mike's projects |
| **HYBRID** | Local pre-analysis → Claude | Ambiguous prose needing reasoning |
| **CLOUD** | Claude Sonnet 4.6 | Open-ended why/how/analyze/architecture |

Every tier gets **learned rules** (brain_injector) and **thinking context**
(goals + session state + intent directive) injected automatically.

---

## Core Intelligence Features

### 🧠 Thinking Engine (`thinking_engine.py`)

- **Intent classification** — decision / urgent_fix / project_question / planning → tailored directive injected into the prompt
- **Response scoring (0–10)** — completeness, persona ("Sir,"), uncertainty, actionability, filler detection
- **Goal tracking** — keeps Mike's active goals (AsthaCash stability, Starline delivery, Jarvis development, hardware upgrade) front-of-mind in every response
- **Proactive advisor** — surfaces disk/RAM/VRAM pressure and goal reminders every 5 min; priority ≥8 alerts go straight to Telegram
- **Daily briefing** — `/briefing`: system health + services + goals + 24h activity
- **Nightly self-reflection** — scores the day's responses, auto-adds improvement rules when quality drops

### 📜 Skillset + Autonomy (`skillset.py`, `autonomy.py`)

- **Learned rules** injected into every prompt, ranked by relevance + priority + hit count
- **Autonomy map:**
  - `pre_approved` — auto-execute (disk, memory, gpu, pm2_status, nginx_status, restart_gateway, restart_starline)
  - `always_ask` — NEVER auto-run (reboot, update_system, deploy_vps, shell, ssh_cmd)
  - `learned_safe` — promoted automatically after 3 human approvals
- **Privacy-absolute** — AsthaCash, Starline, SSH keys, credentials, VPS IPs never leave the machine

### 🔍 Web Search Trainer (`web_search_trainer.py`)

- Detects uncertainty in Jarvis's own answers ("I don't know", outdated, <80 chars)
- Silently searches the web (DuckDuckGo, non-blocking daemon thread), extracts 1–3 lessons via DeepSeek
- Stores lessons in ChromaDB + skillset → available on the **next** query
- Rate-limited (3 searches / 10 min), privacy-guarded, enriches skill gaps during the 6h training cycle

### 💬 Continuous Brain Training (Learning Loop)

- **👍 / 👎** on every Telegram response — rate instantly
- **`/correct TEXT`** — correction → DeepSeek extracts a rule → injected into the very next prompt (~100ms, no retrain wait)
- **Automatic training every 6 hours** (`train.sh` → indexer + learner)
- 👍 → `golden` examples · 👎 → `lesson` rules · corrections → highest priority
- **Weekly full re-index** every Sunday 2am

---

## Action Execution (69 Whitelisted Actions, Tiered)

| Tier | Confirmation | Examples |
|------|--------------|---------|
| `AUTO` (39) | None — instant | disk, memory, gpu, logs, pm2_status, nginx_status, git status |
| `CONFIRM` (23) | One-tap Telegram button | restart_jarvis, restart_gateway, git_pull, npm/pnpm build |
| `APPROVE` (7) | Explicit approval | deploy_vps, ssh_cmd, shell, file_write, reboot, update_system |

The agent and the autonomy engine both respect these tiers — read-only runs free,
pre-approved/learned-safe auto-runs, everything else pauses for a one-tap approval.

---

## API Endpoints (api_v2.py — `http://127.0.0.1:8181`, X-API-Key required)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/agent` | **Autonomous agent loop** — plain-English task → plan, act, answer |
| `POST` | `/query` | Main query — 4-tier brain routing + RAG + thinking context |
| `GET`  | `/health` | Ollama + ChromaDB status |
| `GET`  | `/sysinfo` | CPU, RAM, GPU, disk, uptime |
| `GET`  | `/metrics` | Full metrics dashboard |
| `GET`  | `/learning-stats` | Learning queue / golden / lessons counts |
| `POST` | `/action` | Execute whitelisted action (autonomy + permission gated) |
| `POST` | `/feedback` | Rate an interaction 👍/👎 |
| `POST` | `/correct` | Submit a correction (trains brain immediately) |
| `POST` | `/memory/save` | Save a chunk directly to ChromaDB |
| `POST` | `/memory/sync` | Bidirectional ChromaDB ↔ OpenClaw sync |
| `POST` | `/voice/notify` | Send TTS as a Telegram voice message |
| `POST` | `/webhook/openclaw` | VPS → Jarvis events (HMAC-signed) |

---

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/do TASK` | 🤖 **Autonomous agent** — plans, runs safe actions, reports back |
| `/think Q` | Deep multi-step reasoning + confidence score |
| `/briefing` | Daily intelligence briefing (health + projects + goals + stats) |
| `/objectives` | Owner profile, projects & Jarvis goals |
| `/exec CMD` | Smart dispatch (detect action or plan + execute) |
| `/task DESC` | Delegate to Claude Code (async, result returned here) |
| `/web Q` `/search Q` | Real-time web search + AI answer |
| `/weather [city]` | Current weather |
| `/browse URL` | Open URL in headless browser |
| `/kali [tool target]` | Pentest tools (L1–L4, scope-guarded) |
| `/scans` | Recent scan reports |
| `/oc [tool]` | OpenClaw gateway status / tool invocation |
| `/actions` `/pending` `/approve ID` `/deny ID` | Action & approval management |
| `/correct TEXT` `/learn` `/index` | Learning & memory |
| `/recall [topic]` `/remember K V` | Memory recall / save a fact |
| `/stats` `/sysinfo` `/health` `/selfcheck` `/probe` `/skills` | Status & diagnostics |
| `/voice on\|off` | Toggle voice responses |

**Inline:** tap 👍 / 👎 after any response to train Jarvis.

---

## Proactive & Self-Healing Systems

- **Decision Engine** (every 5 min) — disk/RAM/CPU/GPU alerts, downed-service detection, VPS PM2 monitoring, git-uncommitted nudges, **autonomy-aware auto-restart** of pre-approved services, plus the Thinking Engine's proactive advisor
- **Self-Healer** (`healer.py`, every 10 min, independent of the API) — service recovery, log rotation (>5MB), interactions archiving (>50MB), disk guard, model-integrity check, expired-approval cleanup
- **Voice** — edge-tts British male; local mpg123 playback + Telegram audio

---

## File Structure

```
~/.jarvis/
├── api_v2.py            # FastAPI server (RUNNING — secure, X-API-Key)
├── brain.py            # 4-tier brain router (Edge/Cursor/Hybrid/Cloud)
├── jarvis_agent.py     # 🤖 Autonomous agent loop (ReAct, structured outputs)
├── thinking_engine.py  # 🧠 Reasoning, scoring, goals, briefings, reflection
├── skillset.py         # 📜 Rules + autonomy map + skill gaps
├── brain_injector.py   # 💉 Live rule injection into prompts
├── autonomy.py         # 🔓 Auto-execution decisions
├── web_search_trainer.py # 🔍 Silent web search + self-training
├── executor.py         # Whitelisted action execution (69 actions, tiered)
├── telegram_bot.py     # Telegram interface (/do, /think, /briefing, …)
├── decision_engine.py  # Proactive 5-min checks + alerts
├── learner.py          # 6h training cycle + nightly reflection
├── indexer.py          # RAG indexing (Claude, git, shell, VPS)
├── healer.py           # Autonomous self-healing
├── claude_client.py    # Cloud tier (Claude Sonnet)
├── kali_tools.py       # Scoped pentest tooling
├── voice.py            # TTS via edge-tts + mpg123
├── profile.py / user_facts.py  # Owner profile + fact memory
│
├── config/
│   ├── jarvis.yaml / jarvis_v2.yaml   # Configuration
│   ├── mike_profile.yaml              # Owner profile (always loaded)
│   └── secrets.env                    # API keys (gitignored)
│
├── data/
│   ├── skillset.json                  # Learned rules + autonomy map
│   ├── thinking/goals.json            # Active goals
│   ├── thinking/context_state.json    # Rolling session state
│   ├── agent_runs.jsonl               # Agent task history
│   ├── interactions.jsonl             # All Q&A (training data)
│   └── …                              # (gitignored runtime data)
│
├── memory/             # ChromaDB vector DB (gitignored)
├── logs/               # All logs (gitignored)
└── docker/             # Container stack
```

---

## Cron Schedule

```
*/5  * * * *   monitor.py + decision_engine.py   # Health + AI decisions + proactive advisor
*/10 * * * *   healer.py                          # Self-healing + log rotation
0    9 * * *   analyze.py                          # Daily briefing → Telegram
0    9 * * 0   analyze.py --weekly                 # Weekly summary
0    2 * * 0   selftrain.py                        # Full re-index (Sunday 2am)
0  */6 * * *   train.sh                            # Index + learner + web enrichment + reflection
```

---

## Hardware & Optimizations

| Component | Mike's Setup |
|-----------|--------------|
| CPU | i9-14900KF (24 cores) |
| RAM | 64GB DDR5 |
| GPU | RTX 3050 6GB VRAM |

**VRAM (6GB) constraints baked into the design:**
- Models ≤ 7B, `num_ctx ≤ 2048`, one model loaded at a time
- Agent planner prompt kept small (≤10 actions) — full catalog segfaults the 7B
- Native Ollama `tools` API avoided — it crashes llama-server; structured outputs used instead
- 3B fallback planner for guaranteed stability

**Planned upgrade:** NVIDIA Project DIGITS / RTX Spark (~128GB unified) for 70B+ models.

---

## Quick Start

```bash
# Start services
sudo systemctl start ollama jarvis jarvis-telegram

# Health
curl -s http://127.0.0.1:8181/health

# Autonomous task (via Telegram): /do give me a system health check
# Or via API:
curl -s -X POST http://127.0.0.1:8181/agent \
  -H "X-API-Key: $JARVIS_API_KEY" -H "Content-Type: application/json" \
  -d '{"task":"are both my projects healthy?","max_steps":6}'
```

---

## Active Projects in Memory

**AsthaCash** — Payment Gateway
`/home/kali/Projects/kalimike/Payment-Gateway/`
React admin dashboard + Node.js WebSocket backend · VPS via PM2

**Starline-Final-web** — Real Estate Platform (conztru)
`/home/kali/Projects/kalimike/Starline-Final-web/`
React + Express + PostgreSQL · pnpm monorepo · GitHub Actions CI/CD

---

## Known Constraints

| Issue | Mitigation |
|-------|------------|
| Ollama native `tools` API segfaults RTX 3050 | Use structured outputs (`format` schema) only |
| Full action catalog crashes 7B on 6GB VRAM | Relevance-filter to ≤10 actions per agent step |
| `ollama run` CLI segfaults | Use REST API only |
| ChromaDB import flaps | `PYTHONUNBUFFERED=1` + lazy imports in cron/service |
| No ffmpeg binary | MP3 throughout (mpg123) |
| sudo needs terminal | Service restarts via Telegram approval flow |

---

*Built and maintained by Mike Samuel · Powered by Ollama (DeepSeek-R1, Qwen2.5-Coder), ChromaDB, FastAPI, and Claude · "Jarvis is the brain. Claude is every neuron."*
