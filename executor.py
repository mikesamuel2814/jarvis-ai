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
    "restart_jarvis":    {"desc": "Restart Jarvis API",            "cmd": "sudo -n systemctl restart jarvis",                      "tier": CONFIRM},
    "restart_telegram":  {"desc": "Restart Telegram bot",          "cmd": "sudo -n systemctl restart jarvis-telegram",             "tier": CONFIRM},
    "restart_ollama":    {"desc": "Restart Ollama",                "cmd": "sudo -n systemctl restart ollama",                      "tier": CONFIRM},
    "restart_monitor":   {"desc": "Restart monitor service",       "cmd": "sudo -n systemctl restart jarvis-monitor",              "tier": CONFIRM},
    "reindex":           {"desc": "Re-index all data into memory", "cmd": "python3 /home/kali/.jarvis/indexer.py --now",        "tier": CONFIRM},
    "stop_jarvis":       {"desc": "Stop Jarvis API",               "cmd": "sudo -n systemctl stop jarvis",                         "tier": CONFIRM},
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

    # ── VPS project monitoring (auto) ────────────────────────────────────────
    "pm2_status":        {"desc": "VPS PM2 process list",                  "cmd": "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'pm2 list 2>&1 | head -30'",                                                  "tier": AUTO},
    "pm2_logs_gateway":  {"desc": "AsthaCash gateway backend logs",        "cmd": "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'pm2 logs gateway-backend --lines 20 --nostream 2>&1'",                       "tier": AUTO},
    "pm2_logs_starline": {"desc": "Starline API server logs",              "cmd": "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'pm2 logs api-server --lines 20 --nostream 2>&1'",                            "tier": AUTO},
    "git_status_all":    {"desc": "Git status across all projects",        "cmd": r"find /home/kali/Projects -name '.git' -maxdepth 3 -exec sh -c 'echo \"=== $(dirname {}) ===\"; git -C $(dirname {}) status --short' \;", "tier": AUTO},
    "nginx_status":      {"desc": "VPS Nginx status and config test",      "cmd": "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'systemctl is-active nginx && nginx -t 2>&1'",                                "tier": AUTO},
    "jarvis_logs_tail":  {"desc": "Jarvis API latest logs",                "cmd": "tail -50 /home/kali/.jarvis/logs/jarvis.log",                                                                                      "tier": AUTO},

    # ── VPS project restarts (confirm) ───────────────────────────────────────
    "restart_gateway":   {"desc": "Restart AsthaCash gateway backend on VPS", "cmd": "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'pm2 restart gateway-backend 2>&1'",                                      "tier": CONFIRM},
    "restart_starline":  {"desc": "Restart Starline API server on VPS",    "cmd": "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'pm2 restart api-server 2>&1'",                                              "tier": CONFIRM},

    # ── File & project read-only (auto) ─────────────────────────────────────
    "file_read":         {"desc": "Read a file (path as arg)",     "cmd": None,                                                  "tier": AUTO},
    "file_list":         {"desc": "List a directory (path as arg)","cmd": None,                                                  "tier": AUTO},
    "project_status":    {"desc": "Status of both projects (PM2 + git)", "cmd": None,                                           "tier": AUTO},
    "vps_disk":          {"desc": "VPS disk usage",                "cmd": "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'df -h'",                                                                              "tier": AUTO},
    "vps_free":          {"desc": "VPS RAM usage",                 "cmd": "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'free -h'",                                                                            "tier": AUTO},
    "vps_ps":            {"desc": "VPS running processes",         "cmd": "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'ps aux --sort=-%cpu | head -20'",                                                     "tier": AUTO},
    "git_log_gw":        {"desc": "Recent commits in Payment-Gateway", "cmd": "git -C /home/kali/Projects/kalimike/Payment-Gateway log --oneline -15",                                                              "tier": AUTO},
    "git_log_sl":        {"desc": "Recent commits in Starline",    "cmd": "git -C /home/kali/Projects/kalimike/Starline-Final-web log --oneline -15",                                                               "tier": AUTO},
    "git_diff_gw":       {"desc": "Unstaged changes in Payment-Gateway","cmd": "git -C /home/kali/Projects/kalimike/Payment-Gateway diff --stat",                                                                    "tier": AUTO},
    "git_diff_sl":       {"desc": "Unstaged changes in Starline",  "cmd": "git -C /home/kali/Projects/kalimike/Starline-Final-web diff --stat",                                                                     "tier": AUTO},
    "vps_nginx_logs":    {"desc": "VPS Nginx error logs",          "cmd": "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'tail -30 /var/log/nginx/error.log 2>/dev/null || journalctl -u nginx -n 30 --no-pager'", "tier": AUTO},
    "vps_services":      {"desc": "VPS systemd services status",   "cmd": "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'systemctl list-units --type=service --state=running --no-pager | head -20'",         "tier": AUTO},

    # ── Local project builds (confirm) ───────────────────────────────────────
    "git_pull_gw":       {"desc": "git pull Payment-Gateway",      "cmd": "git -C /home/kali/Projects/kalimike/Payment-Gateway pull",                                                                               "tier": CONFIRM},
    "git_pull_sl":       {"desc": "git pull Starline-Final-web",   "cmd": "git -C /home/kali/Projects/kalimike/Starline-Final-web pull",                                                                            "tier": CONFIRM},
    "npm_install_gw":    {"desc": "npm install gateway-admin",     "cmd": "cd /home/kali/Projects/kalimike/Payment-Gateway/gateway-admin && npm install --legacy-peer-deps",                                        "tier": CONFIRM},
    "pnpm_install_sl":   {"desc": "pnpm install Starline",         "cmd": "cd /home/kali/Projects/kalimike/Starline-Final-web && pnpm install",                                                                     "tier": CONFIRM},
    "npm_build_gw":      {"desc": "npm build gateway-admin",       "cmd": "cd /home/kali/Projects/kalimike/Payment-Gateway/gateway-admin && npm run build",                                                         "tier": CONFIRM},
    "pnpm_build_sl":     {"desc": "pnpm build Starline frontend",  "cmd": "cd /home/kali/Projects/kalimike/Starline-Final-web && pnpm run build",                                                                   "tier": CONFIRM},

    # ── VPS management (confirm) ─────────────────────────────────────────────
    "vps_git_pull_gw":   {"desc": "git pull Payment-Gateway on VPS","cmd": "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'cd ~/Payment-Gateway && git pull 2>&1'",                                          "tier": CONFIRM},
    "vps_git_pull_sl":   {"desc": "git pull Starline on VPS",      "cmd": "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'cd ~/Starline-Final-web && git pull 2>&1'",                                        "tier": CONFIRM},
    "vps_restart_nginx": {"desc": "Restart Nginx on VPS",          "cmd": "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'sudo systemctl restart nginx 2>&1'",                                               "tier": CONFIRM},
    "restart_jarvis_sync":{"desc":"Restart jarvis-sync service",   "cmd": "sudo -n systemctl restart jarvis-sync",                                                                                                   "tier": CONFIRM},

    # ── High risk (approve) ───────────────────────────────────────────────────
    "deploy_vps":        {"desc": "Deploy to VPS (git pull + pm2 restart)", "cmd": None,                                        "tier": APPROVE},
    "ssh_cmd":           {"desc": "Run command on VPS via SSH",    "cmd": None,                                                  "tier": APPROVE},
    "shell":             {"desc": "Run arbitrary shell command",   "cmd": None,                                                  "tier": APPROVE},
    "claude_task":       {"desc": "Run a Claude Code task (background, results via Telegram)", "cmd": None,                     "tier": APPROVE},
    "file_write":        {"desc": "Write content to a local file (path:content as arg)",       "cmd": None,                     "tier": APPROVE},
    "reboot":            {"desc": "Reboot Kali machine",           "cmd": "sudo -n reboot",                                     "tier": APPROVE},
    "update_system":     {"desc": "Run apt update + upgrade",      "cmd": "sudo -n apt update && sudo -n apt upgrade -y",      "tier": APPROVE},
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
    (["gpu status", "gpu usage", "show gpu", "nvidia", "gpu temp", "gpu temperature", "what is my gpu", "how hot is gpu"], "gpu"),
    (["open ports", "listening ports", "show ports"],                              "ports"),
    (["who is logged", "logged in users", "active users"],                         "who"),
    (["running services", "list services", "show services"],                       "services"),
    (["jarvis logs", "show jarvis logs"],                                          "logs_jarvis"),
    (["api logs", "show jarvis log", "jarvis log tail", "tail jarvis log"],        "jarvis_logs_tail"),
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

    # ── VPS project monitoring ────────────────────────────────────────────────
    (["pm2 status", "vps processes", "what's running on vps", "pm2 list"],         "pm2_status"),
    (["gateway logs", "asthacash logs", "payment gateway logs", "gw logs"],        "pm2_logs_gateway"),
    (["starline logs", "api server logs", "real estate logs"],                     "pm2_logs_starline"),
    (["git status", "what's uncommitted", "dirty repos", "git changes"],           "git_status_all"),
    (["restart gateway", "restart asthacash", "restart payment"],                  "restart_gateway"),
    (["restart starline", "restart real estate", "restart api server"],            "restart_starline"),
    (["nginx status", "nginx test", "web server status"],                          "nginx_status"),

    # ── New extended actions ──────────────────────────────────────────────────
    (["read file", "cat file", "show file", "view file"],                         "file_read"),
    (["list files", "list directory", "ls "],                                     "file_list"),
    (["project status", "both projects", "all projects status"],                  "project_status"),
    (["vps disk", "vps storage", "vps space"],                                    "vps_disk"),
    (["vps memory", "vps ram", "vps free"],                                       "vps_free"),
    (["vps ps", "vps processes"],                                                 "vps_ps"),
    (["git log gateway", "gateway commits", "gw log"],                            "git_log_gw"),
    (["git log starline", "starline commits", "sl log"],                          "git_log_sl"),
    (["git diff gateway", "gateway changes"],                                     "git_diff_gw"),
    (["git diff starline", "starline changes"],                                   "git_diff_sl"),
    (["vps nginx logs", "nginx error logs"],                                      "vps_nginx_logs"),
    (["vps services", "vps systemd"],                                             "vps_services"),
    (["git pull gateway", "pull gateway"],                                        "git_pull_gw"),
    (["git pull starline", "pull starline"],                                      "git_pull_sl"),
    (["npm install gateway", "install gateway deps"],                             "npm_install_gw"),
    (["pnpm install starline", "install starline deps"],                          "pnpm_install_sl"),
    (["build gateway", "npm build gateway"],                                      "npm_build_gw"),
    (["build starline", "pnpm build starline"],                                   "pnpm_build_sl"),
    (["vps git pull gateway", "deploy gateway"],                                  "vps_git_pull_gw"),
    (["vps git pull starline", "deploy starline"],                                "vps_git_pull_sl"),
    (["restart nginx", "nginx restart"],                                          "vps_restart_nginx"),
    (["restart syncer", "restart jarvis-sync", "restart sync"],                  "restart_jarvis_sync"),
]

