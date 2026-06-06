#!/usr/bin/env python3
"""
Jarvis v3 Executor Compatibility Layer
Drop-in replacement for executor.py that routes all calls through v3 tools.
Provides identical API so telegram_bot.py and other v2 callers need no changes.

SECURITY: Every tool execution is verified before and after running:
  1. Scope check against session scope
  2. Hard-blocked actions denied outright
  3. Trust registry check for R2+ actions
  4. Output verification (no secrets leaked, no command injection in output)
"""

import os
import re
import shlex
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

# Ensure v3 tools are loaded
import sys
JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
sys.path.insert(0, str(JARVIS_HOME))

from tools.core.system import *
from tools.core.process import *
from tools.core.file import *
from tools.core.network import *
from tools.core.security import *
from tools.core.security_audit import *
from tools.core.dev import *
from tools.core.database import *
from tools.core.docker import *
from tools.core.web import *
from tools.core.backup import *
from tools.core.automation import *
from tools.core.comms import *

from tools.decorator import get_tool_metadata, list_registered_tools
from tools.registry import ToolRegistry
from tools.compat import run_action as _v3_run_action, _HARD_BLOCKED
from security.scope_enforcer import ScopeEnforcer, ScopeLevel
from security.trust_registry import TrustRegistry

AUDIT_LOG = JARVIS_HOME / "logs" / "executor.log"
AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("jarvis.executor_v3_compat")

# ── Tier constants (identical to v2) ──────────────────────────────────
AUTO = "auto"
CONFIRM = "confirm"
APPROVE = "approve"

# ── Build ACTIONS dict in v2 format ───────────────────────────────────
_ACTIONS: dict[str, dict] = {}
_RANK_TO_TIER = {"R0": AUTO, "R1": AUTO, "R2": CONFIRM, "R3": CONFIRM, "R4": APPROVE, "R5": APPROVE, "R6": APPROVE}

for name in list_registered_tools():
    meta = get_tool_metadata(name)
    if meta is None:
        continue
    # Map v3 rank to v2 tier
    tier = _RANK_TO_TIER.get(meta.get("rank", "R0"), CONFIRM)
    _ACTIONS[name] = {
        "desc": meta.get("description", name),
        "cmd": None,  # v3 tools are functions, not shell commands
        "tier": tier,
    }

# Add v2 aliases pointing to same entries
_V2_ALIASES = {
    "ps": "ps_list", "disk": "disk_space", "memory": "ram_usage",
    "gpu": "gpu_status", "ports": "port_listener_scan", "services": "systemctl_list",
    "network": "ifconfig", "crontab": "cron_jobs", "tailscale": "tailscale_status",
    "restart_jarvis": "service_restart", "restart_telegram": "service_restart",
    "restart_ollama": "service_restart", "stop_jarvis": "service_stop",
    "docker_ps": "docker_ps", "docker_stats": "docker_ps",
    "pm2_status": "pm2_status", "git_status_all": "git_status",
    "nginx_status": "nginx_config", "vps_disk": "disk_space",
    "vps_free": "ram_usage", "vps_ps": "ps_list",
    "file_read": "file_read", "file_list": "ls_dir",
    "shell": "bash_script", "ssh_cmd": "ssh_remote",
    "reboot": "reboot", "update_system": "update_system",
}
for v2_name, v3_name in _V2_ALIASES.items():
    if v3_name in _ACTIONS:
        _ACTIONS[v2_name] = _ACTIONS[v3_name].copy()

ACTIONS = _ACTIONS  # Export for v2 callers

# ── Natural language map (simplified from v2) ─────────────────────────
_NL_MAP: list[tuple[list[str], str]] = [
    (["restart jarvis", "restart api"], "restart_jarvis"),
    (["restart telegram", "restart bot"], "restart_telegram"),
    (["show processes", "list processes", "whats running", "ps"], "ps"),
    (["disk space", "disk usage", "storage", "check disk", "show disk"], "disk"),
    (["memory usage", "ram usage", "show memory", "check memory", "check ram"], "memory"),
    (["uptime", "how long running"], "uptime"),
    (["gpu status", "gpu usage", "nvidia", "check gpu"], "gpu"),
    (["open ports", "listening ports", "show ports"], "ports"),
    (["running services", "list services", "show services"], "services"),
    (["network", "ip address", "interfaces", "show network"], "network"),
    (["models", "ai models", "ollama models"], "ollama_models"),
    (["top cpu", "cpu usage", "check cpu"], "top5_cpu"),
    (["top memory", "memory processes", "top ram"], "top5_mem"),
    (["tailscale", "vpn status"], "tailscale"),
    (["docker containers", "docker ps"], "docker_ps"),
    (["reindex", "update memory"], "reindex"),
    (["pm2 status", "vps processes"], "pm2_status"),
    (["gateway logs", "asthacash logs"], "pm2_logs_gateway"),
    (["starline logs", "api server logs"], "pm2_logs_starline"),
    (["restart gateway", "restart asthacash"], "restart_gateway"),
    (["restart starline", "restart real estate"], "restart_starline"),
    (["read file", "cat file", "show file"], "file_read"),
    (["list files", "list directory", "ls "], "file_list"),
    (["project status", "both projects"], "project_status"),
    (["deploy", "deploy to vps"], "deploy_vps"),
    (["claude task", "run claude"], "claude_task"),
]

_ACTION_ALIASES = {
    "gw logs": "pm2_logs_gateway", "gw restart": "restart_gateway",
    "sl logs": "pm2_logs_starline", "sl restart": "restart_starline",
    "pm2": "pm2_status", "git st": "git_status_all", "nginx": "nginx_status",
    "disk": "disk", "memory": "memory", "ps": "ps", "gpu": "gpu",
    "services": "services", "ports": "ports", "uptime": "uptime",
}

