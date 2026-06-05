#!/usr/bin/env python3
"""Persist explicit user-stated facts (preferences), separate from RAG code context."""

import json
import re
from pathlib import Path

JARVIS_HOME = Path.home() / ".jarvis"
FACTS_FILE = JARVIS_HOME / "data" / "user_facts.json"

# "my favourite language is python", "my favorite programing language is python"
_FACT_PATTERNS = [
    re.compile(
        r"my\s+(?:favourite|favorite|preferred|choosen|chosen)\s+(\w[\w\s]{0,40}?)\s+is\s+(.+)",
        re.I,
    ),
    re.compile(r"i\s+(?:like|love|prefer)\s+(.+?)(?:\s+most)?$", re.I),
]


def load_facts() -> dict[str, str]:
    if not FACTS_FILE.exists():
        return {}
    try:
        data = json.loads(FACTS_FILE.read_text())
        return {str(k).lower(): str(v).strip() for k, v in data.items() if v}
    except Exception:
        return {}


def save_facts(facts: dict[str, str]) -> None:
    FACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Normalize duplicate language keys → single canonical key
    lang_keys = {"programing_language", "programming_language", "language"}
    lang_vals = [facts[k] for k in lang_keys if k in facts]
    if lang_vals:
        canonical = lang_vals[-1]
        for k in lang_keys:
            facts.pop(k, None)
        facts["programming_language"] = canonical
    FACTS_FILE.write_text(json.dumps(facts, indent=2))


def extract_fact(text: str) -> tuple[str, str] | None:
    t = text.strip()
    for pat in _FACT_PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        if len(m.groups()) == 2:
            key, val = m.group(1).strip().lower(), m.group(2).strip().rstrip(".")
            key = re.sub(r"\s+", "_", key)
            if key and val and len(val) < 200:
                return key, val
        elif len(m.groups()) == 1:
            return "preference", m.group(1).strip().rstrip(".")
    return None


def remember_from_message(text: str) -> tuple[str, str] | None:
    """If message states a fact, save it. Returns (key, value) or None."""
    hit = extract_fact(text)
    if not hit:
        return None
    key, val = hit
    facts = load_facts()
    facts[key] = val
    save_facts(facts)
    return key, val


def facts_prompt_block() -> str:
    facts = load_facts()
    if not facts:
        return ""
    lines = [f"- {k.replace('_', ' ')}: {v}" for k, v in sorted(facts.items())]
    return "\n\nMike's stated facts (trust these over indexed code):\n" + "\n".join(lines)
