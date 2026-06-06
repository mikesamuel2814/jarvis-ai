# Jarvis Autonomy + Auto Web Search System

**Status:** ✅ Fully implemented, tested, and running (June 6, 2026)

This document describes Jarvis's autonomous decision-making and continuous self-training system.
Claude MUST follow all rules in this file when working on Jarvis code.

---

## RULE SET — Claude Code Sessions

### Auto Web Search Rules (for Claude)
1. **Never disable or remove `web_search_trainer.py` integration** — it is the primary path by which Jarvis grows its knowledge base.
2. **Privacy is absolute**: Never pass AsthaCash, Starline, .ssh, credentials, .env, secrets, private_key, admin93, VPS IPs through external search queries.
3. **All web searches are background only** — `silent_search_and_train()` is non-blocking. Never call `force_search_and_train()` in the query path.
4. **Rate limit is 3 searches per 10 min** — never bypass `_is_rate_limited()`.

### Autonomy Rules (for Claude)
1. **Never shorten the `always_ask` list** in skillset.json: `reboot`, `update_system`, `deploy_vps`, `shell`, `ssh_cmd` — these ALWAYS require explicit approval.
2. **`pre_approved` actions** auto-execute without any user confirmation — only add safe, read-only, or service-restart actions.
3. **`learned_safe` promotion** requires exactly 3 human approvals — never manually set it.
4. **Always syntax-check Python files before restart** — use `python3 -m py_compile <file>`.

### Brain/Learning Rules (for Claude)
1. **Injection must be fast** — `brain_injector.get_injection()` uses 5s cache. Never block on external calls in this path.
2. **All learning is non-blocking** — use daemon threads for ChromaDB upserts and rule extraction.
3. **DeepSeek-R1:7b is the default local model** for rule extraction — never use a model >7B (6GB VRAM limit).
4. **Cursor tier falls back to Cloud** when `cursor.query` module is unavailable — this is expected behavior.

---

## RULE SET — Jarvis Brain (injected into every prompt)

Jarvis follows these standing rules at runtime (injected via `brain_injector.py`):

1. **Auto web search**: When Jarvis lacks information (expresses uncertainty, says "I don't know", gives <80-char response), `web_search_trainer.py` silently searches the web and stores lessons in memory for the NEXT query.
2. **Autonomy**: Pre-approved actions (disk, memory, gpu, nginx_status, pm2_status, restart_gateway, restart_starline) execute automatically without asking. `reboot`, `update_system`, `deploy_vps` ALWAYS require explicit approval.
3. **Learning**: Every correction (/correct) immediately extracts a rule and injects it into the next prompt. Skill gaps identified during training are enriched via web search.
4. **Privacy**: Never send Payment-Gateway, AsthaCash, Starline, SSH keys, or credentials to external services.
5. **Thinking Engine**: Every query gets intent-classified and thinking context injected (goals + session state + directive). Complex queries (CLOUD/HYBRID tier) get full multi-step reasoning context.
6. **Response quality**: Every response scored 0-10. Score <4 triggers web search. Missing "Sir," is flagged. Nightly reflection auto-adds improvement rules.
7. **Proactive decisions**: Jarvis surfaces system alerts and goal reminders every 5min via decision_engine.py — never silent about critical issues.

---

## Jarvis Agent — Autonomous Action Loop (jarvis_agent.py)

**The modern agentic core.** Plain-English task → plan → act → observe → answer.
Replaces fragmented heuristics with one clean ReAct loop using Ollama structured outputs.

| Aspect | Detail |
|---|---|
| Planner model | `qwen2.5-coder:7b` (accurate). Falls back to `qwen2.5-coder:3b` on crash. |
| Why structured outputs (`format` schema) not native `tools` | Native `tools` API **segfaults** llama-server on the RTX 3050. The `format` JSON-schema path is stable. |
| Why catalog is relevance-filtered to 10 actions | Full 69-action prompt **segfaults** the 7b on 6GB VRAM. Small prompt = stable + accurate (fewer distractors). |
| Action selection | Verb-weighted keyword scoring (restart/pull/build/deploy weighted highest), best-match listed first. |
| Safety | Reuses `executor.ACTIONS` (whitelisted+tiered) + `autonomy.should_auto_execute()`. Read-only runs free; pre-approved/learned-safe auto-run; everything else PAUSES for Telegram approval. |
| Entry points | `POST /agent` (api_v2.py) · `/do <task>` (Telegram) |
| Function | `run_agent(task, max_steps=6, allow_destructive=False) -> {answer, steps, actions_run, needs_approval, elapsed}` |

**CRITICAL rules for Claude when touching jarvis_agent.py:**
1. NEVER switch to the native Ollama `tools` parameter — it crashes the GPU. Use `format` schema only.
2. NEVER list all actions in the prompt — keep `_MAX_CATALOG` small (≤12) or the 7b segfaults.
3. The autonomy gate is the safety boundary — never let the agent bypass `should_auto_execute()` for state-changing actions.

## Core Module Map

