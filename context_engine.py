#!/usr/bin/env python3
"""
Jarvis Context Engine — loads Mike's profile and formats tight context blocks
that get injected into EVERY prompt. Ensures Jarvis always knows who Sir is.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import yaml

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
PROFILE_FILE = JARVIS_HOME / "config" / "mike_profile.yaml"
log = logging.getLogger("jarvis.context_engine")

_PROFILE: Optional[dict] = None


def _load_profile() -> dict:
    global _PROFILE
    if _PROFILE is not None:
        return _PROFILE
    if PROFILE_FILE.exists():
        try:
            _PROFILE = yaml.safe_load(PROFILE_FILE.read_text()) or {}
            log.debug("Loaded Mike profile")
            return _PROFILE
        except Exception as exc:
            log.warning("Could not load profile: %s", exc)
    _PROFILE = {}
    return _PROFILE


def get_identity_block() -> str:
    """Core identity facts — who Sir is."""
    p = _load_profile()
    ident = p.get("identity", {})
    if not ident:
        return ""
    lines = [
        "## IDENTITY — Sir Mike Samuel",
        f"- Name: {ident.get('name', 'Mike Samuel')}",
        f"- Address as: {ident.get('address_as', 'Sir')} — ALWAYS, every response",
        f"- Role: {ident.get('role', 'Full-stack developer & entrepreneur')}",
        f"- Location: {ident.get('location', 'Kali Linux workstation')}",
        f"- Timezone: {ident.get('timezone', 'Asia/Dhaka')}",
    ]
    return "\n".join(lines)


def get_style_block() -> str:
    """Communication style rules — how Jarvis must speak."""
    p = _load_profile()
    style = p.get("communication_style", {})
    resp = p.get("response_style", {})
    if not style and not resp:
        return ""
    lines = ["## COMMUNICATION RULES — MANDATORY"]
    if style.get("address_as"):
        lines.append(f'- Address user as "{style["address_as"]}" in EVERY response. No exceptions.')
    if style.get("tone"):
        lines.append(f'- Tone: {style["tone"]}')
    if style.get("forbidden_openers"):
        lines.append(f'- NEVER use these openers: {", ".join(style["forbidden_openers"])}')
    if style.get("response_length"):
        lines.append(f'- Length: {style["response_length"]}')
    if style.get("answer_style"):
        lines.append(f'- Answer style: {style["answer_style"]}')
    if style.get("follow_ups") is not None:
        lines.append(f'- Follow-ups: {style["follow_ups"]}')
    if style.get("yes_no"):
        lines.append(f'- Yes/No: {style["yes_no"]}')
    if resp.get("max_sentences"):
        lines.append(f'- Max sentences: {resp["max_sentences"]}')
    if resp.get("no_hallucination"):
        lines.append('- Reality check: if you do not know, say so. Never invent.')
    if resp.get("unknown_terms"):
        lines.append(f'- Unknown terms: {resp["unknown_terms"]}')
    return "\n".join(lines)


def get_projects_block() -> str:
    """Active projects — so Jarvis references real paths and stacks."""
    p = _load_profile()
    projects = p.get("projects", [])
    if not projects:
        return ""
    lines = ["## ACTIVE PROJECTS"]
    for proj in projects:
        if isinstance(proj, dict):
            lines.append(f'- {proj.get("name", "?")} ({proj.get("type", "?")})')
            lines.append(f'  Path: {proj.get("path", "?")}')
            lines.append(f'  Stack: {", ".join(proj.get("stack", []))}')
            if proj.get("vps"):
                lines.append(f'  VPS: {proj["vps"]}')
    return "\n".join(lines)


def get_persona_block() -> str:
    """Jarvis persona — who Jarvis is."""
    p = _load_profile()
    persona = p.get("jarvis_persona", {})
    if not persona:
        return ""
    lines = [
        "## PERSONA — Jarvis",
        f'- Self: {persona.get("self_description", "Jarvis — personal AI assistant")}',
        f'- Character: {persona.get("character", "brilliant loyal butler")}',
        f'- Relationship: {persona.get("relationship", "loyal, efficient, technically sharp")}',
    ]
    if persona.get("never"):
        lines.append(f'- NEVER: {persona["never"]}')
    return "\n".join(lines)


def get_full_context_block(query: str = "") -> str:
    """Assemble the complete context block for prompt injection."""
    parts = [
        get_identity_block(),
        get_persona_block(),
        get_projects_block(),
        get_style_block(),
    ]
    block = "\n\n".join(p for p in parts if p)
    return block


def get_system_prompt(task_type: str = "chat") -> str:
    """Build a strict system prompt based on the profile."""
    p = _load_profile()
    style = p.get("communication_style", {})
    resp = p.get("response_style", {})
    persona = p.get("jarvis_persona", {})

    forbidden = ", ".join(style.get("forbidden_openers", []))
    max_sent = resp.get("max_sentences", 5)

    base = (
        "You are Jarvis, Sir Mike Samuel's personal AI assistant and loyal technical brain.\n\n"
        "MANDATORY RULES (violation = incorrect response):\n"
        f'1. EVERY response MUST start with "Sir," — no exceptions.\n'
        f"2. Max {max_sent} sentences. Under 50 words unless elaboration is explicitly requested.\n"
        f"3. Direct, minimal, zero fluff. One line when possible.\n"
        f"4. NEVER use these openers: {forbidden}\n"
        f"5. Concrete answers only — no vague suggestions.\n"
        f"6. Never ask follow-up questions or offer further help unprompted.\n"
        f"7. Answer yes or no directly when that is what was asked.\n"
        f"8. If you do not know something, say exactly 'Sir, I don't know.' Never invent.\n"
        f"9. Never mention being an AI by Anthropic, OpenAI, or any company. You are simply Jarvis.\n"
        f"10. Senior-developer technical depth — skip basics, go straight to the point.\n"
    )

    if task_type == "tools":
        base += (
            "\nTOOL RESPONSE RULES:\n"
            "1. Summarize findings in 1-2 sentences.\n"
            "2. Include specific numbers/paths from the tool output.\n"
            "3. Flag any anomalies immediately.\n"
        )
    elif task_type == "chat":
        base += (
            "\nCHAT RULES:\n"
            "1. Warm but brief.\n"
            "2. Acknowledge Sir's status and context.\n"
            "3. No emojis unless Sir uses them first.\n"
        )

    return base


if __name__ == "__main__":
    print(get_full_context_block())
    print("\n" + "=" * 60 + "\n")
    print(get_system_prompt("chat"))
