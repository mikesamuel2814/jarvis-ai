#!/usr/bin/env python3
"""
Jarvis Skillset Engine — persistent rule, skill, autonomy, and pattern memory.

Every learned rule, approval pattern, and decision is recorded here.
This is the single source of truth for Jarvis's autonomous behavior.

Persists to ~/.jarvis/data/skillset.json
"""

import copy
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
SKILLSET_FILE = JARVIS_HOME / "data" / "skillset.json"
LOG_FILE = JARVIS_HOME / "logs" / "skillset.log"

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [skillset] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


DEFAULT_SKILLSET = {
    "version": 1,
    "rules": [],
    "skills": {},
    "autonomy": {
        "pre_approved": ["restart_gateway", "restart_starline", "nginx_status", "pm2_status", "disk", "memory", "gpu"],
        "always_ask": ["reboot", "update_system", "deploy_vps", "shell", "ssh_cmd"],
        "learned_safe": [],
        "approval_counts": {},
    },
    "patterns": {
        "after_deploy": ["pm2_status", "nginx_status"],
        "morning_check": ["sysinfo", "pm2_status"],
        "sequences": [],
    },
    "skill_gaps": [],
    "metadata": {
        "created": datetime.utcnow().isoformat(),
        "last_updated": datetime.utcnow().isoformat(),
        "total_approvals": 0,
        "total_corrections": 0,
        "total_rules_learned": 0,
    },
}


def load() -> dict:
    """Load skillset from JSON. Returns deep-copied defaults if file missing."""
    if SKILLSET_FILE.exists():
        try:
            return json.loads(SKILLSET_FILE.read_text())
        except Exception as e:
            log.warning(f"Failed to load skillset: {e}. Using defaults.")
            return copy.deepcopy(DEFAULT_SKILLSET)
    return copy.deepcopy(DEFAULT_SKILLSET)


def save(data: dict) -> bool:
    """Atomically write skillset to JSON."""
    try:
        SKILLSET_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Update timestamp before writing so file reflects current time
        data.setdefault("metadata", {})["last_updated"] = datetime.utcnow().isoformat()
        # Write to temp file first, then atomic rename
        tmp = SKILLSET_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(SKILLSET_FILE)
        log.debug(f"Skillset saved: {len(data['rules'])} rules, {len(data['skills'])} skills")
        return True
    except Exception as e:
        log.error(f"Failed to save skillset: {e}")
        return False


def add_rule(rule_text: str, priority: int = 5, source: str = "unknown") -> bool:
    """
    Add a rule to the skillset. Deduplicates by exact rule text.
    Priority: 1-10, higher = always injected into prompts.
    """
    if not rule_text or not rule_text.strip():
        return False

    sk = load()
    rule_text = rule_text.strip()

    # Dedup: check if rule already exists (exact match)
    for r in sk["rules"]:
        if r["rule"].lower() == rule_text.lower():
            r["hits"] += 1
            r["ts"] = datetime.utcnow().isoformat()
            save(sk)
            log.info(f"Rule updated (hit count): {rule_text[:60]}")
            return True

    # Add new rule
    import uuid

    new_rule = {
        "id": str(uuid.uuid4())[:8],
        "rule": rule_text,
        "priority": min(10, max(1, priority)),
        "source": source,
        "hits": 1,
        "ts": datetime.utcnow().isoformat(),
    }
    sk["rules"].append(new_rule)
    sk["metadata"]["total_rules_learned"] += 1
    save(sk)
    log.info(f"Rule added (priority {priority}): {rule_text[:60]}")
    return True


