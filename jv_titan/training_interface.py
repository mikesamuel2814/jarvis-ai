#!/usr/bin/env python3
"""
JV Titan Training Interface — Process Mike's life history and activities.

Handles:
  • /train sessions — free-form text about past/present/future
  • Automatic fact extraction — names, dates, preferences, events
  • Memory tape recording — categorized by temporal axis
  • Persona updates — preference learning
  • Growth XP — rewards training with experience points

Training Categories:
  past    — "I grew up in Dhaka", "My first company was..."
  present — "Currently working on AsthaCash", "I prefer vim"
  future  — "I want to build a house", "Plan: deploy by Friday"
"""

import logging
import re
import time
from typing import List, Optional

from . import memory_tape, consciousness, persona, emotion_engine, growth_tracker

log = logging.getLogger("jv_titan.training")

# Simple keyword-based category detection
PAST_KEYWORDS = [
    "grew up", "childhood", "born", "school", "university", "first job",
    "used to", "back then", "in the past", "when i was", "my parents",
    "my family", "i learned", "i started", "years ago", "previously",
]

FUTURE_KEYWORDS = [
    "plan", "going to", "will", "want to", "goal", "target", "deadline",
    "next week", "next month", "next year", "soon", "eventually",
    "aspire", "dream", "hope to", "intend", "schedule", "roadmap",
]

PREFERENCE_PATTERNS = [
    (r"i (?:like|love|prefer|enjoy|hate|dislike|can't stand)\s+(.+)", "preference"),
    (r"my favorite\s+(.+)", "preference"),
    (r"i always\s+(.+)", "habit"),
    (r"i never\s+(.+)", "habit"),
    (r"call me\s+(.+)", "identity"),
    (r"my (?:name|email|phone|address)\s+is\s+(.+)", "identity"),
    (r"i work (?:at|for|on)\s+(.+)", "work"),
    (r"my (?:birthday|birth date)\s+is\s+(.+)", "identity"),
]


def _detect_category(text: str) -> str:
    """Detect temporal category from text."""
    t = text.lower()
    past_score = sum(1 for kw in PAST_KEYWORDS if kw in t)
    future_score = sum(1 for kw in FUTURE_KEYWORDS if kw in t)

    if past_score > future_score and past_score > 0:
        return "past"
    if future_score > past_score and future_score > 0:
        return "future"
    return "present"


def _extract_facts(text: str) -> List[dict]:
    """Extract structured facts from training text."""
    facts = []
    for pattern, fact_type in PREFERENCE_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            facts.append({
                "type": fact_type,
                "value": match.group(1).strip().rstrip("."),
                "context": text[max(0, match.start()-30):match.end()+30],
            })
    return facts


def process_training_input(
    text: str,
    source: str = "mike",
    explicit_category: Optional[str] = None,
) -> dict:
    """
    Process a training input from Mike.

    Returns:
        {
            "memories_added": int,
            "facts_extracted": int,
            "category": str,
            "xp_gained": int,
            "summary": str,
        }
    """
    if not text or not text.strip():
        return {"memories_added": 0, "facts_extracted": 0, "category": "present", "xp_gained": 0, "summary": "Empty input."}

    category = explicit_category or _detect_category(text)
    facts = _extract_facts(text)

    # Add the main memory
    memory_tape.add_memory(
        content=text.strip(),
        category=category,
        source=source,
        importance=7 if category == "past" else 6,
        tags=["training", category] + [f["type"] for f in facts],
    )

    # Add individual facts as separate memories
    for fact in facts:
        memory_tape.add_memory(
            content=f"{fact['type'].title()}: {fact['value']}",
            category=category,
            source=source,
            importance=8,
            tags=["fact", fact["type"], category],
        )

    # Update persona with preferences
    for fact in facts:
        if fact["type"] == "preference":
            persona.update_preference(fact["value"], True)
        elif fact["type"] == "habit":
            persona.update_preference(f"habit:{fact['value']}", True)
        elif fact["type"] == "identity":
            consciousness.update_mike_model("identity_notes", [fact["value"]])
        elif fact["type"] == "work":
            consciousness.update_mike_model("work_history", [fact["value"]])

    # Deepen relationship
    persona.deepen_relationship(0.3)

    # Award XP
    xp_result = growth_tracker.add_xp("training_session", amount=10 + len(facts) * 2)

    # Update emotion
    emotion_engine.update(text=text)

    summary = (
        f"Recorded {1 + len(facts)} memory entries ({category}). "
        f"Extracted {len(facts)} facts. "
        f"Gained {xp_result['xp']} XP (Level {xp_result['level']})."
    )
    if xp_result["new_milestones"]:
        summary += f" Milestone: {xp_result['new_milestones'][0][0]}!"

    log.info("Training processed: %s", summary)
    return {
        "memories_added": 1 + len(facts),
        "facts_extracted": len(facts),
        "category": category,
        "xp_gained": xp_result["xp"],
        "summary": summary,
    }


def process_interaction(text: str, action_taken: str = "", outcome: str = ""):
    """Silently process a normal interaction for growth and memory."""
    # Extract any implicit facts
    facts = _extract_facts(text)
    for fact in facts:
        memory_tape.add_memory(
            content=f"{fact['type'].title()}: {fact['value']}",
            category="present",
            source="mike",
            importance=5,
            tags=["implicit", fact["type"]],
        )
        if fact["type"] == "preference":
            persona.update_preference(fact["value"], True)

    # Growth
    growth_tracker.add_xp("interaction", amount=1)
    if outcome == "success":
        growth_tracker.add_xp("successful_action", amount=5)

    # Emotion
    emotion_engine.update(text=text, action_taken=action_taken, outcome=outcome)

    # Consciousness
    consciousness.touch()
    consciousness.advance_relationship_stage()

    # Persona
    persona.deepen_relationship(0.1)

    # Decision core advancement
    from . import decision_core
    decision_core.advance_autonomy()


def get_training_summary() -> str:
    """Return a summary of what JV Titan has learned so far."""
    growth = growth_tracker.get_state()
    memories = memory_tape.list_memory_tape_dates()
    mm = consciousness.get_mike_model()
    prefs = persona._load().get("mike_preferences", {})

    lines = [
        "📚 JV Titan Training Summary",
        "",
        f"  Total XP: {growth.get('total_xp', 0):,} | Level: {growth.get('level', 1)}",
        f"  Memory days recorded: {len(memories)}",
        f"  Milestones: {len(growth.get('milestones_achieved', []))}",
        "",
        "  Mike's Known Preferences:",
    ]
    for k, v in sorted(prefs.items())[:10]:
        lines.append(f"    • {k}: {str(v)[:50]}")

    lines.append("")
    lines.append("  Known Projects:")
    for proj in mm.get("projects", []):
        lines.append(f"    • {proj}")

    traits = mm.get("traits", [])
    if traits:
        lines.append("")
        lines.append(f"  Observed Traits: {', '.join(traits)}")

    return "\n".join(lines)
