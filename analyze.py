#!/usr/bin/env python3
"""
Jarvis Daily Briefing — 9am digest of work activity, errors, and system health.
Cron: 0 9 * * * /usr/bin/python3 /home/kali/.jarvis/analyze.py
"""

import json
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import psutil
import requests
import yaml

JARVIS_HOME = Path.home() / ".jarvis"
CONFIG_FILE = JARVIS_HOME / "config" / "jarvis.yaml"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return yaml.safe_load(CONFIG_FILE.read_text()) or {}
    return {}


def get_git_activity(hours: int = 24) -> tuple[int, list[str]]:
    """Count commits and collect messages across all Projects."""
    projects = Path.home() / "Projects"
    if not projects.exists():
        return 0, []
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    total = 0
    messages = []
    for repo in projects.iterdir():
        if not (repo / ".git").exists():
            continue
        try:
            r = subprocess.run(
                ["git", "-C", str(repo), "log", "--since", since, "--oneline", "--no-merges"],
                capture_output=True, text=True, timeout=5,
            )
            lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
            total += len(lines)
            for line in lines[:2]:
                messages.append(f"{repo.name}: {line[8:]}")  # strip commit hash
        except Exception:
            pass
    return total, messages[:5]


def get_shell_activity(hours: int = 24) -> tuple[int, list[tuple[str, int]]]:
    """Count shell commands from Jarvis history log."""
    hist = JARVIS_HOME / "data" / "shell" / "history.log"
    if not hist.exists():
        return 0, []
    since = datetime.now() - timedelta(hours=hours)
    commands = []
    for line in hist.read_text().splitlines()[-2000:]:
        try:
            ts = datetime.fromisoformat(line.split(" | ")[0].strip())
            if ts > since:
                cmd = line.split(" | ")[-1].strip().split()[0]
                if cmd:
                    commands.append(cmd)
        except Exception:
            pass
    return len(commands), Counter(commands).most_common(5)


def get_recent_errors(hours: int = 24) -> list[str]:
    """Scan Jarvis logs for errors in the last N hours."""
    errors = []
    since = datetime.now() - timedelta(hours=hours)
    for log_file in (JARVIS_HOME / "logs").glob("*.log"):
        try:
            for line in log_file.read_text().splitlines()[-200:]:
                if any(kw in line.lower() for kw in ["error", "fail", "exception", "critical"]):
                    # Try to parse timestamp
                    try:
                        ts_str = line[1:20]
                        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                        if ts > since:
                            errors.append(f"{log_file.stem}: {line.strip()[:80]}")
                    except Exception:
                        errors.append(f"{log_file.stem}: {line.strip()[:80]}")
        except Exception:
            pass
    return errors[-8:]


def get_system_snapshot() -> dict:
    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime_h = round((datetime.now().timestamp() - psutil.boot_time()) / 3600, 1)
    return {
        "cpu": cpu,
        "ram_pct": ram.percent,
        "ram_used_gb": round(ram.used / 1e9, 1),
        "ram_total_gb": round(ram.total / 1e9, 1),
        "disk_pct": disk.percent,
        "disk_free_gb": round(disk.free / 1e9, 1),
        "uptime_h": uptime_h,
    }


def get_memory_stats() -> int:
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(JARVIS_HOME / "memory"))
        col = client.get_or_create_collection("jarvis_memory")
        return col.count()
    except Exception:
        return 0


def generate_briefing(period: str = "daily") -> str:
    now = datetime.now()
    hours = 24 if period == "daily" else 168

    commits, commit_msgs = get_git_activity(hours)
    cmd_count, top_cmds = get_shell_activity(hours)
    errors = get_recent_errors(hours)
    sys_snap = get_system_snapshot()
    memory_chunks = get_memory_stats()

    label = now.strftime("%A, %B %-d") if period == "daily" else f"Week of {now.strftime('%B %-d')}"
    emoji = "📋" if period == "daily" else "📊"

    lines = [f"{emoji} Jarvis {'Daily' if period == 'daily' else 'Weekly'} Briefing — {label}\n"]

    # Work activity
    lines.append(f"🔨 Activity")
    lines.append(f"  Git commits: {commits}")
    if commit_msgs:
        for m in commit_msgs[:3]:
            lines.append(f"    • {m}")
    lines.append(f"  Shell commands: {cmd_count}")
    if top_cmds:
        top = ", ".join(f"{cmd}×{n}" for cmd, n in top_cmds[:3])
        lines.append(f"  Top commands: {top}")

    # System health
    lines.append(f"\n💻 System")
    lines.append(f"  Uptime: {sys_snap['uptime_h']}h")
    lines.append(f"  CPU: {sys_snap['cpu']}% | RAM: {sys_snap['ram_pct']}% ({sys_snap['ram_used_gb']}/{sys_snap['ram_total_gb']} GB)")
    lines.append(f"  Disk: {sys_snap['disk_pct']}% used ({sys_snap['disk_free_gb']} GB free)")

    # Jarvis memory
    lines.append(f"\n🧠 Jarvis memory: {memory_chunks:,} chunks")

    # Errors
    if errors:
        lines.append(f"\n⚠️ Errors ({len(errors)})")
        for e in errors[:4]:
            lines.append(f"  • {e[:70]}")
    else:
        lines.append(f"\n✅ No errors detected")

    return "\n".join(lines)


def send_briefing(cfg: dict, text: str):
    token_file = JARVIS_HOME / "config" / "telegram.json"
    if not token_file.exists():
        print(text)
        return
    try:
        token = json.loads(token_file.read_text()).get("bot_token", "")
        if not token or token == "YOUR_BOT_TOKEN_HERE":
            print(text)
            return
        api_port = cfg.get("interfaces", {}).get("api_port", 8181)
        try:
            r = requests.post(
                f"http://localhost:{api_port}/telegram/send",
                json={"text": text},
                timeout=10,
            )
            if r.status_code == 200:
                print("Briefing sent via Jarvis API")
                return
        except Exception:
            pass
        # Fallback: direct Telegram API
        updates = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates", timeout=10
        ).json()
        msgs = updates.get("result", [])
        if msgs:
            chat_id = msgs[-1]["message"]["chat"]["id"]
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
            print("Briefing sent via direct Telegram API")
    except Exception as e:
        print(f"Failed to send briefing: {e}")
        print(text)


if __name__ == "__main__":
    import sys
    period = "weekly" if "--weekly" in sys.argv else "daily"  # --daily is default
    cfg = load_config()
    briefing = generate_briefing(period)
    print(briefing)
    send_briefing(cfg, briefing)