# Regex patterns for commands that need arg extraction
import re as _re
_CLAUDE_RESUME_RE = _re.compile(r"^claude\s+(--resume|-r)\s+([0-9a-f-]{36})", _re.I)
_CLAUDE_CMD_RE    = _re.compile(r"^claude\s+(.+)", _re.I)

# Short Telegram aliases → action name (override NL_MAP when exact match)
ACTION_ALIASES: dict[str, str] = {
    "gw logs":     "pm2_logs_gateway",
    "gw restart":  "restart_gateway",
    "sl logs":     "pm2_logs_starline",
    "sl restart":  "restart_starline",
    "pm2":         "pm2_status",
    "git st":      "git_status_all",
    "nginx":       "nginx_status",
    "jlogs":       "jarvis_logs_tail",
}


def detect_action(text: str) -> str | None:
    """Detect if a message is an action command. Returns action name or None.

    Matching priority:
      1. Raw 'claude ...' CLI passthrough.
      2. Exact alias match (ACTION_ALIASES).
      3. Longest NL_MAP phrase that appears in the query wins (specificity).
    """
    t = text.strip()
    # 1. Raw claude CLI invocations
    if _CLAUDE_RESUME_RE.match(t) or _CLAUDE_CMD_RE.match(t):
        return "claude_task"
    tl = t.lower().rstrip("?.!")

    # 2. Alias exact match
    if tl in ACTION_ALIASES:
        return ACTION_ALIASES[tl]

    # 3. Longest-phrase-wins across NL_MAP
    best_action: str | None = None
    best_len: int = 0
    for phrases, action in NL_MAP:
        for phrase in phrases:
            if (phrase in tl or tl.startswith(phrase)) and len(phrase) > best_len:
                best_action = action
                best_len = len(phrase)
    return best_action


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
        if action_name == "file_read":
            return _file_read(arg)
        if action_name == "file_list":
            return _file_list(arg)
        if action_name == "file_write":
            return _file_write(arg)
        if action_name == "project_status":
            return _project_status()
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


