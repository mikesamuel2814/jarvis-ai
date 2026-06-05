#!/usr/bin/env python3
"""
Jarvis Action Executor — runs whitelisted actions on the Kali machine.
All actions are tiered: auto (no confirmation), confirm (one-tap), approve (explicit).
"""

import json
import os
import subprocess
import shlex
from datetime import datetime
from pathlib import Path

JARVIS_HOME = Path.home() / ".jarvis"
AUDIT_LOG = JARVIS_HOME / "logs" / "executor.log"

# Tiers
AUTO = "auto"
CONFIRM = "confirm"
APPROVE = "approve"

# ── Whitelisted actions ────────────────────────────────────────────────────────
# Each entry: description, shell command (None = custom fn), tier, optional arg slot
ACTIONS: dict[str, dict] = {
    # ── Read-only (auto) ──────────────────────────────────────────────────────
    "ps":                {"desc": "Running processes",             "cmd": "ps aux --sort=-%cpu | head -25",                     "tier": AUTO},
    "disk":              {"desc": "Disk usage",                    "cmd": "df -h",                                               "tier": AUTO},
    "memory":            {"desc": "RAM usage",                     "cmd": "free -h",                                             "tier": AUTO},
    "uptime":            {"desc": "System uptime",                 "cmd": "uptime",                                              "tier": AUTO},
    "gpu":               {"desc": "GPU status",                    "cmd": "nvidia-smi",                                          "tier": AUTO},
    "ports":             {"desc": "Open ports",                    "cmd": "ss -tlnp",                                            "tier": AUTO},
    "who":               {"desc": "Logged in users",               "cmd": "who",                                                 "tier": AUTO},
    "services":          {"desc": "Running services",              "cmd": "systemctl list-units --type=service --state=running --no-pager", "tier": AUTO},
    "logs_jarvis":       {"desc": "Jarvis API logs",               "cmd": "journalctl -u jarvis -n 30 --no-pager",              "tier": AUTO},
    "logs_telegram":     {"desc": "Telegram bot logs",             "cmd": "journalctl -u jarvis-telegram -n 30 --no-pager",     "tier": AUTO},
    "logs_ollama":       {"desc": "Ollama logs",                   "cmd": "journalctl -u ollama -n 20 --no-pager",              "tier": AUTO},
    "crontab":           {"desc": "Cron jobs",                     "cmd": "crontab -l",                                          "tier": AUTO},
    "network":           {"desc": "Network interfaces",            "cmd": "ip addr show",                                        "tier": AUTO},
    "ollama_models":     {"desc": "Installed AI models",           "cmd": "ollama list",                                         "tier": AUTO},
    "top5_cpu":          {"desc": "Top CPU processes",             "cmd": "ps aux --sort=-%cpu | head -6 | tail -5",            "tier": AUTO},
    "top5_mem":          {"desc": "Top memory processes",          "cmd": "ps aux --sort=-%mem | head -6 | tail -5",            "tier": AUTO},
    "tailscale":         {"desc": "Tailscale status",              "cmd": "tailscale status",                                    "tier": AUTO},

    # ── Medium risk (confirm) ─────────────────────────────────────────────────
    "restart_jarvis":    {"desc": "Restart Jarvis API",            "cmd": "sudo systemctl restart jarvis",                      "tier": CONFIRM},
    "restart_telegram":  {"desc": "Restart Telegram bot",          "cmd": "sudo systemctl restart jarvis-telegram",             "tier": CONFIRM},
    "restart_ollama":    {"desc": "Restart Ollama",                "cmd": "sudo systemctl restart ollama",                      "tier": CONFIRM},
    "restart_monitor":   {"desc": "Restart monitor service",       "cmd": "sudo systemctl restart jarvis-monitor",              "tier": CONFIRM},
    "reindex":           {"desc": "Re-index all data into memory", "cmd": "python3 /home/kali/.jarvis/indexer.py --now",        "tier": CONFIRM},
    "stop_jarvis":       {"desc": "Stop Jarvis API",               "cmd": "sudo systemctl stop jarvis",                         "tier": CONFIRM},
    "clear_history":     {"desc": "Clear Jarvis memory DB",        "cmd": None,                                                  "tier": CONFIRM},

    # ── Docker management (confirm) ───────────────────────────────────────────
    "docker_ps":         {"desc": "List running containers",               "cmd": "docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}'", "tier": AUTO},
    "docker_stats":      {"desc": "Container CPU/RAM usage",               "cmd": "docker stats --no-stream --format 'table {{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}'", "tier": AUTO},
    "docker_logs_api":   {"desc": "Jarvis API container logs",             "cmd": "docker logs jarvis-api --tail=30 2>&1",       "tier": AUTO},
    "docker_logs_bot":   {"desc": "Telegram bot container logs",           "cmd": "docker logs jarvis-telegram --tail=30 2>&1",  "tier": AUTO},
    "docker_restart_api":{"desc": "Restart API container",                 "cmd": "docker compose -f /home/kali/.jarvis/docker/docker-compose.yml restart jarvis-api", "tier": CONFIRM},
    "docker_restart_bot":{"desc": "Restart Telegram bot container",        "cmd": "docker compose -f /home/kali/.jarvis/docker/docker-compose.yml restart jarvis-telegram", "tier": CONFIRM},
    "docker_up":         {"desc": "Start Jarvis Docker stack",             "cmd": "cd /home/kali/.jarvis/docker && docker compose up -d jarvis-api jarvis-telegram", "tier": CONFIRM},
    "docker_down":       {"desc": "Stop Jarvis Docker stack",              "cmd": "cd /home/kali/.jarvis/docker && docker compose down", "tier": CONFIRM},

    # ── High risk (approve) ───────────────────────────────────────────────────
    "deploy_vps":        {"desc": "Deploy to VPS (git pull + pm2 restart)", "cmd": None,                                        "tier": APPROVE},
    "ssh_cmd":           {"desc": "Run command on VPS via SSH",    "cmd": None,                                                  "tier": APPROVE},
    "shell":             {"desc": "Run arbitrary shell command",   "cmd": None,                                                  "tier": APPROVE},
    "claude_task":       {"desc": "Run a Claude Code task",        "cmd": None,                                                  "tier": APPROVE},
    "reboot":            {"desc": "Reboot Kali machine",           "cmd": "sudo reboot",                                        "tier": APPROVE},
    "update_system":     {"desc": "Run apt update + upgrade",      "cmd": "sudo apt update && sudo apt upgrade -y",             "tier": APPROVE},
}

