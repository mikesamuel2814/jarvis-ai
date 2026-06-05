#!/usr/bin/env python3
"""
Jarvis Planner — uses Claude Opus 4.8 (Anthropic SDK) for planning and synthesis.
Falls back to Ollama deepseek-r1:7b when ANTHROPIC_API_KEY is not set.

OpenClaw = Jarvis action system (executor.py actions).
"""

import json
import os
import re
from pathlib import Path

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── helpers ──────────────────────────────────────────────────────────────────

def _strip_thinking(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"</?think>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── Claude Opus 4.8 path ─────────────────────────────────────────────────────

_PLAN_SYSTEM = (
    "You are the decision brain for Jarvis, Mike Samuel's personal AI on Kali Linux. "
    "Analyze the user request and decide which OpenClaw system actions to execute, if any. "
    "For casual chat or knowledge questions with no system action needed, do not call any tool. "
    "Address Mike as Sir."
)

_SYNTH_SYSTEM = (
    "You are Jarvis, Sir Mike Samuel's personal AI assistant on Kali Linux. "
    "Give concise, technical, direct responses. No filler. Address him as Sir."
)


def _actions_to_tools(available_actions: dict) -> list:
    """Convert OpenClaw ACTIONS dict to Anthropic tool definitions."""
    tools = []
    for name, entry in available_actions.items():
        desc = entry.get("desc", name)
        tier = entry.get("tier", "auto")
        needs_arg = name in ("shell", "ssh_cmd", "deploy_vps")
        properties = {}
        required = []
        if needs_arg:
            properties["arg"] = {
                "type": "string",
                "description": "Command or argument to pass to the action.",
            }
            required.append("arg")
        tools.append({
            "name": name,
            "description": f"{desc} (approval tier: {tier})",
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        })
    return tools


def _plan_with_claude(query: str, available_actions: dict) -> dict:
    """Use Claude Opus 4.8 tool use to plan actions."""
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    tools = _actions_to_tools(available_actions)

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=1024,
        thinking={"type": "adaptive"},
        system=_PLAN_SYSTEM,
        tools=tools,
        messages=[{"role": "user", "content": query}],
    )

    actions = []
    for block in response.content:
        if block.type == "tool_use":
            actions.append({
                "action": block.name,
                "arg": block.input.get("arg", "") if block.input else "",
            })

    return {
        "needs_actions": len(actions) > 0,
        "actions": actions,
        "reasoning": "Claude Opus 4.8 tool selection",
    }


def _synthesize_with_claude(query: str, results: list) -> str:
    """Use Claude Opus 4.8 to turn action outputs into a clean response."""
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    parts = []
    for r in results:
        icon = "✓" if r.get("success") else "✗"
        parts.append(f"[{icon} {r.get('action', '?')}]\n{r.get('output', '')[:1500]}")
    results_block = "\n\n".join(parts)

    prompt = (
        f"Mike asked: \"{query}\"\n\n"
        f"Action outputs:\n{results_block}\n\n"
        "Write a concise direct response:\n"
        "- Highlight problems or anomalies first\n"
        "- Be technical and specific (real numbers, real names)\n"
        "- If everything looks normal, say so in one line\n"
        "- No filler phrases\n"
        "- Address Mike as Sir"
    )

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=1024,
        thinking={"type": "adaptive"},
        system=_SYNTH_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    for block in response.content:
        if block.type == "text" and block.text.strip():
            return block.text.strip()
    return "Done."


# ── Ollama fallback ───────────────────────────────────────────────────────────

_OLLAMA_PLAN_SYSTEM = (
    "You are the decision brain for Jarvis, Mike's personal AI on Kali Linux. "
    "Your ONLY task: analyze the user request and output valid JSON — no preamble, no markdown. "
    "Output exactly one JSON object."
)

_OLLAMA_PLAN_PROMPT = """\
Available OpenClaw actions:
{actions_list}

User request: "{query}"

RULES:
- Output ONLY valid JSON
- Max 5 actions; only include actions directly needed
- For casual chat / knowledge questions with no system action: needs_actions = false
- "arg" is only for: shell (arbitrary cmd), ssh_cmd (remote cmd), deploy_vps (repo name)

Required JSON:
{{"needs_actions": true, "actions": [{{"action": "action_name", "arg": ""}}], "reasoning": "one-line why"}}
"""

_OLLAMA_SYNTH_SYSTEM = (
    "You are Jarvis, Mike's personal AI assistant on Kali Linux. "
    "Give concise, technical, direct responses. No filler."
)

_OLLAMA_SYNTH_PROMPT = """\
Mike asked: "{query}"

Action outputs:
{results_block}

Write a concise direct response for Mike based on this data.
- Highlight problems or anomalies first
- Be technical and specific (real numbers, real names)
- If everything looks normal, say so in one line
- No filler phrases
"""


def _plan_with_ollama(query: str, available_actions: dict) -> dict:
    import ollama as _ollama
    actions_list = "\n".join(
        f"  {k}: {v['desc']} (tier:{v['tier']})"
        for k, v in available_actions.items()
    )
    prompt = _OLLAMA_PLAN_PROMPT.format(
        actions_list=actions_list,
        query=query.replace('"', "'"),
    )
    try:
        resp = _ollama.chat(
            model="deepseek-r1:7b",
            messages=[
                {"role": "system", "content": _OLLAMA_PLAN_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.1, "num_predict": 512},
        )
        raw = _strip_thinking(resp["message"]["content"])
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except Exception:
        pass
    return {"needs_actions": False, "actions": [], "reasoning": "planner unavailable"}


def _synthesize_with_ollama(query: str, results: list) -> str:
    import ollama as _ollama
    if not results:
        return "No actions ran."
    parts = []
    for r in results:
        icon = "✓" if r.get("success") else "✗"
        parts.append(f"[{icon} {r.get('action', '?')}]\n{r.get('output', '')[:1200]}")
    results_block = "\n\n".join(parts)
    prompt = _OLLAMA_SYNTH_PROMPT.format(
        query=query.replace('"', "'"),
        results_block=results_block,
    )
    try:
        resp = _ollama.chat(
            model="deepseek-r1:7b",
            messages=[
                {"role": "system", "content": _OLLAMA_SYNTH_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.4, "num_predict": 1024},
        )
        text = _strip_thinking(resp["message"]["content"]).strip()
        if text:
            return text
    except Exception:
        pass
    lines = []
    for r in results:
        icon = "✅" if r.get("success") else "❌"
        lines.append(f"{icon} `{r.get('action')}`\n```\n{r.get('output','')[:600]}\n```")
    return "\n\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

def plan_actions(query: str, available_actions: dict) -> dict:
    """Plan which OpenClaw actions to run for this query."""
    if ANTHROPIC_API_KEY:
        try:
            return _plan_with_claude(query, available_actions)
        except Exception:
            pass
    return _plan_with_ollama(query, available_actions)


def synthesize_results(query: str, results: list) -> str:
    """Turn raw action outputs into a clean Jarvis response."""
    if not results:
        return "No actions ran."
    if ANTHROPIC_API_KEY:
        try:
            return _synthesize_with_claude(query, results)
        except Exception:
            pass
    return _synthesize_with_ollama(query, results)