def get_prompt_injection(query: str = "", n: int = 5) -> str:
    """
    Return top-N rules formatted for system prompt injection.
    If query provided, ranks rules by semantic relevance.
    """
    sk = load()
    rules = sk.get("rules", [])

    if not rules:
        return ""

    # Simple keyword matching if query provided
    if query:
        query_words = set(query.lower().split())
        scored = []
        for i, r in enumerate(rules):
            rule_words = set(r["rule"].lower().split())
            overlap = len(query_words & rule_words)
            score = (overlap * 2) + (r["priority"] / 10.0) + (r["hits"] / 100.0)
            scored.append((score, i, r))  # i breaks ties without comparing dicts
        scored.sort(key=lambda x: (-x[0], x[1]))
        top_rules = [r for _, _, r in scored[:n]]
    else:
        # No query: return highest-priority rules
        top_rules = sorted(rules, key=lambda r: (-r["priority"], -r["hits"]))[:n]

    if not top_rules:
        return ""

    lines = ["--- JARVIS LEARNED RULES (apply these) ---"]
    for r in top_rules:
        lines.append(f"• {r['rule']} [priority:{r['priority']}]")
    lines.append("--- END LEARNED RULES ---")

    return "\n".join(lines)


def get_skill_gaps_for_query(query: str) -> str:
    """Return enrichment text if query matches a skill gap topic."""
    sk = load()
    gaps = sk.get("skill_gaps", [])

    if not gaps:
        return ""

    query_lower = query.lower()
    for gap in gaps:
        topic = gap.get("topic", "").lower()
        if topic and topic in query_lower:
            enrichment = gap.get("enrichment", "")
            if enrichment:
                return f"--- SKILL CONTEXT ({gap['topic']}) ---\n{enrichment}\n--- END SKILL CONTEXT ---"

    return ""


def record_approval(action_name: str) -> None:
    """
    Record that Mike approved an action.
    After 3 approvals, promote to learned_safe (autonomy).
    """
    sk = load()
    aut = sk["autonomy"]

    # Skip if already in learned_safe
    if action_name in aut.get("learned_safe", []):
        return

    # Skip if in always_ask list (never autonomous)
    if action_name in aut.get("always_ask", []):
        return

    counts = aut.get("approval_counts", {})
    counts[action_name] = counts.get(action_name, 0) + 1
    aut["approval_counts"] = counts

    # Promote to learned_safe after 3 approvals
    if counts[action_name] >= 3:
        learned_safe = aut.get("learned_safe", [])
        if action_name not in learned_safe:
            learned_safe.append(action_name)
            aut["learned_safe"] = learned_safe
            log.info(f"Action promoted to learned_safe: {action_name} (after 3 approvals)")

    sk["autonomy"] = aut
    sk["metadata"]["total_approvals"] += 1
    save(sk)


def get_autonomy_decision(action_name: str) -> tuple[bool, str]:
    """
    Decide whether to auto-execute an action without approval.
    Returns (should_auto_execute, reason).
    """
    sk = load()
    aut = sk["autonomy"]

    # Always require approval for dangerous actions
    if action_name in aut.get("always_ask", []):
        return False, f"Action {action_name} always requires explicit approval"

    # Pre-approved actions: auto-execute
    if action_name in aut.get("pre_approved", []):
        return True, f"Pre-approved action: {action_name}"

    # Learned-safe actions: auto-execute after 3× approvals
    if action_name in aut.get("learned_safe", []):
        return True, f"Learned safe after {aut['approval_counts'].get(action_name, 3)} approvals: {action_name}"

    return False, f"Action {action_name} not in autonomy map"


def update_skill(action_name: str, success: bool, confidence_delta: float = 0.05) -> None:
    """Update skill confidence based on execution outcome."""
    sk = load()
    skills = sk.get("skills", {})

    if action_name not in skills:
        skills[action_name] = {"confidence": 0.5, "usage": 0, "success": 0, "desc": action_name}

    skills[action_name]["usage"] += 1
    if success:
        skills[action_name]["success"] = skills[action_name].get("success", 0) + 1
        # Increase confidence
        skills[action_name]["confidence"] = min(
            1.0, skills[action_name]["confidence"] + confidence_delta
        )
    else:
        # Decrease confidence
        skills[action_name]["confidence"] = max(
            0.0, skills[action_name]["confidence"] - confidence_delta
        )

    sk["skills"] = skills
    save(sk)


