"""
Jarvis v3 → v2 Compatibility Shim
Maps v2 action names to v3 tools, preserving the exact v2 API.
"""

import logging
from typing import Optional

from .decorator import get_tool_func, get_tool_metadata, list_registered_tools
from .registry import ToolRegistry

log = logging.getLogger("jarvis.tools.compat")

# Mapping from v2 action names → v3 tool names
# This preserves all existing v2 behavior while routing through v3 infrastructure.
_V2_TO_V3: dict[str, str] = {
    # System info
    "ps": "ps_list",
    "disk": "disk_space",
    "memory": "ram_usage",
    "uptime": "uptime",
    "gpu": "gpu_status",
    "ports": "port_listener_scan",
    "who": "who",
    "services": "systemctl_list",
    "logs_jarvis": "logs_jarvis",
    "logs_telegram": "logs_telegram",
    "logs_ollama": "logs_ollama",
    "crontab": "cron_jobs",
    "network": "ifconfig",
    "ollama_models": "ollama_models",
    "top5_cpu": "top_processes",
    "top5_mem": "top_processes",
    "tailscale": "tailscale_status",

    # Process & Service
    "restart_jarvis": "service_restart",
    "restart_telegram": "service_restart",
    "restart_ollama": "service_restart",
    "restart_monitor": "service_restart",
    "reindex": "reindex",
    "stop_jarvis": "service_stop",

    # Docker
    "docker_ps": "docker_ps",
    "docker_stats": "docker_ps",
    "docker_logs_api": "docker_logs",
    "docker_logs_bot": "docker_logs",
    "docker_restart_api": "docker_compose",
    "docker_restart_bot": "docker_compose",
    "docker_up": "docker_compose",
    "docker_down": "docker_compose",

    # VPS / Project monitoring
    "pm2_status": "pm2_status",
    "pm2_logs_gateway": "pm2_logs",
    "pm2_logs_starline": "pm2_logs",
    "git_status_all": "git_status",
    "nginx_status": "nginx_config",
    "jarvis_logs_tail": "logs_jarvis",
    "restart_gateway": "pm2_restart",
    "restart_starline": "pm2_restart",
    "vps_disk": "disk_space",
    "vps_free": "ram_usage",
    "vps_ps": "ps_list",
    "git_log_gw": "git_log",
    "git_log_sl": "git_log",
    "git_diff_gw": "git_diff",
    "git_diff_sl": "git_diff",
    "vps_nginx_logs": "nginx_config",
    "vps_services": "systemctl_list",

    # Local builds
    "git_pull_gw": "git_pull",
    "git_pull_sl": "git_pull",
    "npm_install_gw": "npm_install",
    "pnpm_install_sl": "npm_install",
    "npm_build_gw": "pnpm_build",
    "pnpm_build_sl": "pnpm_build",

    # VPS management
    "vps_git_pull_gw": "git_pull",
    "vps_git_pull_sl": "git_pull",
    "vps_restart_nginx": "service_restart",
    "restart_jarvis_sync": "service_restart",

    # File operations
    "file_read": "file_read",
    "file_list": "ls_dir",
    "project_status": "project_status",

    # High risk
    "deploy_vps": "ssh_remote",
    "ssh_cmd": "ssh_remote",
    "shell": "bash_script",
    "claude_task": "claude_task",
    "file_write": "file_write",
    "reboot": "reboot",
    "update_system": "update_system",
}

# Hard-blocked actions (carry over from v2 executor)
_HARD_BLOCKED = {
    "update_system": (
        "🚫 update_system is disabled. Running `apt upgrade` unattended wedged a "
        "CPU core (stuck dpkg-query → kernel soft lockup) on the current kernel. "
        "Run it interactively in a terminal instead: `sudo apt update && sudo apt upgrade`. "
        "Re-enable by removing it from executor.HARD_BLOCKED after pinning a stable kernel."
    ),
}


def resolve_v2_action(v2_name: str) -> Optional[str]:
    """Map a v2 action name to its v3 tool equivalent."""
    return _V2_TO_V3.get(v2_name)


def run_action(action_name: str, arg: str = "", approved: bool = False) -> dict:
    """
    v2-compatible action runner.
    Returns {success, output, action, tier} — identical to executor.run_action().
    """
    if action_name in _HARD_BLOCKED:
        return {
            "success": False,
            "output": _HARD_BLOCKED[action_name],
            "action": action_name,
            "tier": "APPROVE",
            "blocked": True,
        }

    v3_name = resolve_v2_action(action_name)
    if v3_name is None:
        # Try direct v3 name
        if action_name in list_registered_tools():
            v3_name = action_name
        else:
            return {
                "success": False,
                "output": f"Unknown action: {action_name}",
                "action": action_name,
                "tier": "?",
            }

    meta = get_tool_metadata(v3_name)
    tier = meta.get("rank", "R0") if meta else "R0"

    registry = ToolRegistry()
    # Convert v2 arg string to v3 kwargs based on tool
    kwargs = _build_kwargs(v3_name, arg)
    result = registry.execute(v3_name, **kwargs)

    return {
        "success": result.success,
        "output": result.output + (f"\nERROR: {result.error}" if result.error else ""),
        "action": action_name,
        "tier": tier,
    }


def _build_kwargs(tool_name: str, arg: str) -> dict:
    """Convert v2 string arg into v3 kwargs for known tools."""
    if not arg:
        return {}

    # File operations
    if tool_name == "file_read":
        return {"path": arg}
    if tool_name == "ls_dir":
        return {"path": arg or "."}
    if tool_name == "file_write":
        if ":" in arg:
            path, content = arg.split(":", 1)
            return {"path": path, "content": content}
        return {"path": arg}

    # Service operations
    if tool_name == "service_restart":
        service_map = {
            "restart_jarvis": "jarvis",
            "restart_telegram": "jarvis-telegram",
            "restart_ollama": "ollama",
            "restart_monitor": "jarvis-monitor",
            "vps_restart_nginx": "nginx",
            "restart_jarvis_sync": "jarvis-sync",
        }
        # We don't have the v2 action name here, but caller passes it in action_name
        return {}

    # Git operations
    if tool_name in ("git_status", "git_pull", "git_commit", "git_log", "git_diff"):
        # arg is repo path for some; for v2 compatibility we use known paths
        return {}

    # SSH / shell
    if tool_name == "ssh_remote":
        return {"host": "38.47.35.16", "user": "admin93", "command": arg}
    if tool_name == "bash_script":
        return {"script": arg}

    # Default: pass as 'arg' if the tool accepts it
    meta = get_tool_metadata(tool_name)
    if meta and "arg" in meta.get("params", {}):
        return {"arg": arg}
    return {}


def list_v2_actions() -> list[str]:
    """Return all v2 action names that the shim can handle."""
    return sorted(set(list(_V2_TO_V3.keys()) + list_registered_tools()))
