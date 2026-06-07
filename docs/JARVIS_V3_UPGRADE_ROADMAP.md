# Jarvis v3 Upgrade Roadmap

**Created:** 2026-06-07  
**Author:** Kimi Code CLI  
**Status:** Implementation in progress  
**Baseline:** JARVIS_V3_STATUS.md verified — all components green

---

## Upgrade Philosophy

1. **Never break what works** — py_compile before any restart, test after every change
2. **Verify before proceeding** — each upgrade has a verification checklist
3. **Rollback-ready** — keep backups, document how to revert
4. **Privacy absolute** — no credentials, project names, or IPs leave the machine

---

## Upgrade 1: Complete v2→v3 Systemd Cutover
**Impact:** High | **Effort:** Low | **Risk:** Low

### Why
The systemd service still starts `api_v2.py`. The running v3 API is manual — a reboot kills it.

### What
- Replace `/etc/systemd/system/jarvis.service` with v3 config
- Keep old service file backed up as `jarvis.service.v2`
- Update `jarvis-telegram.service` dependency
- Verify auto-restart on reboot

### Files
- `/etc/systemd/system/jarvis.service` ← `systemd/jarvis-v3.service`
- `/etc/systemd/system/jarvis-telegram.service` ← `systemd/jarvis-telegram-v3.service`

### Steps
1. [x] Stop old api_v2 if running
2. [x] Backup old service files
3. [x] Copy v3 service files
4. [x] `systemctl daemon-reload`
5. [x] `systemctl restart jarvis jarvis-telegram`
6. [x] Verify `curl http://127.0.0.1:8181/health` returns v3
7. [x] Verify Telegram bot responds

**Status: COMPLETE** — Verified 2026-06-07. Services active, api_v3.py PID 45180, telegram_bot.py PID 36408.

### Rollback
```bash
sudo cp /etc/systemd/system/jarvis.service.v2 /etc/systemd/system/jarvis.service
sudo systemctl daemon-reload && sudo systemctl restart jarvis
```

---

## Upgrade 2: Wire jarvis_agent.py + thinking_engine.py into v3
**Impact:** High | **Effort:** High | **Risk:** Medium

### Why
The autonomous agent loop and thinking engine are v2-era islands. They use hardcoded Ollama paths and don't leverage the 7-tier router, orchestrator, or v3 tools.

### What
- Replace agent's direct Ollama calls with `BrainRouter.execute()`
- Route task planning through orchestrator's `TaskDecomposer`
- Use v3 `ToolRegistry.execute()` instead of `executor.run_action()`
- Inject learned rules from `brain_injector.py` into orchestrator prompts

### Files
- `jarvis_agent.py` — agent loop
- `thinking_engine.py` — reasoning engine
- `orchestrator.py` — add agent entry point
- `api_v3.py` — add `/agent` endpoint

### Steps
1. [ ] Create `jarvis_agent_v3.py` wrapper that bridges old agent to v3
2. [ ] Add `BrainRouter` import and route all LLM calls through it
3. [ ] Replace `executor.run_action()` with `ToolRegistry.execute()` via `tools/compat.py`
4. [ ] Add `/agent` endpoint to `api_v3.py`
5. [ ] Test: POST /agent with task "check cpu and memory"
6. [ ] Test: Telegram /do command routes through v3 agent

### Verification
- Agent returns synthesized answer using orchestrator
- Agent respects scope/trust gates for destructive actions
- Agent injects learned rules into prompts

---

## Upgrade 3: Unify Autonomy Under v3 Scope+Trust
**Impact:** High | **Effort:** Medium | **Risk:** Medium

### Why
Two parallel approval systems create security gaps. v2 tiers (AUTO/CONFIRM/APPROVE) and v3 (ScopeEnforcer + TrustRegistry) can disagree.

### What
- Map v2 tiers to v3 ranks: AUTO→R0/R1, CONFIRM→R2, APPROVE→R3+
- Replace `autonomy.should_auto_execute()` with `ScopeEnforcer.check() + TrustRegistry.is_trusted()`
- Maintain backward compat in `tools/compat.py`

### Files
- `autonomy.py` — refactor to delegate to v3 security modules
- `tools/compat.py` — rank mapping
- `telegram_bot.py` — use unified permission flow
- `skillset.py` — sync approval_counts with TrustRegistry

