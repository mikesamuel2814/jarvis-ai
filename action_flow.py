#!/usr/bin/env python3
"""
Jarvis Action Flow — step-by-step orchestrator behind /claude-plan.

Responsibilities:
  1. Understand a request (claude_planner.plan_actions → OpenClaw/executor actions).
  2. For each planned action:
        - try OpenClaw FIRST  (openclaw.bridge.run_action)
        - on ok=False & fallback=True → executor.run_action
        - CONFIRM/APPROVE tiers are NEVER auto-run; a pending approval request is
          created (permissions.create_request) and surfaced to Sir.
  3. Multi-step tasks run in order, each step reported ("step 1 … ✓"). On the
     first hard failure we stop and surface the error instead of plowing ahead.
  4. Pure questions (no actions) are answered via brain.execute().
  5. Every interaction is logged via learning.rapid_learner.store_interaction so
     thumbs-up/down feedback works, and an interaction_id is returned.

Persona: replies open with "Sir," — concise, technical, no "Certainly/Of course/
Sure/Absolutely".

All imports are lazy (inside functions) to match api_v2 style and to dodge the
chromadb import-time segfault.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

log = logging.getLogger("jarvis.action_flow")

# Phrases the persona must never open with.
_BANNED_OPENERS = ("certainly", "of course", "sure", "absolutely")


def _personable(text: str) -> str:
    """Enforce the Jarvis persona: strip banned openers, ensure a 'Sir,' lead."""
    t = (text or "").strip()
    if not t:
        return "Sir, the action returned no output."
    low = t.lower()
    for banned in _BANNED_OPENERS:
        if low.startswith(banned):
            # Drop the banned opener and any trailing punctuation/space.
            t = t[len(banned):].lstrip(" ,.!:").strip()
            low = t.lower()
            break
    if not low.startswith("sir"):
        t = "Sir, " + t
    return t


# ── OpenClaw-first → executor fallback for a single action ──────────────────────

def run_action_step(action_name: str, arg: str = "") -> dict:
    """Execute one AUTO action, OpenClaw-first then executor fallback.

    Returns the executor-shaped dict: {success, output, action, tier, via}.
    Does NOT handle permission tiers — callers gate CONFIRM/APPROVE before
    calling this (those never auto-run).
    """
    via = "executor"
    # 1. OpenClaw first.
    try:
        from openclaw.bridge import run_action as oc_run_action
        oc = oc_run_action(action_name, {"arg": arg} if arg else None)
        if oc.get("ok"):
            return {
                "success": True,
                "output": oc.get("output", "") or "Done.",
                "action": action_name,
                "via": "openclaw",
            }
        # ok=False — fall through to executor when the bridge signals fallback.
        if not oc.get("fallback", True):
            return {
                "success": False,
                "output": oc.get("reason", "OpenClaw refused this action."),
                "action": action_name,
                "via": "openclaw",
            }
    except Exception as e:  # noqa: BLE001 — bridge must never break the flow
        log.debug("OpenClaw run_action skipped for %s: %s", action_name, e)

    # 2. Executor fallback.
    try:
        from executor import run_action as exec_run_action
        result = exec_run_action(action_name, arg)
        if isinstance(result, dict):
            result.setdefault("action", action_name)
            result["via"] = via
            return result
        return {"success": False, "output": str(result), "action": action_name, "via": via}
    except Exception as e:  # noqa: BLE001
        log.error("executor.run_action(%s) failed: %s", action_name, e)
        return {"success": False, "output": f"Executor error: {e}", "action": action_name, "via": via}


# ── Main orchestrator ───────────────────────────────────────────────────────────

def orchestrate(
    query: str,
    rag_context: str = "",
    history: list[dict] | None = None,
    notify=None,
) -> dict[str, Any]:
    """Plan → execute step-by-step (OpenClaw→executor) → synthesize.

    Args:
        query:        Sir's natural-language request.
        rag_context:  Retrieved memory context (used for the pure-question path).
        history:      Conversation history for brain.execute().
        notify:       Optional callable(str) used to push approval prompts to
                      Telegram (api_v2 supplies a sender). Never required.

    Returns a dict shaped for the /claude-plan response:
        {response, model, interaction_id, context_used, steps, pending}
    """
    from executor import ACTIONS, AUTO
    from permissions import create_request
    from learning.rapid_learner import store_interaction

    interaction_id = str(uuid.uuid4())

    # ── 1. Plan ────────────────────────────────────────────────────────────────
    plan: dict = {"needs_actions": False, "actions": []}
    try:
        from claude_planner import plan_actions
        plan = plan_actions(query, ACTIONS) or plan
    except Exception as e:  # noqa: BLE001
        log.warning("planner unavailable, treating as question: %s", e)

    planned = plan.get("actions") or []
    # Keep only actions Jarvis actually knows.
    planned = [a for a in planned if a.get("action") in ACTIONS]

    # ── 2. Pure question — no concrete action ──────────────────────────────────
    if not plan.get("needs_actions") or not planned:
        response, model = _answer_question(query, rag_context, history)
        response = _personable(response)
        _safe_log(store_interaction, interaction_id, query, response, "question", model)
        return {
            "response": response,
            "model": model,
            "interaction_id": interaction_id,
            "context_used": 0,
            "steps": [],
            "pending": [],
        }

    # ── 3. Execute the planned steps in order ──────────────────────────────────
    results: list[dict] = []
    step_lines: list[str] = []
    pending_descs: list[str] = []
    multistep = len(planned) > 1

    for i, spec in enumerate(planned, start=1):
        action_name = spec.get("action", "")
        entry = ACTIONS[action_name]
        arg = spec.get("arg", "") or ""
        tier = entry["tier"]

        # CONFIRM/APPROVE never auto-run — raise an approval request instead.
        if tier != AUTO:
            try:
                perm_req = create_request(
                    action=action_name,
                    description=entry["desc"],
                    tier=tier,
                    arg=arg,
                )
                rid = perm_req["id"]
            except Exception as e:  # noqa: BLE001
                log.error("create_request failed for %s: %s", action_name, e)
                step_lines.append(f"step {i} ({entry['desc']}) … ✗ could not queue approval")
                continue
            pending_descs.append(entry["desc"])
            step_lines.append(
                f"step {i} ({entry['desc']}) … ⏳ approval required "
                f"(ID {rid}: /approve {rid} or /deny {rid})"
            )
            if notify:
                try:
                    notify(
                        f"⚡ Jarvis planned action — ID: `{rid}`\n"
                        f"**{entry['desc']}**"
                        + (f"\nArg: `{arg}`" if arg else "")
                        + f"\n\nReply `/approve {rid}` or `/deny {rid}`"
                    )
                except Exception:  # noqa: BLE001
                    pass
            continue

        # AUTO action: OpenClaw-first → executor.
        result = run_action_step(action_name, arg)
        results.append(result)
        icon = "✓" if result.get("success") else "✗"
        via = result.get("via", "executor")
        step_lines.append(f"step {i} ({entry['desc']}) … {icon} [{via}]")

        # Stop a multi-step plan on the first hard failure — don't plow ahead.
        if multistep and not result.get("success"):
            step_lines.append(
                f"Halted after step {i}: {result.get('output', 'unknown error')[:300]}"
            )
            break

    # ── 4. Synthesize a reply ──────────────────────────────────────────────────
    response = ""
    if results:
        try:
            from claude_planner import synthesize_results
            response = synthesize_results(query, results)
        except Exception as e:  # noqa: BLE001
            log.warning("synthesis failed, falling back to raw output: %s", e)
            response = _raw_results_block(results)

    parts: list[str] = []
    if response:
        parts.append(response.strip())
    if multistep or pending_descs:
        parts.append("Steps:\n" + "\n".join(step_lines))
    if pending_descs:
        parts.append("⏳ Awaiting approval: " + "; ".join(pending_descs))

    final = "\n\n".join(p for p in parts if p).strip()
    if not final:
        # Everything was pending or empty — still answer the underlying question.
        q_resp, _ = _answer_question(query, rag_context, history)
        final = q_resp

    final = _personable(final)
    _safe_log(store_interaction, interaction_id, query, final, "claude+openclaw", "claude+openclaw")

    return {
        "response": final,
        "model": "claude+openclaw",
        "interaction_id": interaction_id,
        "context_used": len(results),
        "steps": step_lines,
        "pending": pending_descs,
    }


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _answer_question(query: str, rag_context: str, history: list[dict] | None) -> tuple[str, str]:
    """Answer a non-action question via brain.execute(). Never raises."""
    try:
        from brain import execute as brain_execute
        result = brain_execute(query=query, rag_context=rag_context, history=history)
        return result.get("response", "").strip() or "No response.", result.get("model", "brain")
    except Exception as e:  # noqa: BLE001
        log.error("brain.execute failed: %s", e)
        return f"I could not reach the reasoning brain: {e}", "error"


def _raw_results_block(results: list[dict]) -> str:
    lines = []
    for r in results:
        icon = "✓" if r.get("success") else "✗"
        lines.append(f"{icon} {r.get('action', '?')}:\n{r.get('output', '')[:600]}")
    return "\n\n".join(lines)


def _safe_log(store_fn, interaction_id: str, query: str, response: str, tier: str, model: str) -> None:
    try:
        store_fn(
            interaction_id=interaction_id,
            query=query,
            response=response,
            tier=tier,
            model=model,
            rag_context_ids=[],
        )
    except Exception as e:  # noqa: BLE001
        log.warning("store_interaction failed: %s", e)


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO)
    q = sys.argv[1] if len(sys.argv) > 1 else "check disk usage"
    print(json.dumps(orchestrate(q), indent=2))
