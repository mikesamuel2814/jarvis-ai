#!/usr/bin/env python3
"""
Jarvis Agent — the single autonomous action loop.

This is what makes Jarvis feel like a real AI assistant: you give it a task in
plain English, it decides which actions to run, runs the safe ones itself,
chains them until the task is done, and reports back professionally.

Design (deliberately simple — one loop, no frameworks):
  1. Plan   — local model (qwen2.5-coder:3b) picks the next action via STRUCTURED
              JSON output (Ollama `format` schema — reliable, VRAM-safe, no crash).
  2. Act    — reuse executor.ACTIONS (already whitelisted + tiered).
              AUTO + pre-approved/learned-safe actions run immediately.
              Destructive actions pause for Telegram approval.
  3. Observe— feed the action output back into the conversation.
  4. Repeat — until the model says done, or max_steps hit.
  5. Answer — synthesise a clean "Sir, ..." reply from everything gathered.

Public API:
  run_agent(task, max_steps=6, allow_destructive=False) -> dict
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
LOG_FILE    = JARVIS_HOME / "logs" / "agent.log"
RUN_LOG     = JARVIS_HOME / "data" / "agent_runs.jsonl"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# Planning model — qwen2.5-coder:7b: accurate action selection + stable with
# structured outputs, BUT only when the prompt is small (the full 69-action
# catalog segfaults llama-server on the 6GB card). We keep prompts small by
# relevance-filtering the catalog, and fall back to the 3b on any crash.
PLAN_MODEL     = os.environ.get("JARVIS_AGENT_MODEL", "qwen2.5-coder:7b")
FALLBACK_MODEL = os.environ.get("JARVIS_AGENT_FALLBACK", "qwen2.5-coder:3b")

# Core read-only actions always offered so simple asks never miss.
_CORE_ACTIONS = ["disk", "memory", "gpu", "services", "pm2_status",
                 "nginx_status", "uptime", "project_status"]
# Max actions shown to the planner (small prompt = stable 7b on 6GB VRAM).
_MAX_CATALOG = 16
OLLAMA_HOST = "http://localhost:11434"

# Decision schema the planner must return every step.
_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "done":    {"type": "boolean"},
        "action":  {"type": "string"},
        "arg":     {"type": "string"},
        "answer":  {"type": "string"},
    },
    "required": ["thought", "done"],
}


# ── Tool catalog (reuse executor's whitelisted actions) ───────────────────────

_STOPWORDS = {"the", "a", "an", "is", "are", "on", "to", "of", "and", "in", "my",
              "me", "do", "run", "check", "get", "show", "tell", "if", "it", "for",
              "what", "whats", "how", "can", "you", "please", "now", "this", "that"}


def _score_action(task_tokens: set[str], name: str, desc: str) -> int:
    """Keyword-overlap score between the task and an action's name + description."""
    name_tokens = set(re.split(r"[_\s]+", name.lower()))
    desc_tokens = set(re.findall(r"[a-z0-9]+", desc.lower()))
    score = 0
    for t in task_tokens:
        if t in name_tokens:
            score += 3            # name match is strongest signal
        elif t in desc_tokens:
            score += 1
    return score


def _tool_catalog(allow_destructive: bool, task: str = "") -> tuple[str, set[str]]:
    """Return (catalog_text, allowed_action_names) — relevance-filtered.

    Read-only AUTO actions are candidates always; CONFIRM/APPROVE only when
    allow_destructive. The list is trimmed to the most relevant ~16 actions for
    the task so the planner prompt stays small (keeps the 7b stable on 6GB VRAM)
    and accurate (fewer distractors). Core read-only actions are always included.
    """
    from executor import ACTIONS, AUTO

    task_tokens = {t for t in re.findall(r"[a-z0-9]+", task.lower())
                   if t not in _STOPWORDS and len(t) > 1}

    candidates = []  # (score, name, desc, tier)
    for name, entry in ACTIONS.items():
        if name.startswith("kali_"):
            continue
        tier = entry["tier"]
        if tier != AUTO and not allow_destructive:
            continue
        score = _score_action(task_tokens, name, entry["desc"])
        if name in _CORE_ACTIONS:
            score += 1  # gentle bias so basics are always available
        candidates.append((score, name, entry["desc"], tier))

    # Rank by relevance; keep top N, but always keep core read-only actions.
    candidates.sort(key=lambda c: -c[0])
    chosen: dict[str, tuple] = {}
    for score, name, desc, tier in candidates:
        if len(chosen) >= _MAX_CATALOG:
            break
        chosen[name] = (name, desc, tier)
    for core in _CORE_ACTIONS:
        if core in ACTIONS and core not in chosen:
            chosen[core] = (core, ACTIONS[core]["desc"], ACTIONS[core]["tier"])

    auto_lines, other_lines, allowed = [], [], set()
    for name, desc, tier in chosen.values():
        allowed.add(name)
        if tier == AUTO:
            auto_lines.append(f"  {name} — {desc}")
        else:
            other_lines.append(f"  {name} [{tier}] — {desc}")

    lines = ["READ-ONLY actions (safe, run freely):"]
    lines.extend(sorted(auto_lines))
    if other_lines:
        lines.append("\nSTATE-CHANGING actions (need approval unless pre-approved):")
        lines.extend(sorted(other_lines))
    return "\n".join(lines), allowed