# ── Natural language → action name ────────────────────────────────────────────
NL_MAP: list[tuple[list[str], str]] = [
    (["restart jarvis", "restart api", "restart the api"],                         "restart_jarvis"),
    (["restart telegram", "restart bot", "restart the bot"],                       "restart_telegram"),
    (["restart ollama"],                                                            "restart_ollama"),
    (["show processes", "list processes", "what's running", "whats running", "ps"], "ps"),
    (["disk space", "disk usage", "how much disk", "storage"],                     "disk"),
    (["memory usage", "ram usage", "how much ram", "show memory"],                 "memory"),
    (["uptime", "how long running", "system uptime"],                              "uptime"),
    (["gpu status", "gpu usage", "show gpu", "nvidia"],                            "gpu"),
    (["open ports", "listening ports", "show ports"],                              "ports"),
    (["who is logged", "logged in users", "active users"],                         "who"),
    (["running services", "list services", "show services"],                       "services"),
    (["jarvis logs", "api logs", "show jarvis logs"],                              "logs_jarvis"),
    (["telegram logs", "bot logs", "show bot logs"],                               "logs_telegram"),
    (["ollama logs"],                                                               "logs_ollama"),
    (["cron jobs", "crontab", "scheduled tasks"],                                  "crontab"),
    (["network", "ip address", "interfaces", "show network"],                      "network"),
    (["models", "ai models", "installed models", "ollama models"],                 "ollama_models"),
    (["top cpu", "cpu usage", "cpu processes"],                                    "top5_cpu"),
    (["top memory", "memory processes", "mem usage"],                              "top5_mem"),
    (["tailscale", "vpn status", "tailscale status"],                              "tailscale"),
    (["docker containers", "list containers", "docker ps", "running containers"],  "docker_ps"),
    (["docker stats", "container stats", "container usage"],                       "docker_stats"),
    (["docker logs api", "container logs api"],                                    "docker_logs_api"),
    (["docker logs bot", "container logs telegram"],                               "docker_logs_bot"),
    (["docker up", "start docker", "start containers", "jarvis docker up"],        "docker_up"),
    (["docker down", "stop docker", "stop containers", "jarvis docker down"],      "docker_down"),
    (["reindex", "re-index", "index now", "update memory"],                        "reindex"),
    (["deploy", "deploy to vps", "deploy code"],                                   "deploy_vps"),
    (["claude task", "run claude", "claude code", "ask claude", "claude do", "jarvis code"], "claude_task"),
]

# Regex patterns for commands that need arg extraction
import re as _re
_CLAUDE_RESUME_RE = _re.compile(r"^claude\s+(--resume|-r)\s+([0-9a-f-]{36})", _re.I)
_CLAUDE_CMD_RE    = _re.compile(r"^claude\s+(.+)", _re.I)


def detect_action(text: str) -> str | None:
    """Detect if a message is an action command. Returns action name or None."""
    t = text.strip()
    # Detect raw claude CLI invocations (e.g. "claude --resume UUID")
    if _CLAUDE_RESUME_RE.match(t) or _CLAUDE_CMD_RE.match(t):
        return "claude_task"
    tl = t.lower().rstrip("?.!")
    for phrases, action in NL_MAP:
        for phrase in phrases:
            if phrase in tl or tl.startswith(phrase):
                return action
    return None