def detect_and_store_pattern(action_sequence: list[str]) -> None:
    """Learn action chains from execution sequences."""
    if len(action_sequence) < 2:
        return

    sk = load()
    patterns = sk.get("patterns", {})
    sequences = patterns.get("sequences", [])

    # Check if sequence already exists
    for seq in sequences:
        if seq == action_sequence:
            return

    sequences.append(action_sequence)
    patterns["sequences"] = sequences[:50]  # Keep last 50
    sk["patterns"] = patterns
    save(sk)

    log.info(f"Pattern learned: {' -> '.join(action_sequence)}")


def extract_and_store_rule_from_correction(query: str, correction: str) -> None:
    """
    Extract a rule from a user correction.
    Uses DeepSeek-R1:7b locally (free, fast, private).
    Falls back to heuristic if model fails.
    """
    if not query or not correction:
        return

    # Try DeepSeek rule extraction
    system_prompt = "Extract 1-3 concise rules from a correction. Format: 'When X, always Y'. No preamble, rules only."
    user_prompt = f'Query: "{query}"\nCorrection: "{correction}"\n\nRules:'

    try:
        import ollama

        client = ollama.Client(host="http://localhost:11434")
        resp = client.generate(
            model="deepseek-r1:7b",
            prompt=user_prompt,
            system=system_prompt,
            stream=False,
        )
        rule_text = (resp.get("response", "") or "").strip()
        if rule_text and len(rule_text) > 10:
            # Split multiple rules (separated by newlines)
            for line in rule_text.split("\n"):
                line = line.strip()
                if line and len(line) > 10:
                    add_rule(line, priority=8, source="correction")
            sk = load()
            sk["metadata"]["total_corrections"] += 1
            save(sk)
            log.info(f"Rule extracted from correction: {rule_text[:80]}")
            return
    except Exception as e:
        log.debug(f"DeepSeek extraction failed: {e}. Using heuristic.")

    # Heuristic fallback: simple pattern extraction
    if "always" in correction.lower():
        add_rule(f"When asked '{query[:40]}...', {correction[:80]}", priority=6, source="correction")
    else:
        add_rule(f"{correction[:100]}", priority=5, source="correction")


def test_load_save() -> bool:
    """Self-test: load and save skillset."""
    print("Test 1: Load default skillset...")
    sk_before = load()
    assert "rules" in sk_before, "Missing 'rules' key"
    assert "autonomy" in sk_before, "Missing 'autonomy' key"
    before_count = len(sk_before["rules"])
    print(f"  ✓ Loaded {before_count} rules, {len(sk_before['skills'])} skills")

    print("Test 2: Add a unique test rule...")
    import time
    unique_rule = f"Test rule {int(time.time())} for validation"
    add_rule(unique_rule, priority=5, source="test")
    sk_after = load()
    after_count = len(sk_after["rules"])
    assert after_count == before_count + 1, f"Rule not added: {before_count} -> {after_count}"
    print(f"  ✓ Rule added, now {after_count} total")

    print("Test 3: Get prompt injection...")
    inj = get_prompt_injection("test query", n=3)
    assert "LEARNED RULES" in inj, "Injection format incorrect"
    print(f"  ✓ Injection format OK: {len(inj)} chars")

    print("Test 4: Autonomy decision...")
    auto, reason = get_autonomy_decision("restart_gateway")
    assert auto == True, "restart_gateway should be pre-approved"
    print(f"  ✓ {reason}")

    auto, reason = get_autonomy_decision("reboot")
    assert auto == False, "reboot should require approval"
    print(f"  ✓ {reason}")

    print("\n✅ All tests passed")
    return True


if __name__ == "__main__":
    test_load_save()