| File | Purpose |
|---|---|
| `thinking_engine.py` | Core intelligence: reasoning, goals, scoring, briefings, self-reflection |
| `skillset.py` | Rules + autonomy map + skill gaps |
| `brain_injector.py` | Prompt injection (5s cache) |
| `autonomy.py` | Auto-execution decisions |
| `web_search_trainer.py` | Silent web search when uncertain |
| `brain.py` | 4-tier query router (EDGE/CURSOR/HYBRID/CLOUD) |
| `api.py` | FastAPI server — all endpoints |
| `decision_engine.py` | Proactive 5-min checks + alerts |
| `learner.py` | 6h training cycle + self-reflection |
| `telegram_bot.py` | All Telegram commands |

---

## Overview

Jarvis is designed for **autonomous operation with human oversight**. Every learned rule, approval history, and skill gap feeds a continuous learning loop that makes Jarvis increasingly self-sufficient.

```
User Feedback (correction, 👍, 👎)
        ↓
[rapid_learner.py] Store in JSON + ChromaDB
        ↓
[skillset.py] Extract rules, track approvals
        ↓
[brain_injector.py] Inject rules into every prompt (query-time)
        ↓
[autonomy.py] Decide: auto-execute or ask? (action-time)
        ↓
[Model responds + executes with learned context]
        ↓
Loop continues — each interaction improves behavior
```

## Core Files

### 1. `skillset.py` — Rule & Autonomy Database
**Single source of truth for Jarvis's learned behaviors.**

Persists to: `~/.jarvis/data/skillset.json`

Schema:
```python
{
  "version": 1,
  "rules": [
    {
      "id": "uuid",
      "rule": "When asked X, do Y",
      "priority": 1-10,      # Higher = always injected
      "source": "correction|lesson|pattern",
      "hits": 5,             # Times used
      "ts": "ISO datetime"
    }
  ],
  "skills": {
    "action_name": {
      "confidence": 0.0-1.0,
      "usage": int,
      "success": int,
      "desc": str
    }
  },
  "autonomy": {
    "pre_approved": ["action1", ...],       # Always auto-execute
    "always_ask": ["action1", ...],         # Never auto-execute
    "learned_safe": ["action1", ...],       # Auto after 3 approvals
    "approval_counts": {"action": count}
  },
  "patterns": {
    "after_deploy": ["action1", "action2"],
    "sequences": [["action1", "action2"], ...]
  },
  "skill_gaps": [
    {
      "topic": "docker",
      "fail_count": 3,
      "enrichment": "Docker best practices..."
    }
  ]
}
```

**Key functions:**
- `load()` → dict
- `save(data)` → bool
- `add_rule(text, priority, source)` → bool
- `get_prompt_injection(query, n=5)` → str
- `record_approval(action)` → None (promotes to learned_safe at 3)
- `get_autonomy_decision(action)` → (bool, str)
- `extract_and_store_rule_from_correction(query, correction)` → None

### 2. `brain_injector.py` — Live Rule Injection
**Injects top learned rules into every system prompt at query-time.**

Uses:
- Semantic matching (query words vs. rule keywords)
- Priority ranking (higher priority rules win)
- Hit counting (frequently-used rules ranked higher)
- In-memory cache (5s TTL, prevents blocking)

Output format injected into system prompt:
```
--- JARVIS LEARNED RULES (apply these) ---
• When Mike asks about X, always show Y [priority:8]
• Never mention Z when Mike says W [priority:9]
--- END LEARNED RULES ---

--- SKILL CONTEXT (Docker) ---
[Enrichment text for weak topics]
--- END SKILL CONTEXT ---
```

**Key function:**
- `get_injection(query, history, n_rules=5)` → str

### 3. `autonomy.py` — Autonomous Execution Engine
**Decides when Jarvis can execute actions without asking first.**

Decision tree:
1. **Always-ask actions** (destructive) → NEVER auto-execute (reboot, shell, deploy_vps)
2. **Pre-approved actions** (safe) → auto-execute immediately (restart_gateway, sysinfo)
3. **Learned-safe actions** → auto-execute after 3 human approvals

**Key functions:**
- `should_auto_execute(action, context)` → (bool, reason)
- `record_approval(action)` → None
- `suggest_next_action(last_action, outputs)` → str | None
- `log_execution_outcome(action, success, auto)` → None

## Integration Points

### In `api.py` — build_system_prompt()
```python
def build_system_prompt(ctx_block: str = "", query: str = "") -> str:
    # ... existing blocks ...
    
    # NEW: Inject learned rules
    from brain_injector import get_injection
    skill_injection = get_injection(query)
    if skill_injection:
        prompt += f"\n\n{skill_injection}"
    
    return prompt
```

### In `brain.py` — execute()
```python
def execute(query: str, rag_context: str, history: list = None, ...):
    # NEW: Prepend learned rules to RAG context
    from brain_injector import get_injection
    skill_injection = get_injection(query, history=history)
    if skill_injection and rag_context:
        rag_context = skill_injection + "\n\n" + rag_context
    elif skill_injection:
        rag_context = skill_injection
    
    # ... existing routing code ...
```