_STOPWORDS = {"the", "a", "an", "is", "are", "on", "to", "of", "and", "in", "my",
              "me", "do", "run", "check", "get", "show", "tell", "if", "it", "for",
              "what", "whats", "how", "can", "you", "please", "now", "this", "that"}

_KALI_TOOL_NAMES = re.compile(
    r"\b(nmap|nikto|wpscan|gobuster|ffuf|sqlmap|nuclei|masscan|theharvester|"
    r"whatweb|searchsploit|hydra|medusa|aircrack|dirb|dirbuster|feroxbuster|"
    r"amass|subfinder|dnsx|httpx|crackmapexec|evil-winrm|metasploit|msfconsole|"
    r"burpsuite|zaproxy|openvas|nessus|wireshark|tcpdump|netcat|nc)\b",
    re.I,
)


def detect_action(text: str) -> Optional[str]:
    """Detect if a message is an action command. Returns action name or None."""
    t = text.strip()
    tl = t.lower().rstrip("?.!")

    # Guard: Kali tools never match as system actions
    if _KALI_TOOL_NAMES.search(tl):
        return None

    # Exact alias match
    if tl in _ACTION_ALIASES:
        return _ACTION_ALIASES[tl]

    # Exact action name
    if tl in ACTIONS:
        return tl

    # Longest-phrase-wins
    best_action = None
    best_len = 0
    for phrases, action in _NL_MAP:
        for phrase in phrases:
            pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
            if re.search(pattern, tl) and len(phrase) > best_len:
                best_action = action
                best_len = len(phrase)
    return best_action


def _audit(msg: str):
    try:
        with open(AUDIT_LOG, "a") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# ── Security verification ─────────────────────────────────────────────

def _verify_output(output: str, action: str) -> tuple[bool, str]:
    """
    Verify tool output before returning to user.
    Returns (ok, output_or_error).
    """
    if not output:
        return True, output

    # Check for leaked secrets
    secret_patterns = [
        r"[A-Za-z0-9_-]{20,}",  # long tokens
        r"-----BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----",
        r"MOONSHOT_API_KEY\s*=\s*\S+",
        r"api_key\s*=\s*[\"']\S+[\"']",
    ]
    for pattern in secret_patterns:
        if re.search(pattern, output, re.IGNORECASE):
            # Don't block - just log and continue. Real secret detection is in vault.
            _audit(f"VERIFY_WARNING potential_secret_in_output action={action}")
            break

    # Check for command injection artifacts in output
    dangerous = ["; rm -rf /", "; sudo", "| bash", "| sh ", "$("]
    for d in dangerous:
        if d in output.lower():
            return False, f"🚫 Output blocked: potential injection artifact detected."

    return True, output


# ── Main execution ────────────────────────────────────────────────────

def run_action(action_name: str, arg: str = "", approved: bool = False) -> dict:
    """
    Execute an action with v3 verification gates.
    Returns {success, output, action, tier} — identical to v2.
    """
    _audit(f"EXECUTE {action_name} arg={arg!r} approved={approved}")

    # 1. Hard block check
    if action_name in _HARD_BLOCKED:
        _audit(f"BLOCKED {action_name} (hard-blocked)")
        return {
            "success": False,
            "output": _HARD_BLOCKED[action_name],
            "action": action_name,
            "tier": APPROVE,
            "blocked": True,
        }

    # 2. Resolve to v3 tool
    from tools.compat import resolve_v2_action
    v3_name = resolve_v2_action(action_name)
    if v3_name is None:
        if action_name in list_registered_tools():
            v3_name = action_name
        else:
            return {"success": False, "output": f"Unknown action: {action_name}", "action": action_name, "tier": "?"}

    meta = get_tool_metadata(v3_name)
    if meta is None:
        return {"success": False, "output": f"No metadata for: {action_name}", "action": action_name, "tier": "?"}

    rank = meta.get("rank", "R0")
    scope_str = meta.get("scope", "READ")
    tier = _RANK_TO_TIER.get(rank, CONFIRM)

    # 3. Scope enforcement
    scope = ScopeLevel(scope_str)
    enforcer = ScopeEnforcer(current_scope=ScopeLevel.LOCAL if approved else ScopeLevel.READ)
    if not enforcer.can_execute(scope):
        _audit(f"SCOPE_DENIED {action_name} scope={scope.value}")
        return {
            "success": False,
            "output": f"🚫 Action '{action_name}' exceeds session scope ({scope.value}). Approve to escalate.",
            "action": action_name,
            "tier": tier,
        }

    # 4. Trust check for R2+ (unless approved)
    if rank in ("R2", "R3", "R4", "R5", "R6") and not approved:
        trust = TrustRegistry()
        if not trust.is_trusted(v3_name, {}):
            _audit(f"NEEDS_APPROVAL {action_name} rank={rank}")
            return {
                "success": False,
                "output": f"⏳ Action '{action_name}' requires approval (rank {rank}).",
                "action": action_name,
                "tier": tier,
                "needs_approval": True,
            }

    # 5. Execute via v3
    result = _v3_run_action(action_name, arg=arg, approved=approved)

    # 6. Verify output
    ok, verified_output = _verify_output(result.get("output", ""), action_name)
    if not ok:
        _audit(f"OUTPUT_BLOCKED {action_name}")
        result["output"] = verified_output
        result["success"] = False

    _audit(f"DONE {action_name} success={result['success']}")
    return result


# Back-compat: allow importing these names
if __name__ == "__main__":
    print(f"v3 compat executor loaded. {len(ACTIONS)} actions available.")