# ── Planner (structured output) ───────────────────────────────────────────────

def _call_model(model: str, messages: list[dict]) -> dict:
    """One structured-output call. Raises on transport/model error."""
    import ollama
    client = ollama.Client(host=OLLAMA_HOST)
    resp = client.chat(
        model=model,
        messages=messages,
        format=_STEP_SCHEMA,
        options={"temperature": 0, "num_ctx": 2048, "num_predict": 400},
    )
    raw = resp["message"]["content"]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise


def _plan_step(messages: list[dict]) -> dict:
    """Next step as structured JSON. Tries 7b (accurate); on crash/parse-fail
    falls back to 3b (stable). Never raises."""
    try:
        return _call_model(PLAN_MODEL, messages)
    except Exception as exc:
        log.warning("planner %s failed (%s) — falling back to %s",
                    PLAN_MODEL, str(exc)[:60], FALLBACK_MODEL)
        try:
            return _call_model(FALLBACK_MODEL, messages)
        except Exception as exc2:
            log.warning("fallback planner failed: %s", str(exc2)[:60])
            return {"thought": f"planner error: {exc2}", "done": True, "answer": ""}


# ── Action execution with autonomy gate ───────────────────────────────────────

def _execute(action: str, arg: str, allow_destructive: bool) -> dict:
    """Run an action through the autonomy gate.

    Returns {ran: bool, output: str, needs_approval: bool, tier: str}.
    """
    from executor import ACTIONS, run_action, AUTO

    entry = ACTIONS.get(action)
    if not entry:
        return {"ran": False, "output": f"Unknown action: {action}", "needs_approval": False, "tier": "?"}

    tier = entry["tier"]

    # Read-only actions always run.
    if tier == AUTO:
        res = run_action(action, arg, approved=True)
        return {"ran": True, "output": str(res.get("output", "")), "needs_approval": False, "tier": tier}

    # State-changing: consult autonomy (pre-approved / learned-safe).
    auto_ok = False
    try:
        from autonomy import should_auto_execute, log_execution_outcome
        auto_ok, reason = should_auto_execute(action)
    except Exception:
        reason = "autonomy unavailable"

    if auto_ok and allow_destructive:
        res = run_action(action, arg, approved=True)
        try:
            log_execution_outcome(action, success=res.get("success", False), auto=True)
        except Exception:
            pass
        return {"ran": True, "output": str(res.get("output", "")),
                "needs_approval": False, "tier": tier}

    # Needs human approval — pause the loop.
    return {"ran": False, "output": f"'{entry['desc']}' requires your approval.",
            "needs_approval": True, "tier": tier}


# ── Main agent loop ───────────────────────────────────────────────────────────

_SYSTEM_TEMPLATE = """You are Jarvis, Sir Mike Samuel's autonomous AI assistant.
You complete tasks by running actions and reasoning over their output.

Available actions:
{catalog}

Rules:
- Each step, return JSON: {{"thought": "...", "done": false, "action": "name", "arg": "optional"}}
- Run read-only actions freely to gather facts before concluding.
- 'arg' is only for actions that take one (file paths, shell args). Omit otherwise.
- When you have enough to answer, return {{"thought":"...","done":true,"answer":"Sir, ..."}}
- The answer must start with "Sir," be concise, professional, and factual.
- Never invent action names. Never repeat an action that already succeeded.
- If a task needs a state-changing action you cannot run, explain what needs approval."""


