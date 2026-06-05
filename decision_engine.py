#!/usr/bin/env python3
"""
Jarvis Decision Engine — proactive AI decision-making.
Runs inside monitor.py (every 5 min via cron).
Makes AUTO-safe decisions automatically; sends Telegram + voice for important events.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import psutil
import requests

JARVIS_HOME = Path.home() / ".jarvis"
DECISION_LOG   = JARVIS_HOME / "data" / "decisions.jsonl"
DECISION_STATE = JARVIS_HOME / "data" / "decision_state.json"

_STRIP_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip(t: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", _STRIP_RE.sub("", t)).strip()


def _log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [decision] {msg}"
    log_file = JARVIS_HOME / "logs" / "monitor.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if DECISION_STATE.exists():
        try:
            return json.loads(DECISION_STATE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict):
    DECISION_STATE.parent.mkdir(parents=True, exist_ok=True)
    DECISION_STATE.write_text(json.dumps(state, indent=2))


def _log_decision(situation: str, decision: str, auto_acted: bool):
    DECISION_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "situation": situation,
        "decision": decision,
        "auto_acted": auto_acted,
    }
    with open(DECISION_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _get_telegram_creds() -> tuple[str, int | None]:
    token_file = JARVIS_HOME / "config" / "telegram.json"
    chat_id_file = JARVIS_HOME / "data" / "telegram_chat_id.json"
    token = ""
    chat_id = None
    try:
        token = json.loads(token_file.read_text()).get("bot_token", "")
        if token == "YOUR_BOT_TOKEN_HERE":
            token = ""
    except Exception:
        pass
    try:
        chat_id = json.loads(chat_id_file.read_text()).get("chat_id")
    except Exception:
        pass
    return token, chat_id


def _notify(text_msg: str, voice_text: str | None = None, parse_mode: str = "Markdown"):
    """Send Telegram text + optional voice."""
    import yaml
    cfg_file = JARVIS_HOME / "config" / "jarvis.yaml"
    api_port = 8181
    try:
        cfg = yaml.safe_load(cfg_file.read_text()) or {}
        api_port = cfg.get("interfaces", {}).get("api_port", 8181)
    except Exception:
        pass

    # Send text via Jarvis API (preferred — handles chat_id)
    sent = False
    try:
        r = requests.post(
            f"http://localhost:{api_port}/telegram/send",
            json={"text": text_msg},
            timeout=10,
        )
        sent = r.status_code == 200
    except Exception:
        pass

    # Fallback: direct Telegram API
    if not sent:
        token, chat_id = _get_telegram_creds()
        if token and chat_id:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text_msg, "parse_mode": parse_mode},
                    timeout=10,
                )
            except Exception:
                pass

    # Voice notification
    if voice_text:
        token, chat_id = _get_telegram_creds()
        if token and chat_id:
            try:
                sys.path.insert(0, str(JARVIS_HOME))
                from voice import send_voice_telegram
                send_voice_telegram(voice_text, token, chat_id)
            except Exception:
                pass


def ai_decide(situation: str, options: list[str]) -> dict:
    """Use DeepSeek-R1 to pick the best action. Returns {action, reasoning, confidence}."""
    try:
        import ollama
        opts_str = "\n".join(f"  {i+1}. {o}" for i, o in enumerate(options))
        prompt = (
            f"Situation: {situation}\n\nOptions:\n{opts_str}\n\n"
            f"Output ONLY JSON: {{\"choice\": 1, \"reasoning\": \"one line\", \"confidence\": \"high|medium|low\"}}"
        )
        resp = ollama.chat(
            model="deepseek-r1:7b",
            messages=[
                {"role": "system", "content": "You are a decision AI. Output only valid JSON. No markdown."},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.1, "num_predict": 128, "num_ctx": 512},
        )
        raw = _strip(resp["message"]["content"])
        s, e = raw.find("{"), raw.rfind("}") + 1
        if s >= 0 and e > s:
            d = json.loads(raw[s:e])
            idx = int(d.get("choice", 1)) - 1
            idx = max(0, min(idx, len(options) - 1))
            return {"action": options[idx], "reasoning": d.get("reasoning", ""), "confidence": d.get("confidence", "medium")}
    except Exception:
        pass
    return {"action": options[-1], "reasoning": "AI unavailable — defaulting to safe option", "confidence": "low"}


# ── Decision checks ────────────────────────────────────────────────────────────

def check_disk_filling(state: dict):
    try:
        disk = psutil.disk_usage("/")
        pct = disk.percent
        free_gb = round(disk.free / 1e9, 1)
        prev = state.get("disk_pct_prev", 0)
        state["disk_pct_prev"] = pct

        if pct > 90 and not state.get("disk_90_alerted"):
            decision = ai_decide(
                f"Disk is {pct}% full ({free_gb}GB free) on Mike's Kali machine.",
                [
                    "Alert Mike to clean up — check ~/.jarvis/logs/ and ~/Downloads/",
                    "Alert Mike — suggest running: du -sh ~/* | sort -rh | head -10",
                ]
            )
            _notify(
                f"🔴 *Disk {pct}% full* — {free_gb}GB remaining\n"
                f"Recommendation: {decision['action']}",
                voice_text=f"Warning Sir, disk is {pct} percent full with only {free_gb} gigabytes remaining.",
            )
            _log_decision(f"Disk {pct}% full", decision["action"], False)
            state["disk_90_alerted"] = True
            _log(f"Disk alert sent: {pct}%")
        elif pct < 85:
            state["disk_90_alerted"] = False

        # Filling fast: >3% jump in 5min
        if prev and (pct - prev) > 3 and not state.get("disk_spike_alerted"):
            _notify(
                f"⚠️ Disk filling fast: {prev}% → {pct}% in last 5 min",
                voice_text="Sir, disk usage is rising fast. Something may be writing large files.",
            )
            state["disk_spike_alerted"] = True
        elif (pct - prev) <= 1:
            state.pop("disk_spike_alerted", None)
    except Exception as e:
        _log(f"disk_filling check error: {e}")


def check_services_smart(state: dict):
    """Detect downed services, create approval request for Mike to restart."""
    services = {
        "ollama": "Ollama LLM",
        "jarvis": "Jarvis API",
        "jarvis-telegram": "Telegram bot",
    }
    import yaml
    cfg_file = JARVIS_HOME / "config" / "jarvis.yaml"
    api_port = 8181
    try:
        cfg = yaml.safe_load(cfg_file.read_text()) or {}
        api_port = cfg.get("interfaces", {}).get("api_port", 8181)
    except Exception:
        pass

    for svc, desc in services.items():
        r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True)
        is_active = r.stdout.strip() == "active"
        key = f"svc_smart_{svc}"

        if not is_active and not state.get(key):
            # Create a pending approval request via Jarvis API
            try:
                resp = requests.post(
                    f"http://localhost:{api_port}/action",
                    json={"action": f"restart_{svc.replace('-','_').replace('jarvis_telegram','telegram')}", "arg": ""},
                    timeout=10,
                )
                d = resp.json()
                req_id = d.get("request_id", "")
                msg = (
                    f"🔴 *{desc} is DOWN*\n"
                    f"Jarvis detected `{svc}` stopped.\n\n"
                    f"Tap to restart:\n`/approve {req_id}`"
                )
            except Exception:
                msg = f"🔴 *{desc} is DOWN* (`{svc}`)\nRun: `sudo systemctl restart {svc}`"

            _notify(
                msg,
                voice_text=f"Sir, {desc} has gone down. Please check Telegram to restart it.",
            )
            _log_decision(f"{svc} down", "Sent restart approval request to Mike", False)
            state[key] = True
            _log(f"Service down alert: {svc}")
        elif is_active and state.get(key):
            state[key] = False
            _notify(f"✅ `{svc}` is back online.")
            _log(f"Service recovered: {svc}")


def check_vps_processes(state: dict):
    """Check VPS PM2 processes every 10 min, alert if down."""
    import yaml
    cfg_file = JARVIS_HOME / "config" / "jarvis.yaml"
    try:
        cfg = yaml.safe_load(cfg_file.read_text()) or {}
    except Exception:
        cfg = {}
    vps_host = cfg.get("openclaw", {}).get("vps_host", "")
    if not vps_host:
        return

    last = state.get("vps_pm2_last")
    if last and (datetime.now() - datetime.fromisoformat(last)).total_seconds() < 600:
        return
    state["vps_pm2_last"] = datetime.now().isoformat()

    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
             f"admin93@{vps_host}", "pm2 jlist 2>/dev/null"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return
        procs = json.loads(r.stdout.strip())
        for proc in procs:
            name = proc.get("name", "?")
            status = proc.get("pm2_env", {}).get("status", "")
            key = f"vps_pm2_{name}"
            if status in ("stopped", "errored"):
                if not state.get(key):
                    # Auto-restart on VPS (safe — just restarts an app process)
                    restart_r = subprocess.run(
                        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                         f"admin93@{vps_host}", f"pm2 restart {name} 2>&1 | tail -2"],
                        capture_output=True, text=True, timeout=20,
                    )
                    if restart_r.returncode == 0:
                        msg = f"⚡ *Auto-restarted* VPS process `{name}` (was {status})\n`{restart_r.stdout.strip()[:200]}`"
                        voice = f"Sir, VPS process {name} was {status}. I restarted it automatically."
                        _log_decision(f"VPS PM2 {name} {status}", "Auto-restarted via SSH", True)
                    else:
                        msg = f"🔴 VPS `{name}` is {status} — auto-restart failed.\n`{restart_r.stderr.strip()[:200]}`"
                        voice = f"Sir, VPS process {name} is {status} and could not be restarted."
                        _log_decision(f"VPS PM2 {name} restart failed", "Manual needed", False)
                    _notify(msg, voice_text=voice)
                    state[key] = True
                    _log(f"VPS PM2 {name}: {status} → restarted")
            elif status == "online" and state.get(key):
                state[key] = False
    except Exception as e:
        _log(f"VPS PM2 check error: {e}")


def check_git_uncommitted(state: dict):
    """Notify if projects have uncommitted changes sitting for >3 hours."""
    last = state.get("git_check_last")
    if last and (datetime.now() - datetime.fromisoformat(last)).total_seconds() < 10800:
        return
    state["git_check_last"] = datetime.now().isoformat()

    projects_dir = Path.home() / "Projects"
    if not projects_dir.exists():
        return

    dirty = []
    for git_dir in projects_dir.rglob(".git"):
        repo = git_dir.parent
        try:
            r = subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain"],
                capture_output=True, text=True, timeout=5,
            )
            if r.stdout.strip():
                dirty.append(repo.name)
        except Exception:
            pass

    prev = state.get("git_dirty_repos", [])
    if dirty and dirty != prev:
        repos = ", ".join(dirty[:3])
        decision = ai_decide(
            f"Repos with uncommitted changes: {repos}. Mike may be actively working.",
            ["Notify Mike to commit", "Stay silent — Mike is likely working"]
        )
        if "notify" in decision["action"].lower():
            _notify(
                f"📝 Uncommitted changes in: `{repos}`\nCommit when ready, Sir.",
                voice_text=None,  # Silent for git — not urgent
            )
        state["git_dirty_repos"] = dirty
    elif not dirty:
        state["git_dirty_repos"] = []


def check_high_resource(state: dict):
    """Alert on sustained high CPU/RAM with AI diagnosis."""
    try:
        cpu = psutil.cpu_percent(interval=2)
        ram = psutil.virtual_memory().percent

        # CPU high for 2+ consecutive checks
        cpu_high_count = state.get("cpu_high_count", 0)
        if cpu > 88:
            cpu_high_count += 1
        else:
            cpu_high_count = 0
        state["cpu_high_count"] = cpu_high_count

        if cpu_high_count >= 3 and not state.get("cpu_sustained_alert"):
            # Find the culprit process
            try:
                ps_r = subprocess.run(
                    ["ps", "aux", "--sort=-%cpu", "--no-headers"],
                    capture_output=True, text=True, timeout=5,
                )
                top_proc = ps_r.stdout.splitlines()[0].split()[10] if ps_r.stdout else "unknown"
            except Exception:
                top_proc = "unknown"
            _notify(
                f"⚠️ *CPU sustained at {cpu}%* for 15+ min\nTop process: `{top_proc}`",
                voice_text=f"Sir, CPU has been at {int(cpu)} percent for over 15 minutes. Top process is {top_proc}.",
            )
            state["cpu_sustained_alert"] = True
            _log(f"Sustained CPU alert: {cpu}%, top={top_proc}")
        elif cpu < 75:
            state["cpu_sustained_alert"] = False

        if ram > 90 and not state.get("ram_critical_alert"):
            _notify(
                f"🔴 *RAM critical: {ram}%*\nConsider restarting heavy processes.",
                voice_text=f"Sir, RAM is at {int(ram)} percent. System may become unresponsive.",
            )
            state["ram_critical_alert"] = True
            _log(f"RAM critical alert: {ram}%")
        elif ram < 82:
            state["ram_critical_alert"] = False
    except Exception as e:
        _log(f"resource check error: {e}")


def run_decision_engine() -> int:
    state = load_state()
    n = 0
    checks = [
        check_services_smart,
        check_disk_filling,
        check_high_resource,
        check_vps_processes,
        check_git_uncommitted,
    ]
    for check in checks:
        try:
            check(state)
        except Exception as e:
            _log(f"{check.__name__} failed: {e}")
    save_state(state)
    return n


if __name__ == "__main__":
    run_decision_engine()
