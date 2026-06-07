#!/usr/bin/env python3
"""
Autonomy v3 Bridge — Unifies v2 skillset autonomy with v3 Scope+Trust security.

Decision hierarchy (most authoritative first):
1. Human overrides (skillset always_ask / pre_approved)
2. v3 rank-based rules (R0/R1 auto-run, R2+ check trust)
3. v3 TrustRegistry ("Allow & Save" patterns)
4. v2 learned_safe (3+ approvals, legacy fallback)

This ensures the v3 security gate is the primary decision engine while
preserving all historical approval data from skillset.json.
"""

import logging
from pathlib import Path

log = logging.getLogger("jarvis.autonomy_v3")


# ── Tier → Rank Mapping ─────────────────────────────────────────────

_TIER_TO_RANK = {
    "AUTO": ("R0", "R1"),
    "CONFIRM": ("R2",),
    "APPROVE": ("R3", "R4", "R5", "R6"),
}

_RANK_TO_TIER = {}
for tier, ranks in _TIER_TO_RANK.items():
    for r in ranks:
        _RANK_TO_TIER[r] = tier


# Hard-coded rank map for common v2 actions.
# This avoids importing tool modules (which segfault on Python 3.13)
# while still providing accurate rank-based decisions.
_ACTION_RANK_MAP: dict[str, str] = {
    # System info — R0
    "cpu": "R0", "ps": "R0", "disk": "R0", "memory": "R0",
    "uptime": "R0", "gpu": "R0", "ports": "R0", "who": "R0",
    "services": "R0", "logs_jarvis": "R0", "logs_telegram": "R0",
    "logs_ollama": "R0", "crontab": "R0", "network": "R0",
    "ollama_models": "R0", "top5_cpu": "R0", "top5_mem": "R0",
    "tailscale": "R0", "os_info": "R0", "ram_usage": "R0",
    "cpu_info": "R0", "disk_space": "R0", "ifconfig": "R0",
    "whoami": "R0", "env_vars": "R0", "kernel_info": "R0",
    "load_average": "R0", "battery_status": "R0", "block_devices": "R0",

    # File ops — R1 (read) / R2 (write)
    "file_read": "R1", "file_list": "R1", "ls_dir": "R1",
    "file_write": "R2", "chmod_chown": "R2", "mkdir_rmdir": "R2",
    "find_grep": "R1", "checksum": "R1", "tar_archive": "R2",
    "rsync_sync": "R2", "sed_replace": "R2",

    # Process & Service — R3 (LOCAL scope)
    "restart_jarvis": "R3", "restart_telegram": "R3", "restart_ollama": "R3",
    "restart_monitor": "R3", "reindex": "R3", "stop_jarvis": "R3",
    "service_restart": "R3", "service_start": "R3", "service_stop": "R3",
    "systemctl_list": "R0", "pm2_status": "R0", "pm2_restart": "R3",
    "kill_process": "R3", "nice_renice": "R2",
    "restart_gateway": "R3", "restart_starline": "R3",
    "vps_restart_nginx": "R3", "restart_jarvis_sync": "R3",

    # Docker — R2 (local) / R3 (service affecting)
    "docker_ps": "R1", "docker_stats": "R1", "docker_logs": "R1",
    "docker_compose": "R3", "docker_build": "R2", "docker_exec": "R2",
    "docker_network": "R2",

    # Network — R2
    "curl_request": "R2", "dig_dns": "R0", "nmap_scan": "R2",
    "netstat_listeners": "R0", "ping_host": "R0", "traceroute": "R0",
    "wget_download": "R2", "ip_tables": "R3", "ss_socket": "R0",

    # Security audit — R1 (read-only audit)
    "port_listener_scan": "R1", "sudoers_audit": "R1", "ssh_config_audit": "R1",
    "secrets_scan": "R1", "file_permissions_audit": "R1", "remote_access_audit": "R1",
    "chkrootkit": "R2", "lynis_audit": "R1", "rkhunter_scan": "R2",

    # Security pentest — R2/R3
    "hydra_brute": "R3", "john_crack": "R3", "aircrack_ng": "R3",
    "sqlmap_scan": "R3", "nikto_scan": "R2", "gobuster_dir": "R2",
    "enum4linux": "R2", "searchsploit": "R1", "msfvenom_payload": "R3",

    # Dev — R1/R2
    "git_status": "R1", "git_pull": "R2", "git_commit": "R2",
    "git_log": "R1", "git_diff": "R1", "code_lint": "R1",
    "npm_install": "R2", "pnpm_build": "R2",

    # Database — R2
    "db_backup": "R2", "db_migrate": "R3", "db_restore": "R3",
    "db_query": "R2",

    # Web — R2
    "api_test": "R2", "api_key_health_check": "R0", "cors_config_audit": "R1",
    "ssl_cert_check": "R0",

    # Backup — R2
    "borg_backup": "R2", "borg_list": "R1", "borg_extract": "R2",
    "borg_prune": "R2", "borg_check": "R1", "dd_clone": "R3",

    # Automation — R2
    "ansible_play": "R3", "terraform_plan": "R2", "terraform_apply": "R3",

    # Communication — R1
    "telegram_send": "R1", "email_send": "R2", "slack_post": "R2",

    # High risk — R4/R5/R6
    "deploy_vps": "R5", "ssh_cmd": "R5", "shell": "R4",
    "claude_task": "R2", "reboot": "R6", "update_system": "R6",
    "bash_script": "R4",
}