def extract_action_arg(text: str, action: str) -> str:
    """Extract the argument for an action from the original message text."""
    if action == "claude_task":
        m = _CLAUDE_RESUME_RE.match(text.strip()) or _CLAUDE_CMD_RE.match(text.strip())
        if m:
            return text.strip()  # Pass full command as task description
    return ""


def run_action(action_name: str, arg: str = "") -> dict:
    """Execute an action. Returns {success, output, action, tier}."""
    _audit(f"EXECUTE {action_name} arg={arg!r}")
    if action_name not in ACTIONS:
        return {"success": False, "output": f"Unknown action: {action_name}", "action": action_name}

    entry = ACTIONS[action_name]
    tier = entry["tier"]

    try:
        if action_name == "deploy_vps":
            return _deploy_vps(arg)
        if action_name == "ssh_cmd":
            return _ssh_cmd(arg)
        if action_name == "shell":
            return _run_shell(arg)
        if action_name == "claude_task":
            return _run_claude_task(arg)
        if action_name == "clear_history":
            return _clear_history()
        if entry["cmd"]:
            cmd = entry["cmd"]
            if "{arg}" in cmd:
                cmd = cmd.replace("{arg}", shlex.quote(arg))
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            output = (result.stdout + result.stderr).strip()
            _audit(f"DONE {action_name} exit={result.returncode}")
            return {"success": result.returncode == 0, "output": output[:3000], "action": action_name, "tier": tier}
    except subprocess.TimeoutExpired:
        _audit(f"TIMEOUT {action_name}")
        return {"success": False, "output": "Command timed out.", "action": action_name, "tier": tier}
    except Exception as e:
        _audit(f"ERROR {action_name}: {e}")
        return {"success": False, "output": str(e), "action": action_name, "tier": tier}

    return {"success": False, "output": "No command defined.", "action": action_name, "tier": tier}


def _run_claude_task(task: str) -> dict:
    if not task:
        return {"success": False, "output": "No task specified.", "action": "claude_task"}
    claude_bin = "/home/kali/.local/bin/claude"
    if not Path(claude_bin).exists():
        claude_bin = "claude"
    env = {**os.environ, "HOME": "/home/kali", "PATH": f"/home/kali/.local/bin:{os.environ.get('PATH','')}"}

    # Handle raw "claude <args>" passthrough (e.g. "claude --resume UUID")
    if task.lower().startswith("claude "):
        args_part = task[7:].strip()
        cmd = [claude_bin] + shlex.split(args_part)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                                cwd="/home/kali", env=env)
    else:
        result = subprocess.run(
            [claude_bin, "-p", "--dangerously-skip-permissions", task],
            capture_output=True, text=True, timeout=300,
            cwd="/home/kali/Projects", env=env,
        )
    output = (result.stdout + result.stderr).strip()
    _audit(f"CLAUDE_TASK done exit={result.returncode} task={task[:80]!r}")
    return {"success": result.returncode == 0, "output": output[:3000], "action": "claude_task"}


def _deploy_vps(repo: str = "") -> dict:
    import yaml
    cfg_path = JARVIS_HOME / "config" / "jarvis.yaml"
    vps = "38.47.35.16"
    user = "admin93"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text())
        vps = cfg.get("openclaw", {}).get("vps_host", vps)
    repo = repo.strip() or "starlineb"
    steps = [
        f"ssh {user}@{vps} 'cd ~/{repo} && git pull'",
        f"ssh {user}@{vps} 'cd ~/{repo} && npm install --production'",
        f"ssh {user}@{vps} 'pm2 restart {repo}'",
    ]
    results = []
    for step in steps:
        r = subprocess.run(step, shell=True, capture_output=True, text=True, timeout=60)
        icon = "OK" if r.returncode == 0 else "FAIL"
        cmd_part = step.split("'")[1] if "'" in step else step
        results.append(f"[{icon}] {cmd_part}\n{(r.stdout+r.stderr).strip()[:200]}")
    output = "\n\n".join(results)
    _audit(f"DEPLOY {repo} done")
    return {"success": True, "output": output, "action": "deploy_vps"}


def _ssh_cmd(cmd: str) -> dict:
    import yaml
    cfg_path = JARVIS_HOME / "config" / "jarvis.yaml"
    vps = "38.47.35.16"
    user = "admin93"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text())
        vps = cfg.get("openclaw", {}).get("vps_host", vps)
    full_cmd = f"ssh {user}@{vps} '{cmd}'"
    r = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, timeout=60)
    return {"success": r.returncode == 0, "output": (r.stdout + r.stderr).strip()[:3000], "action": "ssh_cmd"}


def _run_shell(cmd: str) -> dict:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    return {"success": r.returncode == 0, "output": (r.stdout + r.stderr).strip()[:3000], "action": "shell"}


def _clear_history() -> dict:
    hist = JARVIS_HOME / "data" / "telegram_history.json"
    hist.write_text("{}")
    return {"success": True, "output": "Conversation history cleared.", "action": "clear_history"}


def _audit(msg: str):
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG, "a") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def get_action_info(action_name: str) -> dict | None:
    return ACTIONS.get(action_name)
