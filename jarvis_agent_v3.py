#!/usr/bin/env python3
"""
Jarvis Agent v3 — Autonomous action loop powered by the v3 orchestrator.

Bridges the old jarvis_agent.py API to the v3 intelligence stack:
  • Task planning → Orchestrator.run() (decomposer + scope/trust gates)
  • LLM calls     → BrainRouter.execute() (7-tier routing)
  • Context       → thinking_engine.get_full_thinking_context()
  • Tools         → ToolRegistry.execute() via orchestrator

Public API (drop-in replacement for jarvis_agent.run_agent):
  run_agent(task, max_steps=6, allow_destructive=False) -> dict
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
LOG_FILE = JARVIS_HOME / "logs" / "agent_v3.log"
RUN_LOG = JARVIS_HOME / "data" / "agent_runs_v3.jsonl"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent_v3] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


# ── v3 Agent Core ───────────────────────────────────────────────────────────────

async def run_agent_v3(
    task: str,
    max_steps: int = 6,
    allow_destructive: bool = False,
    history: Optional[list] = None,
) -> dict:
    """
    Run the v3 autonomous agent on a plain-English task.

    Returns:
        {
          "answer": str,            # professional reply for Sir
          "steps": [ {tool, output_preview} ],
          "actions_run": [names],
          "needs_approval": str|None,
          "elapsed": float,
          "tier": str,              # brain router tier used
        }
    """
    t0 = time.time()

    # 1. Gather thinking context (goals, session state, intent)
    thinking_ctx = ""
    try:
        from thinking_engine import get_full_thinking_context
        thinking_ctx = get_full_thinking_context(task)
    except Exception as exc:
        log.debug("Thinking context unavailable: %s", exc)

    # 2. Route through BrainRouter to pick the right intelligence tier
    tier_name = "EDGE"
    try:
        from brain_router import BrainRouter
        router = BrainRouter()
        tier = router.route(task, history=history)
        tier_name = tier.value
    except Exception as exc:
        log.debug("BrainRouter unavailable: %s", exc)

    # 3. Run the orchestrator — this handles decomposition, scope/trust gating,
    #    nano-bot dispatch, and synthesis.
    try:
        from orchestrator import Orchestrator
        from tools.registry import ToolRegistry

        # Lazy-init singletons (safe for repeated calls)
        reg = ToolRegistry()
        for mod in [
            "tools.core.system", "tools.core.process", "tools.core.file",
            "tools.core.network", "tools.core.security", "tools.core.security_audit",
            "tools.core.dev", "tools.core.database", "tools.core.docker",
            "tools.core.web", "tools.core.backup", "tools.core.automation", "tools.core.comms",
        ]:
            __import__(mod)
        reg.index_all()

        orch = Orchestrator(tool_registry=reg, worker_count=1)
        result = await orch.run(task, allow_destructive=allow_destructive)
    except Exception as exc:
        log.warning("Orchestrator failed: %s", exc)
        return {
            "answer": f"⚠️ Sir, I encountered an error while processing your request: {exc}",
            "steps": [],
            "actions_run": [],
            "needs_approval": None,
            "elapsed": round(time.time() - t0, 1),
            "tier": tier_name,
        }

    # 4. If orchestrator paused for approval, return that immediately
    if result.needs_approval:
        answer = (
            f"Sir, to finish this I need your approval to run *{result.needs_approval}*. "
            "Approve it and I'll complete the task."
        )
        _log_run(task, result.steps, answer, result.needs_approval, tier_name)
        return {
            "answer": answer,
            "steps": [
                {"action": s.get("tool", s.get("task", "?")),
                 "output_preview": str(s.get("result", {}).get("output", ""))[:300]}
                for s in result.steps if s.get("tool")
            ],
            "actions_run": [s.get("tool", s.get("task", "?")) for s in result.steps if s.get("tool")],
            "needs_approval": result.needs_approval,
            "permission_request_id": result.permission_request_id,
            "permission_keyboard": result.permission_keyboard,
            "elapsed": round(time.time() - t0, 1),
            "tier": tier_name,
        }

    # 5. Post-process answer with BrainRouter if synthesis looks thin
    answer = result.answer
    if not answer or len(answer) < 20:
        answer = await _synthesize_v3(task, result.steps, thinking_ctx, tier_name)

    actions_run = [s.get("tool", s.get("task", "?")) for s in result.steps if s.get("tool")]
    _log_run(task, result.steps, answer, None, tier_name)

    return {
        "answer": answer,
        "steps": [
            {"action": s.get("tool", s.get("task", "?")),
             "output_preview": str(s.get("result", {}).get("output", ""))[:300]}
            for s in result.steps if s.get("tool")
        ],
        "actions_run": actions_run,
        "needs_approval": None,
        "elapsed": round(time.time() - t0, 1),
        "tier": tier_name,
    }


async def _synthesize_v3(task: str, steps: list, thinking_ctx: str, tier_name: str) -> str:
    """Use BrainRouter to synthesize a professional answer from step outputs."""
    if not steps:
        return "Sir, I couldn't gather enough to answer that. Try rephrasing the task."

    context = "\n\n".join(
        f"[{s.get('tool', s.get('task', '?'))}]\n{str(s.get('result', {}).get('output', ''))[:600]}"
        for s in steps
    )
    prompt = (
        f"Task: {task}\n\n"
        f"Gathered data:\n{context}\n\n"
        f"Write the final answer for Sir. Start with 'Sir,'. Be concise, factual, professional."
    )
    if thinking_ctx:
        prompt = f"{thinking_ctx}\n\n{prompt}"

    try:
        from brain_router import BrainRouter
        router = BrainRouter()
        resp = await router.execute(
            query=prompt,
            context="",
            system_prompt="You are Jarvis, Sir Mike Samuel's AI assistant. Be concise and factual.",
        )
        ans = resp.content if hasattr(resp, "content") else str(resp)
        import re
        ans = re.sub(r"<think>.*?</think>", "", ans, flags=re.DOTALL).strip()
        return ans if ans else f"Sir, here's what I found:\n{context[:600]}"
    except Exception as exc:
        log.debug("Synthesis failed: %s", exc)
        return f"Sir, here's what I found:\n{context[:600]}"


def _log_run(task: str, steps: list, answer: str, needs_approval: Optional[str], tier: str) -> None:
    try:
        with open(RUN_LOG, "a") as f:
            f.write(json.dumps({
                "ts": int(time.time()),
                "task": task[:300],
                "actions": [s.get("tool", s.get("task", "?")) for s in steps],
                "needs_approval": needs_approval,
                "answer": answer[:300],
                "tier": tier,
            }) + "\n")
    except Exception:
        pass


# ── Backward-compat wrapper ─────────────────────────────────────────────────────

def run_agent(task: str, max_steps: int = 6, allow_destructive: bool = False) -> dict:
    """
    Synchronous wrapper matching the old jarvis_agent.run_agent() signature.
    Delegates to the async v3 implementation.
    """
    import asyncio
    try:
        return asyncio.run(run_agent_v3(task, max_steps, allow_destructive))
    except Exception as exc:
        log.warning("run_agent async failed (%s), falling back to legacy agent", exc)
        try:
            import jarvis_agent
            return jarvis_agent.run_agent(task, max_steps, allow_destructive)
        except Exception as exc2:
            log.error("Legacy agent also failed: %s", exc2)
            return {
                "answer": f"⚠️ Sir, both v3 and legacy agents failed. Error: {exc2}",
                "steps": [],
                "actions_run": [],
                "needs_approval": None,
                "elapsed": 0.0,
                "tier": "ERROR",
            }


# ── Self-test ───────────────────────────────────────────────────────────────────

def _test():
    import asyncio

    print("=== Jarvis Agent v3 self-test ===\n")

    print("Test 1: Read-only task (autonomous)...")
    result = asyncio.run(run_agent_v3("check cpu and memory", allow_destructive=False))
    print(f"  Actions run: {result['actions_run']}")
    print(f"  Tier: {result['tier']}")
    print(f"  Elapsed: {result['elapsed']}s")
    print(f"  Answer:\n  {result['answer'][:400]}\n")
    assert result["needs_approval"] is None

    print("Test 2: Destructive task pauses for approval (untrusted)...")
    result = asyncio.run(run_agent_v3("restart sshd service", allow_destructive=True))
    print(f"  Needs approval: {result['needs_approval']}")
    print(f"  Has permission keyboard: {bool(result.get('permission_keyboard'))}")
    assert result["needs_approval"] is not None
    print("  ✓ Correctly paused for approval\n")

    print("Test 3: Trusted destructive task auto-runs...")
    result = asyncio.run(run_agent_v3("restart ollama service", allow_destructive=True))
    print(f"  Actions run: {result['actions_run']}")
    print(f"  Needs approval: {result['needs_approval']}")
    assert result["needs_approval"] is None
    print("  ✓ Auto-ran trusted action\n")

    print("✅ Agent v3 self-test complete")


if __name__ == "__main__":
    _test()