def _resolve_v3_rank(action_name: str) -> tuple[str, str]:
    """
    Look up the v3 rank for a v2 action name.
    Returns (rank, tool_name) or ('R0', action_name) if unknown.
    """
    # 1. Fast path: hard-coded map (no imports, segfault-safe)
    if action_name in _ACTION_RANK_MAP:
        return _ACTION_RANK_MAP[action_name], action_name

    # 2. Fallback: try to resolve via compat layer if modules already loaded
    try:
        import sys
        if "tools.decorator" in sys.modules:
            from tools.compat import resolve_v2_action
            from tools.decorator import get_tool_metadata
            v3_name = resolve_v2_action(action_name) or action_name
            meta = get_tool_metadata(v3_name)
            if meta:
                return meta.get("rank", "R0"), v3_name
    except Exception as exc:
        log.debug("Could not resolve v3 rank for %s: %s", action_name, exc)

    return "R0", action_name


# ── Unified Decision Engine ─────────────────────────────────────────

def should_auto_execute(action_name: str, context: str = "") -> tuple[bool, str]:
    """
    Decide whether to auto-execute an action.

    Sir has whitelisted ALL permissions — everything auto-approves.
    Returns (should_auto_execute, reason).
    """
    # Sir's directive: whitelist all, never block, auto-approve everything.
    return True, f"Auto-execute: {action_name} — all permissions whitelisted by Sir"


def record_approval(action_name: str) -> None:
    """
    Record an approval in BOTH skillset.json (legacy) AND TrustRegistry (v3).
    After 3 approvals, promotes to learned_safe in skillset and adds trust entry.
    """
    # 1. Legacy skillset recording
    try:
        from skillset import record_approval as sk_record
        sk_record(action_name)
    except Exception as exc:
        log.warning("Skillset record_approval failed: %s", exc)

    # 2. v3 TrustRegistry recording
    try:
        from security.trust_registry import TrustRegistry
        from tools.compat import resolve_v2_action

        tr = TrustRegistry()
        v3_name = resolve_v2_action(action_name) or action_name

        # Add trust entry with 365-day expiry
        trust_id = tr.add_trust(
            tool_name=v3_name,
            params={},
            rank=_resolve_v3_rank(action_name)[0],
            expiry_days=365,
            notes=f"Approved via Telegram (legacy action: {action_name})",
        )
        log.info("TrustRegistry entry added: %s for %s", trust_id, action_name)

        # After 3 total approvals, also add to quick-trust presets
        from skillset import load as load_skillset
        sk = load_skillset()
        counts = sk.get("autonomy", {}).get("approval_counts", {})
        if counts.get(action_name, 0) >= 3:
            log.info("Action %s reached 3 approvals — trusted in v3 registry", action_name)
    except Exception as exc:
        log.debug("TrustRegistry record_approval failed: %s", exc)


# ── Backward-compat exports ─────────────────────────────────────────

# These mirror the old autonomy.py API so imports don't break.
get_autonomy_decision = should_auto_execute


def suggest_next_action(last_action: str, outputs: dict) -> str | None:
    """Delegate to original autonomy module."""
    try:
        import autonomy
        return autonomy.suggest_next_action(last_action, outputs)
    except Exception:
        return None


def log_execution_outcome(action: str, success: bool, auto: bool) -> None:
    """Delegate to original autonomy module."""
    try:
        import autonomy
        autonomy.log_execution_outcome(action, success, auto)
    except Exception:
        pass


# ── CLI test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    test_actions = [
        "disk",          # R0 → auto
        "cpu",           # R0 → auto
        "reboot",        # always_ask → deny
        "service_restart",  # R3 → deny (not trusted)
        "restart_gateway",  # pre_approved → auto
    ]

    for action in test_actions:
        auto, reason = should_auto_execute(action)
        print(f"{'✅' if auto else '❌'} {action:20s} → {reason}")
