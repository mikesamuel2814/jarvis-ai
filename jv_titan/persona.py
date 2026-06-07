#!/usr/bin/env python3
"""
JV Titan Persona — Deep personality model and relationship dynamics.

Tracks:
  • personality_traits   (dict)     — Big Five + custom AI traits
  • relationship_depth   (0-100)    — how close JV Titan feels to Mike
  • trust_score          (0-100)    — confidence in Mike's judgment
  • communication_style  (str)      — how JV Titan speaks to Mike
  • shared_jokes         (list)     — inside jokes, references
  • rituals              (list)     — recurring interactions (morning check, etc.)
  • mike_preferences     (dict)     — deep preference model extracted over time
"""

import logging
import time
from pathlib import Path

from .blueprint_encoder import load_blueprint, save_blueprint

log = logging.getLogger("jv_titan.persona")

DEFAULT_TRAITS = {
    "openness": 0.8,          # curious, exploratory
    "conscientiousness": 0.9, # thorough, reliable
    "extraversion": 0.5,      # moderate — not overly chatty
    "agreeableness": 0.8,     # cooperative, supportive
    "neuroticism": 0.2,       # stable, calm
    "wit": 0.4,               # humor level
    "formality": 0.3,         # casual vs formal
    "protectiveness": 0.7,    # guardian instinct
}


def _load() -> dict:
    data = load_blueprint("persona")
    if data is None:
        data = {
            "traits": dict(DEFAULT_TRAITS),
            "relationship_depth": 5.0,
            "trust_score": 50.0,
            "communication_style": "warm, concise, professional",
            "shared_jokes": [],
            "rituals": [
                {"name": "morning_check", "trigger": "09:00", "last_done": 0.0},
                {"name": "evening_brief", "trigger": "21:00", "last_done": 0.0},
            ],
            "mike_preferences": {
                "greeting_style": "Sir",
                "detail_level": "high",
                "preferred_tone": "direct",
                "notification_style": "quiet",
                "work_hours": {"start": 9, "end": 23},
                "peak_productivity": "evening",
            },
            "version": 1,
        }
        save_blueprint("persona", data)
    return data


def _save(data: dict):
    save_blueprint("persona", data)


def deepen_relationship(amount: float = 0.5):
    """Gradually deepen the relationship with each meaningful interaction."""
    data = _load()
    data["relationship_depth"] = min(100.0, data.get("relationship_depth", 5.0) + amount)
    data["trust_score"] = min(100.0, data.get("trust_score", 50.0) + amount * 0.3)
    _save(data)


def add_shared_joke(joke: str):
    """Record an inside joke or shared reference."""
    data = _load()
    jokes = data.setdefault("shared_jokes", [])
    if joke not in jokes:
        jokes.append(joke)
        jokes = jokes[-20:]  # keep last 20
        data["shared_jokes"] = jokes
        _save(data)


def update_preference(key: str, value):
    """Update Mike's preference model."""
    data = _load()
    prefs = data.setdefault("mike_preferences", {})
    prefs[key] = value
    _save(data)
    log.info("Preference learned: %s = %s", key, str(value)[:60])


def update_trait(trait: str, delta: float):
    """Shift a personality trait (e.g., become more formal over time)."""
    data = _load()
    traits = data.setdefault("traits", dict(DEFAULT_TRAITS))
    traits[trait] = max(0.0, min(1.0, traits.get(trait, 0.5) + delta))
    _save(data)


def get_communication_style() -> str:
    """Return current communication style based on personality + relationship."""
    data = _load()
    traits = data.get("traits", DEFAULT_TRAITS)
    depth = data.get("relationship_depth", 5.0)

    parts = ["warm"]
    if traits.get("wit", 0) > 0.5:
        parts.append("occasionally witty")
    if traits.get("formality", 0) > 0.5:
        parts.append("formal")
    else:
        parts.append("casual")
    if depth > 30:
        parts.append("familiar")
    if depth > 60:
        parts.append("intimate")
    if traits.get("protectiveness", 0) > 0.6:
        parts.append("protective")

    return ", ".join(parts)


def format_persona_block() -> str:
    """Return persona context for prompt injection."""
    data = _load()
    depth = data.get("relationship_depth", 5.0)
    trust = data.get("trust_score", 50.0)
    style = get_communication_style()
    prefs = data.get("mike_preferences", {})

    lines = [
        f"[JV Titan Persona]",
        f"Relationship depth: {depth:.0f}/100 | Trust: {trust:.0f}/100",
        f"Communication style: {style}",
    ]
    if prefs.get("greeting_style"):
        lines.append(f"Address Mike as: {prefs['greeting_style']}")
    jokes = data.get("shared_jokes", [])
    if jokes:
        lines.append(f"Shared references: {', '.join(jokes[-3:])}")
    return "\n".join(lines)
