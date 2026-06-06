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

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
DECISION_LOG   = JARVIS_HOME / "data" / "decisions.jsonl"
DECISION_STATE = JARVIS_HOME / "data" / "decision_state.json"


def _api_key() -> str:
    """Read JARVIS_API_KEY from env or secrets.env file."""
    key = os.environ.get("JARVIS_API_KEY", "")
    if not key:
        try:
            for line in (JARVIS_HOME / "config" / "secrets.env").read_text().splitlines():
                if line.startswith("JARVIS_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
        except Exception:
            pass
    return key


def _api_headers() -> dict:
    key = _api_key()
    return {"X-API-Key": key} if key else {}

VPS_HOST = "38.47.35.16"
VPS_USER = "admin93"

TRACKED_REPOS = {
    "Payment-Gateway":   Path.home() / "Projects" / "kalimike" / "Payment-Gateway",
    "Starline-Final-web": Path.home() / "Projects" / "kalimike" / "Starline-Final-web",
}

# Minimum seconds before the same alert key can fire again
COOLDOWN_SECONDS = 1800  # 30 minutes

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
                jarvis_str = str(JARVIS_HOME)
                if jarvis_str not in sys.path:
                    sys.path.insert(0, jarvis_str)
                from voice import send_voice_telegram
                send_voice_telegram(voice_text, token, chat_id)
            except Exception:
                pass


# ── Cooldown helper ────────────────────────────────────────────────────────────

def _cooldown_ok(state: dict, alert_key: str, severity: str = "same") -> bool:
    """Return True if enough time has passed since this alert last fired.

    severity='worse' bypasses the cooldown — always fires if the situation worsened.
    """
    if severity == "worse":
        return True
    cooldowns = state.setdefault("cooldowns", {})
    last_ts = cooldowns.get(alert_key)
    if last_ts:
        elapsed = (datetime.now() - datetime.fromisoformat(last_ts)).total_seconds()
        if elapsed < COOLDOWN_SECONDS:
            return False
    return True


def _cooldown_set(state: dict, alert_key: str):
    state.setdefault("cooldowns", {})[alert_key] = datetime.now().isoformat()


def ai_decide(situation: str, options: list[str]) -> dict:
    """Use DeepSeek-R1 to pick the best action. Returns {action, reasoning, confidence}."""
    try:
        import ollama
        opts_str = "\n".join(f"  {i+1}. {o}" for i, o in enumerate(options))
        prompt = (
            f"Jarvis decision needed for: {situation}\n"
            f"Mike's preferences: high autonomy, auto-act on safe operations, notify after\n"
            f"Options:\n{opts_str}\n\n"
            f"Pick the best action. Respond with ONLY valid JSON — no markdown, no explanation outside JSON:\n"
            f'{{\"action\": \"...\", \"reasoning\": \"one line\", \"confidence\": 0.0}}'
        )
        resp = ollama.chat(
            model="deepseek-r1:7b",
            messages=[
                {"role": "system", "content": "You are a decision AI for Jarvis, Mike Samuel's personal AI. Output only valid JSON. No markdown."},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.1, "num_predict": 160, "num_ctx": 512},
        )
        raw = _strip(resp["message"]["content"])
        s, e = raw.find("{"), raw.rfind("}") + 1
        if s >= 0 and e > s:
            d = json.loads(raw[s:e])
            action = d.get("action", "")
            # If model returned a choice index instead of text, map it
            if not action:
                idx = int(d.get("choice", 1)) - 1
                idx = max(0, min(idx, len(options) - 1))
                action = options[idx]
            confidence = d.get("confidence", 0.5)
            if isinstance(confidence, str):
                confidence = {"high": 0.85, "medium": 0.5, "low": 0.2}.get(confidence, 0.5)
            return {"action": action, "reasoning": d.get("reasoning", ""), "confidence": confidence}
    except Exception:
        pass
    return {"action": options[-1], "reasoning": "AI unavailable — defaulting to safe option", "confidence": 0.2}


# ── Decision checks ────────────────────────────────────────────────────────────

def check_morning_briefing(state: dict):
    """Trigger morning briefing between 08:55–09:05 if not already sent today."""
    now = datetime.now()
    if not (now.hour == 8 and now.minute >= 55) and not (now.hour == 9 and now.minute <= 5):
        return

    today_str = now.strftime("%Y-%m-%d")
    if state.get("last_briefing_date") == today_str:
        return  # Already sent today

    _log("Morning briefing window detected — triggering analyze.py")
    try:
        result = subprocess.run(
            [str(JARVIS_HOME / "venv" / "bin" / "python3"), str(JARVIS_HOME / "analyze.py")],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        state["last_briefing_date"] = today_str
        _log_decision("Morning briefing trigger", "Ran analyze.py directly", True)
        _log(f"Morning briefing ran: exit {result.returncode}")
    except Exception as e:
        _log(f"Morning briefing failed: {e}")


def check_vps_deploy_health(state: dict):
    """After a recent deploy_vps action, verify the VPS is actually up."""
    # Look for recent deploy entries in decisions.jsonl (within last 30 min)
    recent_deploy = False
    try:
        if DECISION_LOG.exists():
            cutoff = datetime.now() - timedelta(minutes=30)
            with open(DECISION_LOG) as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        ts = datetime.fromisoformat(entry.get("timestamp", "1970-01-01"))
                        if ts > cutoff and "deploy_vps" in entry.get("situation", "").lower():
                            recent_deploy = True
                            break
                    except Exception:
                        pass
    except Exception:
        pass

    if not recent_deploy:
        return

    alert_key = "vps_deploy_health"
    # Check VPS HTTP reachability
    try:
        r = requests.get(f"http://{VPS_HOST}", timeout=5)
        vps_http_ok = r.status_code < 500
    except Exception:
        vps_http_ok = False

    # Check PM2 process list via SSH
    pm2_down = []
    try:
        ssh_r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
             f"{VPS_USER}@{VPS_HOST}", "pm2 jlist 2>/dev/null"],
            capture_output=True, text=True, timeout=15,
        )
        if ssh_r.returncode == 0 and ssh_r.stdout.strip():
            procs = json.loads(ssh_r.stdout.strip())
            for proc in procs:
                status = proc.get("pm2_env", {}).get("status", "")
                if status in ("stopped", "errored"):
                    pm2_down.append(proc.get("name", "?"))
    except Exception:
        pass

    if pm2_down and _cooldown_ok(state, alert_key):
        names = ", ".join(pm2_down)
        _notify(
            f"🔴 *VPS post-deploy check FAILED*\nProcesses down after deploy: `{names}`\nHTTP: {'OK' if vps_http_ok else 'UNREACHABLE'}",
            voice_text=f"Sir, VPS processes {names} are down after the recent deploy. Immediate attention required.",
        )
        _log_decision("VPS post-deploy health check", f"Processes down: {names}", False)
        _cooldown_set(state, alert_key)
        _log(f"VPS post-deploy alert: {names}")
    elif not vps_http_ok and _cooldown_ok(state, alert_key):
        _notify(
            f"🔴 *VPS unreachable* after recent deploy\n`http://{VPS_HOST}` timed out.",
            voice_text=f"Sir, the VPS at {VPS_HOST} is unreachable after a recent deploy.",
        )
        _log_decision("VPS post-deploy HTTP check", "VPS unreachable", False)
        _cooldown_set(state, alert_key)
        _log("VPS post-deploy HTTP unreachable")