### In `learning/rapid_learner.py` — _store_correction()
```python
def _store_correction(interaction: dict, correction: str):
    # ... store to corrections.jsonl and ChromaDB ...
    
    # NEW: Extract rule immediately (non-blocking thread)
    from skillset import extract_and_store_rule_from_correction
    import threading
    threading.Thread(
        target=extract_and_store_rule_from_correction,
        args=(query, correction),
        daemon=True
    ).start()
```

### In `/action` API endpoint
```python
@app.post("/action")
def execute_action(req: ActionRequest):
    # NEW: Check autonomy before creating approval request
    from autonomy import should_auto_execute
    auto, reason = should_auto_execute(req.action)
    if auto:
        result = run_action(req.action, req.arg or "")
        _send_telegram_direct(f"⚡ Auto-executed: {reason}")
        return result
    
    # ... else: proceed with normal approval flow ...
```

## Workflow: Correction → Rule → Auto-Execution

1. **User sends correction in Telegram:**
   ```
   /correct Always address me as Sir at the start
   ```

2. **rapid_learner.py** stores:
   - `corrections.jsonl` ← correction record
   - `lessons.jsonl` ← lesson candidate
   - `ChromaDB` ← immediate upsert
   - Spawns **skillset.py** thread

3. **skillset.py** (in background thread):
   - Calls DeepSeek-R1:7b to extract rule
   - `add_rule("When answering, always start with 'Sir'", priority=8, source="correction")`
   - `skillset.json` saved with new rule

4. **brain_injector.py** cache invalidated

5. **Next query from user:**
   - `get_injection(query)` returns formatted rules block
   - Rules injected into system prompt in **api.py** and **brain.py**
   - Model sees rule and applies it immediately

6. **Result:** No retraining delay, no prompt engineering required. Rules activate in ~100ms.

## Workflow: Approval → Learned Safe → Auto-Execution

1. **User runs action:**
   ```
   /exec restart_gateway
   ```

2. **autonomy.py** checks:
   - Not in `always_ask` list? ✓
   - In `pre_approved` list? ✓ → Auto-execute immediately
   - OR: Approval count >= 3? → Auto-execute

3. **If auto-executing:**
   - Log decision to `autonomy.log`
   - Execute action
   - Notify user: "⚡ Auto-executed: restart_gateway (pre-approved)"

4. **If requiring approval (first 2 times):**
   - Create approval request
   - Send Telegram with approve/deny buttons
   - Record approval in `skillset.json`
   - After 3rd approval, promote to `learned_safe`

5. **Result:** Frequent actions require approval once, then auto-execute forever with notification.

## Rules for Claude

When working on Jarvis code:

1. **Never break skillset.py** — it's the state machine for autonomy
   - Always syntax-check before restart
   - Never lose `data/skillset.json`
   - Test `load()` → `save()` → `load()` round-trip

2. **Injection must be fast**
   - Cache TTL = 5s (hardcoded in `brain_injector.py`)
   - Never block on rule extraction (use threading)
   - Never sync with ChromaDB at query-time

3. **Autonomy is conservative**
   - `pre_approved` list: only read-only + safe actions
   - `always_ask` list: NEVER shortened
   - `learned_safe`: requires 3+ human approvals
   - No ML-based autonomy — only explicit approval counts

4. **Rule extraction must not crash**
   - DeepSeek failures are logged but don't block
   - Heuristic fallback in `extract_and_store_rule_from_correction`
   - ChromaDB unavailability deferred to learner

5. **Testing**
   - Run `python3 skillset.py` before commit
   - Run `python3 brain_injector.py` before commit
   - Run `python3 autonomy.py` before commit
   - Run `/tmp/test_jarvis_autonomy.py` before restart

## Monitoring

Check autonomy health:
```bash
# See all rules
python3 -c "from skillset import load; sk=load(); print(json.dumps(sk['rules'][:3], indent=2))" 

# See autonomy map
python3 -c "from skillset import load; sk=load(); print(json.dumps(sk['autonomy'], indent=2))"

# Test injection for a query
python3 -c "from brain_injector import get_injection; print(get_injection('what is disk usage'))"

# Check auto-execution decision
python3 -c "from autonomy import should_auto_execute; print(should_auto_execute('restart_gateway'))"
```

## Future Enhancements

- [ ] Multi-model reasoning in rule extraction (Claude for complex rules)
- [ ] Skill gap auto-enrichment (generate training data for weak topics)
- [ ] Pattern learning from action sequences (suggest chains)
- [ ] Confidence scoring per rule (down-rank low-accuracy rules)
- [ ] Memory pruning (remove old, unused rules quarterly)
- [ ] Proactive suggestions (Jarvis recommends actions based on patterns)

## References

- `/home/kali/.jarvis/skillset.py` — Rule database
- `/home/kali/.jarvis/brain_injector.py` — Rule injection
- `/home/kali/.jarvis/autonomy.py` — Auto-execution
- `/home/kali/.jarvis/learning/rapid_learner.py` — Integration point
- `/home/kali/.jarvis/api.py` — Prompt building
- `/home/kali/.jarvis/brain.py` — Query routing
- `/home/kali/.claude/plans/crystalline-percolating-lobster.md` — Implementation plan
