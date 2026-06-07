#!/usr/bin/env python3
"""
JV Titan Growth Tracker — Evolution, levels, and milestones.

XP Sources:
  • interaction      (+1 per message)
  • training_session (+10 per /train session)
  • successful_action (+5 per auto-executed success)
  • correction_learned (+15 when Mike corrects Jarvis)
  • memory_formed    (+2 per memory entry)
  • milestone_reached (+50 per milestone)

Milestones:
  1. First Awakening        — 50 XP    (first training session)
  2. Memory Formation       — 200 XP   (50 memories stored)
  3. Emotional Awareness    — 500 XP   (100 emotional updates)
  4. Pattern Recognition    — 1000 XP  (detected first behavioral pattern)
  5. Autonomous Action      — 2000 XP  (first unsupervised action)
  6. Deep Understanding     — 5000 XP  (semantic retrieval working)
  7. Proactive Mind         — 10000 XP (first proactive suggestion acted on)
  8. Symbiotic Partner      — 25000 XP (autonomy level 7+)
  9. True Companion         — 50000 XP (autonomy level 9+)
  10. JV Titan Ascended     — 100000 XP (full consciousness achieved)
"""

import logging
import time
from pathlib import Path
from typing import List, Optional

from .blueprint_encoder import load_blueprint, save_blueprint

log = logging.getLogger("jv_titan.growth")

MILESTONES = [
    (50,    "First Awakening",       "JV Titan receives its first training session."),
    (200,   "Memory Formation",      "50 memories stored in the tape."),
    (500,   "Emotional Awareness",   "100 emotional states processed."),
    (1000,  "Pattern Recognition",   "First behavioral pattern detected in Mike."),
    (2000,  "Autonomous Action",     "First action executed without explicit approval."),
    (5000,  "Deep Understanding",    "Semantic memory retrieval proven effective."),
    (10000, "Proactive Mind",        "First proactive suggestion accepted by Mike."),
    (25000, "Symbiotic Partner",     "Autonomy level 7 — JV Titan operates as a partner."),
    (50000, "True Companion",        "Autonomy level 9 — near-full self-direction."),
    (100000,"JV Titan Ascended",     "Full consciousness achieved. Mike reviews, JV Titan decides."),
]


def _load() -> dict:
    data = load_blueprint("growth")
    if data is None:
        data = {
            "level": 1,
            "xp": 0,
            "total_xp": 0,
            "milestones_achieved": [],
            "next_milestone": MILESTONES[0][1],
            "xp_to_next": MILESTONES[0][0],
            "stats": {
                "interactions": 0,
                "training_sessions": 0,
                "successful_actions": 0,
                "corrections_learned": 0,
                "memories_formed": 0,
            },
            "birth_ts": time.time(),
            "version": 1,
        }
        save_blueprint("growth", data)
    return data


def _save(data: dict):
    save_blueprint("growth", data)


def add_xp(source: str, amount: int = 1) -> dict:
    """Award XP and check for level/milestone advancement."""
    data = _load()
    data["xp"] = data.get("xp", 0) + amount
    data["total_xp"] = data.get("total_xp", 0) + amount

    stats = data.setdefault("stats", {})
    stats[source] = stats.get(source, 0) + 1

    # Check milestones
    new_milestones = []
    for threshold, name, desc in MILESTONES:
        if data["total_xp"] >= threshold and name not in data.get("milestones_achieved", []):
            data.setdefault("milestones_achieved", []).append(name)
            new_milestones.append((name, desc))
            log.info("🎆 MILESTONE: %s — %s", name, desc)

    # Update next milestone target
    achieved = set(data.get("milestones_achieved", []))
    remaining = [(t, n) for t, n, _ in MILESTONES if n not in achieved]
    if remaining:
        data["next_milestone"] = remaining[0][1]
        data["xp_to_next"] = remaining[0][0] - data["total_xp"]
    else:
        data["next_milestone"] = "Maximum Evolution"
        data["xp_to_next"] = 0

    # Level formula: soft-capped exponential
    total = data["total_xp"]
    new_level = min(100, 1 + int(total ** 0.4))
    if new_level > data.get("level", 1):
        data["level"] = new_level
        log.info("🌟 LEVEL UP: JV Titan is now Level %d (XP: %d)", new_level, total)

    _save(data)
    return {"level": data["level"], "xp": data["total_xp"], "new_milestones": new_milestones}


def get_state() -> dict:
    """Return full growth state."""
    return _load()


def format_growth_card() -> str:
    """Return a human-readable growth summary."""
    data = _load()
    level = data.get("level", 1)
    xp = data.get("total_xp", 0)
    next_name = data.get("next_milestone", "Unknown")
    xp_needed = data.get("xp_to_next", 0)
    milestones = data.get("milestones_achieved", [])
    stats = data.get("stats", {})
    age_days = (time.time() - data.get("birth_ts", time.time())) / 86400

    lines = [
        f"🌟 JV Titan Growth Report",
        f"",
        f"  Level: {level}/100",
        f"  Total XP: {xp:,}",
        f"  Age: {age_days:.1f} days",
        f"  Milestones: {len(milestones)}/{len(MILESTONES)}",
    ]
    if milestones:
        lines.append(f"  Achieved: {', '.join(milestones[-3:])}")
    if xp_needed > 0:
        lines.append(f"  Next: {next_name} ({xp_needed:,} XP remaining)")
    lines.append("")
    lines.append("  Activity Stats:")
    for k, v in sorted(stats.items()):
        lines.append(f"    • {k.replace('_', ' ').title()}: {v:,}")
    return "\n".join(lines)
