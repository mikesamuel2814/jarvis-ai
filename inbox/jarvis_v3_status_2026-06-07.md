# Jarvis v3 — Status & Kimi Handoff

**Last verified:** 2026-06-07 · **API:** `3.0.0` · **Services:** `jarvis`, `jarvis-telegram` active
**Audience:** the Kimi Code CLI (and any coding agent) picking up Jarvis to enhance its brain, abilities, and smartness.

This document records what is **built and tested working** so you can build on a known-good baseline instead of re-deriving it. Anything not listed under "Done" should be treated as unverified.

---

## 1. Done & Tested Working ✅

### Brain Router (7-tier) — `brain_router.py`, `models/clients.py`
- Routes any query to one of: **NANO** (phi4-mini, CPU) · **EDGE** (qwen2.5-coder:7b, GPU) · **CURSOR** · **HYBRID** · **KIMI** (Kimi For Coding) · **CLOUD** (Claude) · **SWARM** (consensus).
- 9-stage classifier (prefix overrides → security → build → code → reasoning → sysinfo → nano → embedding fallback → HYBRID default). **All 7 tiers verified routing correctly.**
- `ModelDispatcher` has **runtime failover**: a mid-call exception on one tier silently falls over to the next available tier. Verified live (NANO→EDGE when phi4-mini's llama-server aborts on the RTX 3050).
- Live endpoints tested: `/v3/route`, `/v3/ask` (NANO, EDGE, KIMI all answered).

### KIMI tier = **Kimi For Coding** — `kimi/client.py`
- Base URL `https://api.kimi.com/coding/v1` · model `kimi-for-coding` · key in `MOONSHOT_API_KEY` (sk-kimi-… form).
- **Access is gated to approved coding agents via `User-Agent`.** Jarvis sends `claude-code/1.0` (honest — it runs through Claude Code). Verified: real completions return, dispatcher resolves to `kimi` with no fallback.
- All three values env-overridable: `KIMI_BASE_URL` / `KIMI_MODEL_ID` / `KIMI_USER_AGENT`. Key lookup: `KIMI_API_KEY` → `MOONSHOT_API_KEY`.
- ⚠️ Cosmetic only: `/v3/ask` still labels responses `model: kimi-k2.6` (stale tier label) — intentionally left as-is.

### Orchestrator pipeline — `orchestrator.py`
6-step: **Intent → Decompose → Scope+Trust gate → Dispatch → Synthesize.** Tested end-to-end:
- Read-only request ("cpu, ram, disk") → 3/3 tasks, ~3.4s, LLM-synthesized answer. ✓
- **Unified scope+trust gate:** an action auto-runs only if within session scope AND rank R0/R1. Anything over-scope OR needing trust routes to a **Telegram approval pause** (not a silent block). The pause never executes — only Sir's Allow press does.
- Service action ("restart the nginx service") → pauses, emits `[✅ Allow] [💾 Allow & Save] [❌ Deny]`. ✓

### Nano-bot swarm — `nano_swarm/bots/`
7 bots (Scanner/Verifier/Fetcher/Analyzer/Builder/Test/**Guard**). Guard gate (scope→rank→trust) verified blocking R3/LOCAL tools; analyzer risk scoring; test-bot code validation. ✓

### Progressive-trust permission UI — `telegram_ui/callbacks.py`, `telegram_bot.py`, `api_v3.py`
- `/orchestrate <task>` Telegram command renders permission buttons.
- **Cross-process flow verified:** `/v3/orchestrate` registers a pending permission on the orchestrator singleton → button press POSTs `/v3/permission/{id}/{action}` → resolved by the **same** singleton. Deny + re-press-expiry both correct. "Allow & Save" persists a trust pattern.

### Dynamic tool system — `tools/`, `tool_builder/`
- **127 tools** registered (13 categories), all metadata valid. `load_all_tools()` + dynamic-tool loader.
- `ToolBuilder` 5-phase pipeline (analyze→generate→security→test→deploy). Good tools deploy; malicious tools blocked at the security phase. ✓
- `CodeGenerator` is **provider-independent**: Kimi → local qwen2.5-coder → local deepseek → self-synthesis (maps intent→shell, self-verifies). Falls through to synth when no LLM available. ✓
- `SecurityBot` (regex secret scan + AST injection/path-traversal) gates every build.

### Notifications — `notifier.py`
- 5 levels (GENERAL/NORMAL/MEDIUM/HIGH/CRITICAL); **HIGH-only delivery policy** (threshold = HIGH). Dedup window + `JARVIS_NOTIFY_DISABLED=1` kill-switch. Autonomous-event helpers (bot_request/remediation/decision) classified HIGH so they reach Sir. ✓

### Learning loop — `learner.py`
- 6h cycle ingests interactions/golden/corrections/lessons → ChromaDB. VRAM-compliant (deepseek-r1:7b, num_ctx≤1024, keep_alive=0, no native `tools` param).
- **NEW: routing-accuracy feedback** (`analyze_routing_feedback`) — correlates v3 tier choices with good/bad ratings + response length, flags high-failure tiers / under-routing / low-confidence guesses, writes insights to `decision_state.json` + a memory lesson. Surfaced in `learner.py --stats`. ✓

### Guardian / security monitors
- `guardian/auto_remedy.py` (P0/P1 known-safe auto-remediation through the autonomy gate), `guardian/alerter.py` (→ notifier). Security modules suite **13/13**. Live `/v3/guardian/check` returns alerts.

### API endpoints verified live
`/health` · `/v3/tools` · `/v3/tools/search` · `/v3/tools/{name}` · `/v3/route` · `/v3/ask` · `/v3/orchestrate` · `/v3/permission/{id}/{action}` · `/v3/swarm/status` · `/v3/guardian/check`.

### Test inventory (all green)
| Suite | Result |
|---|---|
| `tests/test_v3_swarm_builder.py` | 39 PASS |
| `security/tests/test_security_modules.py` | 13 PASS (needs `pytest` in venv) |
| `tools/tests/test_all_tools.py` / `test_tools_simple.py` | all pass (127 tools) |

---

## 2. Hard Constraints (do NOT violate)

- **RTX 3050 = 6GB VRAM.** Never use a model >7B. `num_ctx ≤ 2048`. One GPU model at a time.
- **Never use Ollama's native `tools=` param** — it segfaults llama-server on this GPU. Use the `format` JSON-schema path.
- `ollama run` CLI segfaults — **REST API only** (port 11434).
- **Privacy:** never send AsthaCash / Starline / `.ssh` / credentials / `.env` / secrets / VPS IPs / `admin93` to any external service (Kimi/Claude/web).
- **Root / privileged / `always_ask` actions** (`reboot`, `update_system`, `deploy_vps`, `shell`, `ssh_cmd`) stay **Telegram-APPROVE gated**. Never arm unattended autonomous root.
- **Always `python3 -m py_compile` before any service restart.** Never break `jarvis` / `jarvis-telegram`.
- Service restarts: `sudo -n systemctl restart jarvis`.
- Known env flake: standalone `tools.load_all_tools()` intermittently SIGSEGVs (chromadb/onnxruntime native) — does **not** affect the long-lived API service.

---

## 3. Suggested Runway for Kimi (enhance brain / ability / smartness)

Ordered by leverage. Each should ship with a test in the relevant suite and respect §2.

1. **Close the routing-feedback loop into action.** `analyze_routing_feedback` currently *reports* mis-routes; make it *adjust* — auto-tune `brain_router.py` patterns or seed new intent examples into the `jarvis_routing_intents` ChromaDB collection from the insights.
2. **Richer decomposition.** `thinking/decomposer.py` is rule-based. Add an LLM-backed decomposition path (route through KIMI/EDGE) for requests that don't match the hand-written rules, with the rule-based path as fast fallback.
3. **Multi-step ReAct in the orchestrator.** Today subtasks are dispatched once. Add observe→re-plan so a failed/empty tool result can trigger an alternative tool or a follow-up subtask.
4. **Tool-builder autonomy.** Let Jarvis detect a capability gap during orchestration (no tool matched) and auto-invoke `ToolBuilder` to synthesize + security-review + deploy a new tool, then retry — all behind the Guard/approval gate.
5. **SWARM consensus quality.** Wire `SwarmClient` to run EDGE+CLOUD+KIMI and reconcile, with a judge step, for high-stakes security/reasoning queries.
6. **Self-reflection → router.** Feed nightly `thinking_engine` reflection scores back into per-tier confidence so weak tiers get down-weighted automatically.

**Start here:** read this file, then `docs/V3_BUILD_REPORT.md` and `CLAUDE.md`. Run the three test suites to confirm the baseline is green on your machine state before changing anything.