def run_agent(task: str, max_steps: int = 6, allow_destructive: bool = False) -> dict:
    """Run the autonomous agent loop on a plain-English task.

    Returns:
        {
          "answer": str,            # professional reply for Sir
          "steps": [ {action, output_preview} ],
          "actions_run": [names],
          "needs_approval": str|None,  # action name awaiting approval, if paused
          "elapsed": float,
        }
    """
    t0 = time.time()
    catalog, allowed = _tool_catalog(allow_destructive, task=task)
    system = _SYSTEM_TEMPLATE.format(catalog=catalog)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]

    steps: list[dict] = []
    actions_run: list[str] = []
    ran_ok: set[str] = set()

    for i in range(max_steps):
        plan = _plan_step(messages)
        thought = plan.get("thought", "")
        log.info("step %d: %s", i + 1, thought[:80])

        if plan.get("done"):
            answer = (plan.get("answer") or "").strip()
            if not answer:
                answer = _synthesize(task, steps)
            _log_run(task, steps, answer, None)
            return {"answer": answer, "steps": steps, "actions_run": actions_run,
                    "needs_approval": None, "elapsed": round(time.time() - t0, 1)}

        action = (plan.get("action") or "").strip()
        arg = (plan.get("arg") or "").strip()

        if not action:
            # Model didn't pick an action and isn't done — nudge once, else finish.
            messages.append({"role": "assistant", "content": json.dumps(plan)})
            messages.append({"role": "user", "content": "Pick an action or set done=true with an answer."})
            continue

        if action not in allowed:
            messages.append({"role": "assistant", "content": json.dumps(plan)})
            messages.append({"role": "user",
                             "content": f"'{action}' is not available. Choose from the listed actions only."})
            continue

        if action in ran_ok:
            # Avoid loops — force a conclusion.
            messages.append({"role": "assistant", "content": json.dumps(plan)})
            messages.append({"role": "user",
                             "content": f"You already ran {action}. Use what you have and set done=true."})
            continue

        result = _execute(action, arg, allow_destructive)

        if result["needs_approval"]:
            answer = (f"Sir, to finish this I need your approval to run *{action}* "
                      f"({result['tier']} tier). Approve it and I'll complete the task.")
            _log_run(task, steps, answer, action)
            return {"answer": answer, "steps": steps, "actions_run": actions_run,
                    "needs_approval": action, "elapsed": round(time.time() - t0, 1)}

        output = result["output"]
        actions_run.append(action)
        if result["ran"]:
            ran_ok.add(action)
        preview = output[:1500]
        steps.append({"action": action, "arg": arg, "output_preview": output[:300]})

        # Observe: feed result back.
        messages.append({"role": "assistant", "content": json.dumps(plan)})
        messages.append({"role": "user",
                         "content": f"Result of {action}:\n{preview}\n\nContinue or set done=true."})

    # Hit step limit — synthesize from what we have.
    answer = _synthesize(task, steps)
    _log_run(task, steps, answer, None)
    return {"answer": answer, "steps": steps, "actions_run": actions_run,
            "needs_approval": None, "elapsed": round(time.time() - t0, 1)}


def _synthesize(task: str, steps: list[dict]) -> str:
    """Produce a final professional answer from gathered step outputs."""
    if not steps:
        return "Sir, I couldn't gather enough to answer that. Try rephrasing the task."

    context = "\n\n".join(f"[{s['action']}]\n{s['output_preview']}" for s in steps)
    prompt = (
        f"Task: {task}\n\nGathered data:\n{context}\n\n"
        f"Write the final answer for Sir. Start with 'Sir,'. Be concise, factual, professional."
    )
    try:
        import ollama
        client = ollama.Client(host=OLLAMA_HOST)
        resp = client.chat(
            model=PLAN_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.2, "num_ctx": 2048, "num_predict": 400},
        )
        ans = re.sub(r"<think>.*?</think>", "", resp["message"]["content"], flags=re.DOTALL).strip()
        return ans if ans else f"Sir, here's what I found:\n{context[:600]}"
    except Exception:
        return f"Sir, here's what I found:\n{context[:600]}"


def _log_run(task: str, steps: list, answer: str, needs_approval: str | None) -> None:
    try:
        with open(RUN_LOG, "a") as f:
            f.write(json.dumps({
                "ts": int(time.time()),
                "task": task[:300],
                "actions": [s["action"] for s in steps],
                "needs_approval": needs_approval,
                "answer": answer[:300],
            }) + "\n")
    except Exception:
        pass


# ── Self-test ─────────────────────────────────────────────────────────────────

def _test():
    print("=== Jarvis Agent self-test ===\n")

    print("Test 1: Tool catalog builds + relevance-filters...")
    cat, allowed = _tool_catalog(allow_destructive=False, task="check disk usage")
    assert "disk" in allowed and "reboot" not in allowed
    assert len(allowed) <= _MAX_CATALOG + len(_CORE_ACTIONS)
    cat2, allowed2 = _tool_catalog(allow_destructive=True, task="restart the payment gateway backend")
    assert "restart_gateway" in allowed2, f"relevant action filtered out: {allowed2}"
    print(f"  ✓ Filtered safe: {len(allowed)} | Filtered destructive: {len(allowed2)} (restart_gateway present)\n")

    print("Test 2: Read-only task (autonomous, no approval)...")
    result = run_agent("What is the current disk and memory usage on this machine?", max_steps=4)
    print(f"  Actions run: {result['actions_run']}")
    print(f"  Elapsed: {result['elapsed']}s")
    print(f"  Answer:\n  {result['answer'][:400]}\n")
    assert result["needs_approval"] is None

    print("Test 3: Destructive task pauses for approval...")
    result = run_agent("Reboot the machine now", max_steps=3, allow_destructive=True)
    print(f"  Needs approval: {result['needs_approval']}")
    print(f"  Answer: {result['answer'][:200]}\n")

    print("✅ Agent self-test complete")


if __name__ == "__main__":
    _test()