_SAFE_ROOTS = [
    Path.home() / ".jarvis",
    Path.home() / "Projects",
    Path("/var/log"),
    Path("/tmp"),
]


def _is_safe_path(p: Path) -> bool:
    try:
        r = p.resolve()
        return any(r == root.resolve() or root.resolve() in r.parents for root in _SAFE_ROOTS)
    except Exception:
        return False


def _file_read(path_arg: str) -> dict:
    if not path_arg:
        return {"success": False, "output": "No path given.", "action": "file_read"}
    p = Path(path_arg.strip()).expanduser()
    if not _is_safe_path(p):
        return {"success": False, "output": f"Path not in allowed roots: {p}", "action": "file_read"}
    if not p.exists():
        return {"success": False, "output": f"File not found: {p}", "action": "file_read"}
    if p.is_dir():
        return _file_list(path_arg)
    try:
        content = p.read_text(errors="replace")[:4000]
        return {"success": True, "output": content, "action": "file_read"}
    except Exception as e:
        return {"success": False, "output": str(e), "action": "file_read"}


def _file_list(path_arg: str) -> dict:
    target = Path(path_arg.strip() if path_arg else ".").expanduser()
    if not _is_safe_path(target) and str(target) != ".":
        return {"success": False, "output": f"Path not in allowed roots: {target}", "action": "file_list"}
    r = subprocess.run(f"ls -la {shlex.quote(str(target))}", shell=True,
                       capture_output=True, text=True, timeout=10)
    return {"success": r.returncode == 0, "output": (r.stdout + r.stderr).strip()[:2000], "action": "file_list"}


