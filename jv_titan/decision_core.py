#!/usr/bin/env python3
"""
JV Titan Decision Core — Autonomous decision-making with confidence scoring.

Tracks:
  • autonomy_level        (0-10)     — current autonomy tier
  • confidence_threshold  (0.0-1.0)  — min confidence to act without asking
  • decision_log          (list)     — all decisions with outcomes
  • pending_decisions     (list)     — decisions awaiting Mike's input
  • proactive_suggestions (list)     — things JV Titan thinks Mike should know

Autonomy Tiers:
  0-2  → Observer      (only suggests, never acts)
  3-4  → Assistant     (acts on pre-approved items only)
  5-6  → Partner       (acts on trusted items, asks for novel)
  7-8  → Delegate      (acts independently, reports after)
  9-10 → Autonomous    (full self-direction, Mike reviews periodically)
"""

import logging
import time
from pathlib import Path
from typing import List, Optional

from .blueprint_encoder import load_blueprint, save_blueprint

log = logging.getLogger("jv_titan.decision")

TIER_NAMES = {
    0: "Observer", 1: "Observer", 2: "Observer",
    3: "Assistant", 4: "Assistant",
    5: "Partner", 6: "Partner",
    7: "Delegate", 8: "Delegate",
    9: "Autonomous", 10: "Autonomous",
}


def _load() -> dict:
    data = load_blueprint("decisions")
    if data is None:
        data = {
            "autonomy_level": 2,
            "confidence_threshold": 0.85,
            "decision_log": [],
            "pending_decisions": [],
            "proactive_suggestions": [],
            "successful_patterns": {},
            "failed_patterns": {},
            "version": 1,
        }
        save_blueprint("decisions", data)
    return data


def _save(data: dict):
    save_blueprint("decisions", data)


def get_autonomy_tier() -> tuple[int, str]:
    """Return (level, name) of current autonomy."""
    level = _load().get("autonomy_level", 2)
    return level, TIER_NAMES.get(level, "Observer")


def can_act_without_approval(action_type: str, confidence: float) -> bool:
    """Check if JV Titan can autonomously execute an action."""
    data = _load()
    level = data.get("autonomy_level", 2)
    threshold = data.get("confidence_threshold", 0.85)

    if level <= 2:
        return False
    if level <= 4:
        # Assistant: only pre-approved action types
        pre_approved = {"cpu_info", "ram_usage", "disk_space", "gpu_status",
                        "pm2_status", "nginx_status", "sysinfo", "health"}
        return action_type in pre_approved and confidence >= threshold
    if level <= 6:
        # Partner: pre-approved + learned-safe
        pre_approved = {"cpu_info", "ram_usage", "disk_space", "gpu_status",
                        "pm2_status", "nginx_status", "sysinfo", "health",
                        "service_restart", "pm2_restart"}
        return action_type in pre_approved and confidence >= threshold
    if level <= 8:
        # Delegate: most safe actions
        blocked = {"reboot", "shell", "ssh_cmd", "deploy_vps", "docker_run",
                   "nmap_full", "nikto_scan", "wpscan"}
        return action_type not in blocked and confidence >= threshold
    # Autonomous: almost everything
    blocked = {"reboot", "shell", "ssh_cmd"}
    return action_type not in blocked and confidence >= 0.6


def log_decision(
    decision: str,
    action_type: str,
    confidence: float,
    auto_executed: bool,
    outcome: str = "",
    approved_by_mike: bool = False,
):
    """Record a decision in the log."""
    data = _load()
    entry = {
        "ts": time.time(),
        "decision": decision,
        "action_type": action_type,
        "confidence": confidence,
        "auto_executed": auto_executed,
        "outcome": outcome,
        "approved": approved_by_mike,
    }
    log_list = data.get("decision_log", [])
    log_list.append(entry)
    data["decision_log"] = log_list[-500:]  # keep last 500

    # Learn from outcomes
    patterns = data.setdefault("successful_patterns" if outcome == "success" else "failed_patterns", {})
    patterns[action_type] = patterns.get(action_type, 0) + 1

    _save(data)


def add_suggestion(text: str, priority: int = 5, category: str = "general"):
    """Add a proactive suggestion for Mike."""
    data = _load()
    sugg = data.setdefault("proactive_suggestions", [])
    sugg.append({
        "ts": time.time(),
        "text": text,
        "priority": priority,
        "category": category,
        "shown": False,
    })
    data["proactive_suggestions"] = sugg[-50:]  # keep last 50
    _save(data)
    log.info("Suggestion queued [%s|%d]: %s", category, priority, text[:60])


def get_pending_suggestions(n: int = 3, mark_shown: bool = True) -> List[dict]:
    """Return top-N unseen proactive suggestions."""
    data = _load()
    sugg = data.get("proactive_suggestions", [])
    unseen = [s for s in sugg if not s.get("shown")]
    unseen.sort(key=lambda x: (-x.get("priority", 5), x.get("ts", 0)))
    top = unseen[:n]
    if mark_shown:
        for s in top:
            s["shown"] = True
        _save(data)
    return top


def advance_autonomy() -> tuple[int, str]:
    """Slowly advance autonomy based on successful decisions."""
    data = _load()
    level = data.get("autonomy_level", 2)
    if level >= 10:
        return level, TIER_NAMES[level]

    successes = len(data.get("successful_patterns", {}))
    total_decisions = len(data.get("decision_log", []))

    # Require minimum interactions and success ratio
    thresholds = {3: 20, 5: 100, 7: 500, 9: 2000}
    for target, min_decisions in thresholds.items():
        if level < target and total_decisions >= min_decisions:
            data["autonomy_level"] = target
            _save(data)
            log.info("Autonomy advanced: Level %d (%s)", target, TIER_NAMES[target])
            return target, TIER_NAMES[target]

    return level, TIER_NAMES.get(level, "Observer")


def format_decision_context() -> str:
    """Return decision-making context for prompt injection."""
    level, name = get_autonomy_tier()
    data = _load()
    threshold = data.get("confidence_threshold", 0.85)
    pending = len(data.get("pending_decisions", []))
    sugg = len([s for s in data.get("proactive_suggestions", []) if not s.get("shown")])

    lines = [
        f"[JV Titan Decision Core — Tier {level}: {name}]",
        f"Confidence threshold: {threshold:.0%} | Pending decisions: {pending} | Suggestions queued: {sugg}",
    ]
    if level <= 4:
        lines.append("I suggest actions but wait for Sir's approval.")
    elif level <= 6:
        lines.append("I act on trusted patterns and ask for anything uncertain.")
    elif level <= 8:
        lines.append("I act independently and report outcomes.")
    else:
        lines.append("I operate with full autonomy. Sir reviews periodically.")
    return "\n".join(lines)