### Steps
1. [ ] Create `autonomy_v3.py` wrapper that maps old API to new security modules
2. [ ] Add rank-to-tier mapping in `tools/compat.py`
3. [ ] Update `telegram_bot.py` to use `PermissionCallbackHandler`
4. [ ] Sync `skillset.json` approval_counts → TrustRegistry
5. [ ] Test: R0 action auto-runs, R2+ action pauses for approval
6. [ ] Test: "Allow & Save" persists trust pattern

### Verification
- v2 action `/action {"action":"disk"}` returns R0, auto-runs
- v2 action `/action {"action":"reboot"}` returns R3+, pauses
- v3 tool `service_restart` with R3 rank pauses for approval
- Telegram button flow works end-to-end

---

## Upgrade 4: Semantic Rule Retrieval for brain_injector.py
**Impact:** Medium | **Effort:** Medium | **Risk:** Low

### Why
Keyword overlap fails when query and rule share no words but are conceptually related. With ChromaDB already deployed, rules should be embedded and retrieved semantically.

### What
- Embed rules into ChromaDB `jarvis_rules` collection
- Use semantic similarity + keyword hybrid scoring
- Keep 5s cache TTL

### Files
- `brain_injector.py` — add semantic retrieval path
- `skillset.py` — add rule embedding on save
- `learner.py` — sync rules to ChromaDB during training

### Steps
1. [ ] Create `jarvis_rules` ChromaDB collection
2. [ ] Add `embed_rules()` function in `skillset.py`
3. [ ] Modify `brain_injector.get_injection()` to use hybrid (semantic + keyword) scoring
4. [ ] Update `learner.py` to re-index rules every 6h cycle
5. [ ] Test: Query "how much space is left" matches disk-related rule even with no word overlap

### Verification
- `get_injection("disk usage")` returns disk-related rules
- `get_injection("how much space is left")` returns same rules via semantic match
- Cache TTL still 5s, no performance regression

---

## Upgrade 5: Self-Healing Worker Pool (Python 3.13 Compat)
**Impact:** Medium | **Effort:** High | **Risk:** Medium

### Why
The nano-swarm worker pool segfaults on Python 3.13 when creating asyncio tasks. The `asyncio.gather` bypass works but loses bot role dispatching (Guard, Scanner, Analyzer).

### What
- Replace `asyncio.create_task()` with `asyncio.to_thread()` for CPU-bound execution
- Or use `multiprocessing.Pool` with `maxtasksperchild=1`
- Or document as known issue and keep gather fallback

### Files
- `nano_swarm/worker_pool.py` — fix task creation
- `orchestrator.py` — restore worker pool dispatch

### Steps
1. [ ] Test `asyncio.to_thread()` in worker loop
2. [ ] Test `multiprocessing.Pool` with isolated processes
3. [ ] If both fail, keep `asyncio.gather` and document limitation
4. [ ] Verify bot dispatching (Guard, Scanner) still works

### Verification
- Orchestrator dispatches 6 security tasks without segfault
- Guard bot blocks R3/LOCAL tools
- Scanner bot runs R0 read-only tools

---

## Test Inventory (Run After Every Upgrade)

```bash
# 1. Security tests
cd ~/.jarvis && venv/bin/python3 -m pytest security/tests/test_security_modules.py -v

# 2. Swarm/builder tests
cd ~/.jarvis && venv/bin/python3 tests/test_v3_swarm_builder.py

# 3. API health
curl -s http://127.0.0.1:8181/health | python3 -m json.tool

# 4. Orchestrator end-to-end
curl -s -X POST http://127.0.0.1:8181/v3/orchestrate \
  -H "x-api-key: $JARVIS_API_KEY" \
  -d '{"request":"check cpu and memory","allow_destructive":false}'

# 5. Action compat
curl -s -X POST http://127.0.0.1:8181/action \
  -H "x-api-key: $JARVIS_API_KEY" \
  -d '{"action":"cpu"}'
```

---

## Execution Order

1. **Upgrade 1** (systemd) — unblock reboot resilience
2. **Upgrade 3** (unify autonomy) — security hardening before wiring agent
3. **Upgrade 2** (wire agent) — intelligence layer
4. **Upgrade 4** (semantic rules) — quality of responses
5. **Upgrade 5** (worker pool) — parallelism restoration

---

*Next: Start Upgrade 1*
