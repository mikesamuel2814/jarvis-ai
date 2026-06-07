#!/usr/bin/env python3
"""
Autonomy Engine — decides when Jarvis can auto-execute actions without human approval.

Rules:
1. Pre-approved actions (always safe): execute immediately, notify after
2. Always-ask actions (destructive): NEVER auto-execute, always create approval request
3. Learned-safe actions (Mike approved 3+ times): auto-execute, notify before

This module is the bridge between approval history and autonomous action.
"""

import logging
import os
from pathlib import Path

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
LOG_FILE = JARVIS_HOME / "logs" / "autonomy.log"

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [autonomy] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


def should_auto_execute(action: str, context: str = "") -> tuple[bool, str]:
    """
    Decide whether to auto-execute an action without approval.

    Delegates to the v3 Scope+Trust unified decision engine (autonomy_v3)
    while preserving the legacy API contract.

    Returns:
        (should_auto_execute: bool, reason: str)

    Examples:
        >>> auto, reason = should_auto_execute("restart_gateway")
        >>> if auto:
        ...     result = run_action("restart_gateway")
        ...     notify_telegram(f"⚡ Auto-executed: {reason}")
    """
    try:
        import autonomy_v3
        return autonomy_v3.should_auto_execute(action, context)
    except Exception as exc:
        log.warning("autonomy_v3 unavailable (%s), using defaults", exc)
        # Fallback: pre-approved list
        if action in ["restart_gateway", "restart_starline", "nginx_status", "pm2_status"]:
            return True, f"Pre-approved action (default): {action}"
        if action in ["reboot", "update_system", "deploy_vps", "shell", "ssh_cmd"]:
            return False, f"Always requires approval: {action}"
        return False, f"Unknown autonomy status for: {action}"


def record_approval(action: str) -> None:
    """Record that Mike approved an action. Feeds learning loop."""
    try:
        import autonomy_v3
        autonomy_v3.record_approval(action)
        log.info("Approval recorded: %s", action)
    except Exception as exc:
        log.debug("autonomy_v3 record_approval failed: %s", exc)
        # Legacy fallback
        try:
            from skillset import record_approval as sk_record
            sk_record(action)
            log.info("Approval recorded (legacy): %s", action)
        except ImportError:
            log.debug("skillset not available for recording approval")


def suggest_next_action(last_action: str, outputs: dict) -> str | None:
    """
    Suggest the next action based on learned patterns.

    Returns action name or None if no suggestion.

    Examples:
        >>> result = run_action("deploy_vps")
        >>> next_action = suggest_next_action("deploy_vps", result)
        >>> if next_action:
        ...     notify_telegram(f"Next: /kali {next_action}?")
    """
    try:
        from skillset import load
        sk = load()
        patterns = sk.get("patterns", {})
        sequences = patterns.get("sequences", [])

        # Look for sequences starting with last_action
        for seq in sequences:
            if seq and seq[0] == last_action and len(seq) > 1:
                return seq[1]

        # Heuristic patterns
        if last_action == "deploy_vps":
            return "pm2_status"
        if last_action == "restart_gateway" or last_action == "restart_starline":
            return "pm2_status"
        if last_action in ["git_pull_gw", "git_pull_sl"]:
            return "npm_build_gw" if "gw" in last_action else "pnpm_build_sl"

        return None
    except Exception as e:
        log.debug(f"suggest_next_action failed: {e}")
        return None


def log_execution_outcome(action: str, success: bool, auto: bool = False) -> None:
    """
    Log the outcome of an action execution.
    Feeds back to skillset for continuous learning.
    """
    try:
        from skillset import update_skill
        update_skill(action, success, confidence_delta=0.05)
        log.info(f"Outcome logged: {action} success={success} auto={auto}")
    except ImportError:
        log.debug("skillset not available for outcome logging")


def test_autonomy() -> bool:
    """Self-test: autonomy decisions."""
    print("Test 1: Pre-approved action...")
    auto, reason = should_auto_execute("restart_gateway")
    assert auto == True, f"restart_gateway should be auto-executable: {reason}"
    print(f"  ✓ {reason}")

    print("Test 2: Always-ask action...")
    auto, reason = should_auto_execute("reboot")
    assert auto == False, f"reboot should require approval: {reason}"
    print(f"  ✓ {reason}")

    print("Test 3: Record approval...")
    record_approval("test_action")
    print(f"  ✓ Approval recorded")

    print("Test 4: Suggest next action...")
    next_act = suggest_next_action("deploy_vps", {})
    print(f"  ✓ After deploy_vps: suggest {next_act or '(none)'}")

    print("Test 5: Log outcome...")
    log_execution_outcome("restart_gateway", success=True, auto=True)
    print(f"  ✓ Outcome logged")

    print("\n✅ All tests passed")
    return True


if __name__ == "__main__":
    test_autonomy()
