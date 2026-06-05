#!/usr/bin/env python3
"""
Jarvis Planner — uses Ollama (deepseek-r1:7b) to plan OpenClaw actions.
Replaced Claude CLI subprocess to avoid Bun segfault crashes.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import ollama

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))

_PLAN_SYSTEM = (
    "You are the decision brain for Jarvis, Mike's personal AI on Kali Linux. "
    "Your ONLY task: analyze the user request and output valid JSON — no preamble, no markdown. "
    "Output exactly one JSON object."
)

_PLAN_PROMPT = """\
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

_SYNTH_SYSTEM = (
    "You are Jarvis, Mike's personal AI assistant on Kali Linux. "
    "Give concise, technical, direct responses. No filler."
)

_SYNTH_PROMPT = """\
Mike asked: "{query}"

Action outputs:
{results_block}

Write a concise direct response for Mike based on this data.
- Highlight problems or anomalies first
- Be technical and specific (real numbers, real names)
- If everything looks normal, say so in one line
- No filler phrases
"""


def _strip_thinking(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"</?think>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def plan_actions(query: str, available_actions: dict) -> dict:
    """Use local LLM to plan which OpenClaw actions to run."""
    actions_list = "\n".join(
        f"  {k}: {v['desc']} (tier:{v['tier']})"
        for k, v in available_actions.items()
    )
    prompt = _PLAN_PROMPT.format(
        actions_list=actions_list,
        query=query.replace('"', "'"),
    )
    try:
        resp = ollama.chat(
            model="deepseek-r1:7b",
            messages=[
                {"role": "system", "content": _PLAN_SYSTEM},
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


def synthesize_results(query: str, results: list) -> str:
    """Turn raw action outputs into a clean response using local LLM."""
    if not results:
        return "No actions ran."

    parts = []
    for r in results:
        icon = "✓" if r.get("success") else "✗"
        parts.append(f"[{icon} {r.get('action', '?')}]\n{r.get('output', '')[:1200]}")
    results_block = "\n\n".join(parts)

    prompt = _SYNTH_PROMPT.format(
        query=query.replace('"', "'"),
        results_block=results_block,
    )
    try:
        resp = ollama.chat(
            model="deepseek-r1:7b",
            messages=[
                {"role": "system", "content": _SYNTH_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.4, "num_predict": 1024},
        )
        text = _strip_thinking(resp["message"]["content"]).strip()
        if text:
            return text
    except Exception:
        pass

    # Fallback: raw formatted output
    lines = []
    for r in results:
        icon = "✅" if r.get("success") else "❌"
        lines.append(f"{icon} `{r.get('action')}`\n```\n{r.get('output','')[:600]}\n```")
    return "\n\n".join(lines)