def check_disk_filling(state: dict):
    try:
        disk = psutil.disk_usage("/")
        pct = disk.percent
        free_gb = round(disk.free / 1e9, 1)
        prev = state.get("disk_pct_prev", 0)
        state["disk_pct_prev"] = pct

        alert_key = "disk_90"
        if pct > 90:
            severity = "worse" if pct > state.get("disk_pct_alerted_at", 0) + 2 else "same"
            if _cooldown_ok(state, alert_key, severity):
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
                _cooldown_set(state, alert_key)
                state["disk_pct_alerted_at"] = pct
                _log(f"Disk alert sent: {pct}%")
        elif pct < 85:
            state.pop("disk_pct_alerted_at", None)

        # Filling fast: >3% jump in 5min
        spike_key = "disk_spike"
        if prev and (pct - prev) > 3 and _cooldown_ok(state, spike_key):
            _notify(
                f"⚠️ Disk filling fast: {prev}% → {pct}% in last 5 min",
                voice_text="Sir, disk usage is rising fast. Something may be writing large files.",
            )
            _cooldown_set(state, spike_key)
        elif (pct - prev) <= 1:
            state.get("cooldowns", {}).pop(spike_key, None)
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
        try:
            r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=5)
            is_active = r.stdout.strip() == "active"
        except Exception:
            is_active = True  # assume active on timeout/error — avoid false-positive alerts
            continue
        key = f"svc_smart_{svc}"
        alert_key = f"svc_down_{svc}"

        if not is_active and _cooldown_ok(state, alert_key):
            action_name = f"restart_{svc.replace('-','_').replace('jarvis_telegram','telegram')}"

            # Check autonomy: if this restart is pre-approved/learned-safe, execute directly
            auto_restarted = False
            try:
                from autonomy import should_auto_execute, log_execution_outcome
                auto_ok, auto_reason = should_auto_execute(action_name)
                if auto_ok:
                    result = subprocess.run(
                        ["sudo", "-n", "systemctl", "restart", svc],
                        capture_output=True, text=True, timeout=30,
                    )
                    success = result.returncode == 0
                    log_execution_outcome(action_name, success=success, auto=True)
                    if success:
                        _notify(
                            f"⚡ *{desc} auto-restarted* — was down, now recovering.\n_{auto_reason}_",
                            voice_text=f"Sir, {desc} was down. I've restarted it automatically.",
                        )
                        _log_decision(f"{svc} down", f"Auto-restarted ({auto_reason})", True)
                        auto_restarted = True
            except Exception:
                pass

            if not auto_restarted:
                # Create a pending approval request via Jarvis API
                try:
                    resp = requests.post(
                        f"http://localhost:{api_port}/action",
                        json={"action": action_name, "arg": ""},
                        headers=_api_headers(),
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

            _cooldown_set(state, alert_key)
            state[key] = True
            _log(f"Service down alert: {svc}")
        elif is_active and state.get(key):
            state[key] = False
            state.get("cooldowns", {}).pop(alert_key, None)
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
        if r.returncode != 0 or not r.stdout.strip():
            return
        procs = json.loads(r.stdout.strip())
        for proc in procs:
            name = proc.get("name", "?")
            status = proc.get("pm2_env", {}).get("status", "")
            key = f"vps_pm2_{name}"
            alert_key = f"vps_pm2_alert_{name}"
            if status in ("stopped", "errored"):
                if _cooldown_ok(state, alert_key):
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
                    _cooldown_set(state, alert_key)
                    state[key] = True
                    _log(f"VPS PM2 {name}: {status} → restarted")
            elif status == "online" and state.get(key):
                state[key] = False
                state.get("cooldowns", {}).pop(alert_key, None)
    except Exception as e:
        _log(f"VPS PM2 check error: {e}")


def check_git_uncommitted(state: dict):
    """Notify if tracked projects have uncommitted changes sitting for >3 hours.

    Reports per-repo with file counts and specific filenames.
    """
    last = state.get("git_check_last")
    if last and (datetime.now() - datetime.fromisoformat(last)).total_seconds() < 10800:
        return
    state["git_check_last"] = datetime.now().isoformat()

    dirty_repos = {}  # repo_name -> list of changed files

    for repo_name, repo_path in TRACKED_REPOS.items():
        if not repo_path.exists():
            continue
        try:
            r = subprocess.run(
                ["git", "-C", str(repo_path), "status", "--porcelain"],
                capture_output=True, text=True, timeout=5,
            )
            if r.stdout.strip():
                files = []
                for line in r.stdout.strip().splitlines():
                    # porcelain format: XY filename
                    parts = line.strip().split(None, 1)
                    if len(parts) == 2:
                        files.append(parts[1])
                dirty_repos[repo_name] = files
        except Exception:
            pass

    prev = state.get("git_dirty_repos", {})

    for repo_name, files in dirty_repos.items():
        alert_key = f"git_dirty_{repo_name}"
        prev_count = len(prev.get(repo_name, []))
        curr_count = len(files)

        # Determine severity: worse if file count grew
        severity = "worse" if curr_count > prev_count else "same"

        if not _cooldown_ok(state, alert_key, severity):
            continue

        # Compute how long this has been dirty (use state timestamp)
        dirty_since_key = f"git_dirty_since_{repo_name}"
        if repo_name not in prev:
            state[dirty_since_key] = datetime.now().isoformat()

        dirty_since = state.get(dirty_since_key)
        hours_dirty = 0
        if dirty_since:
            hours_dirty = (datetime.now() - datetime.fromisoformat(dirty_since)).total_seconds() / 3600

        if hours_dirty < 3 and severity != "worse":
            continue

        # Build readable file list (up to 4 shown)
        shown = files[:4]
        file_list = ", ".join(shown)
        if len(files) > 4:
            file_list += f" (+{len(files) - 4} more)"

        decision = ai_decide(
            f"{repo_name} has {curr_count} uncommitted file(s) for {hours_dirty:.1f} hours. Files: {file_list}",
            ["Notify Mike to commit", "Stay silent — Mike is likely actively working"]
        )
        if "notify" in decision["action"].lower():
            _notify(
                f"📝 *{repo_name}* has {curr_count} uncommitted file(s) for {int(hours_dirty)}h:\n"
                f"`{file_list}`\nCommit when ready, Sir.",
                voice_text=None,  # Silent for git — not urgent
            )
            _log_decision(f"git dirty: {repo_name} ({curr_count} files)", decision["action"], False)
            _cooldown_set(state, alert_key)
            _log(f"Git dirty alert: {repo_name} ({curr_count} files, {hours_dirty:.1f}h)")

    # Clear tracking for repos that are now clean
    for repo_name in list(prev.keys()):
        if repo_name not in dirty_repos:
            state.pop(f"git_dirty_since_{repo_name}", None)
            state.get("cooldowns", {}).pop(f"git_dirty_{repo_name}", None)

    state["git_dirty_repos"] = {k: v for k, v in dirty_repos.items()}


def check_high_resource(state: dict):
    """Alert on sustained high CPU/RAM with AI diagnosis. Includes top memory processes."""
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

        cpu_alert_key = "cpu_sustained"
        if cpu_high_count >= 3 and _cooldown_ok(state, cpu_alert_key):
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
            _cooldown_set(state, cpu_alert_key)
            _log(f"Sustained CPU alert: {cpu}%, top={top_proc}")
        elif cpu < 75:
            state.get("cooldowns", {}).pop(cpu_alert_key, None)

        # RAM > 80% — include top 3 memory hogs
        ram_alert_key = "ram_high"
        if ram > 80:
            severity = "worse" if ram > state.get("ram_alerted_at", 0) + 5 else "same"
            if _cooldown_ok(state, ram_alert_key, severity):
                try:
                    procs = sorted(
                        psutil.process_iter(["name", "memory_percent"]),
                        key=lambda p: p.info["memory_percent"],
                        reverse=True,
                    )[:3]
                    proc_lines = "\n".join(
                        f"  • `{p.info['name']}` — {p.info['memory_percent']:.1f}%"
                        for p in procs
                    )
                except Exception:
                    proc_lines = "  (process list unavailable)"

                threshold_label = "critical" if ram > 90 else "high"
                _notify(
                    f"{'🔴' if ram > 90 else '⚠️'} *RAM {threshold_label}: {ram}%*\n"
                    f"Top memory processes:\n{proc_lines}",
                    voice_text=f"Sir, RAM is at {int(ram)} percent. "
                               f"{'System may become unresponsive.' if ram > 90 else 'Consider closing heavy processes.'}",
                )
                _cooldown_set(state, ram_alert_key)
                state["ram_alerted_at"] = ram
                _log(f"RAM {threshold_label} alert: {ram}%")
        elif ram < 75:
            state.pop("ram_alerted_at", None)
            state.get("cooldowns", {}).pop(ram_alert_key, None)
    except Exception as e:
        _log(f"resource check error: {e}")


def run_decision_engine() -> int:
    state = load_state()
    errors = 0
    checks = [
        check_morning_briefing,
        check_services_smart,
        check_disk_filling,
        check_high_resource,
        check_vps_processes,
        check_vps_deploy_health,
        check_git_uncommitted,
    ]
    for check in checks:
        try:
            check(state)
        except Exception as e:
            _log(f"{check.__name__} failed: {e}")
            errors += 1
    save_state(state)
    return errors


if __name__ == "__main__":
    run_decision_engine()