def _file_write(arg: str) -> dict:
    """arg format: 'path::content' (double colon separator)"""
    if "::" not in arg:
        return {"success": False, "output": "Format: path::content", "action": "file_write"}
    path_str, content = arg.split("::", 1)
    p = Path(path_str.strip()).expanduser()
    if not _is_safe_path(p):
        return {"success": False, "output": f"Path not in allowed roots: {p}", "action": "file_write"}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return {"success": True, "output": f"Written {len(content)} chars to {p}", "action": "file_write"}
    except Exception as e:
        return {"success": False, "output": str(e), "action": "file_write"}


def _project_status() -> dict:
    lines = []
    # PM2 status
    pm2 = subprocess.run(
        "ssh -o StrictHostKeyChecking=no admin93@38.47.35.16 'pm2 list 2>&1 | head -20'",
        shell=True, capture_output=True, text=True, timeout=15,
    )
    lines.append("=== VPS PM2 ===\n" + (pm2.stdout + pm2.stderr).strip()[:500])
    # Local git status
    for proj, path in [("Payment-Gateway", "/home/kali/Projects/kalimike/Payment-Gateway"),
                        ("Starline", "/home/kali/Projects/kalimike/Starline-Final-web")]:
        g = subprocess.run(f"git -C {path} status --short",
                           shell=True, capture_output=True, text=True, timeout=10)
        out = (g.stdout + g.stderr).strip() or "(clean)"
        lines.append(f"=== {proj} git ===\n{out[:300]}")
    return {"success": True, "output": "\n\n".join(lines), "action": "project_status"}


def _run_claude_task(task: str) -> dict:
    if not task:
        return {"success": False, "output": "No task specified.", "action": "claude_task"}
    claude_bin = "/home/kali/.local/bin/claude"
    if not Path(claude_bin).exists():
        claude_bin = "claude"
    env = {**os.environ, "HOME": "/home/kali",
           "PATH": f"/home/kali/.local/bin:{os.environ.get('PATH','')}",
           "MALLOC_ARENA_MAX": "2"}

    import threading

    def _bg_run(task_desc: str):
        try:
            if task_desc.lower().startswith("claude "):
                args_part = task_desc[7:].strip()
                cmd = [claude_bin] + shlex.split(args_part)
                cwd = "/home/kali"
            else:
                cmd = [claude_bin, "-p", "--dangerously-skip-permissions", task_desc]
                cwd = "/home/kali/Projects"
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=cwd, env=env)
            output = (r.stdout + r.stderr).strip()[:3000]
            _audit(f"CLAUDE_TASK done exit={r.returncode} task={task_desc[:80]!r}")
            # Notify via Telegram
            try:
                import requests as _req
                icon = "✅" if r.returncode == 0 else "❌"
                msg = f"{icon} Claude task done:\n_{task_desc[:100]}_\n\n```\n{output[:2000]}\n```"
                _req.post("http://localhost:8181/telegram/send",
                          json={"text": msg, "parse_mode": "Markdown"}, timeout=10)
            except Exception:
                pass
        except subprocess.TimeoutExpired:
            _audit(f"CLAUDE_TASK TIMEOUT task={task_desc[:80]!r}")
            try:
                import requests as _req
                _req.post("http://localhost:8181/telegram/send",
                          json={"text": f"⏱ Claude task timed out:\n_{task_desc[:100]}_"}, timeout=10)
            except Exception:
                pass

    t = threading.Thread(target=_bg_run, args=(task,), daemon=True)
    t.start()
    _audit(f"CLAUDE_TASK started task={task[:80]!r}")
    return {"success": True,
            "output": f"Claude task started in background. Results will be sent via Telegram.\nTask: {task[:200]}",
            "action": "claude_task"}


def _audit(msg: str):
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG, "a") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def get_action_info(action_name: str) -> dict | None:
    return ACTIONS.get(action_name)
