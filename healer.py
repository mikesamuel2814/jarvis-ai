#!/usr/bin/env python3
"""
Jarvis Self-Healer — autonomous recovery, log rotation, health tracking.
Runs every 10 min via cron. Works WITHOUT the Jarvis API (direct Telegram).
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

JARVIS_HOME = Path.home() / ".jarvis"
LOG_FILE = JARVIS_HOME / "logs" / "healer.log"
HEAL_HISTORY = JARVIS_HOME / "data" / "heal_history.jsonl"
STATE_FILE = JARVIS_HOME / "data" / "healer_state.json"

# Log rotation: rotate file when it exceeds this size (bytes)
LOG_ROTATE_BYTES = 5 * 1024 * 1024  # 5 MB
LOG_KEEP_BYTES = 2 * 1024 * 1024    # keep last 2 MB after rotation

# interactions.jsonl archive threshold
INTERACTIONS_MAX = 50 * 1024 * 1024  # 50 MB

SERVICES = [
    ("jarvis", "Jarvis API"),
    ("jarvis-telegram", "Jarvis Telegram Bot"),
    ("ollama", "Ollama LLM"),
]

# Models that must be present
REQUIRED_MODELS = ["deepseek-r1:7b", "mxbai-embed-large", "phi4-mini"]


def log(msg: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def record_heal(action: str, result: str, success: bool):
    HEAL_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now().isoformat(),
        "action": action,
        "result": result,
        "success": success,
    }
    with open(HEAL_HISTORY, "a") as f:
        f.write(json.dumps(entry) + "\n")


def send_telegram(text: str):
    """Send alert directly via Telegram Bot API — bypasses Jarvis API."""
    try:
        cfg_path = JARVIS_HOME / "config" / "telegram.json"
        token = json.loads(cfg_path.read_text()).get("bot_token", "")
        chat_id_path = JARVIS_HOME / "data" / "telegram_chat_id.json"
        chat_id = json.loads(chat_id_path.read_text()).get("chat_id", "")
        if not token or not chat_id:
            return
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        log(f"Telegram send failed: {e}")


def check_service(svc: str) -> bool:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", svc],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() == "active"
    except subprocess.TimeoutExpired:
        return False


def restart_service(svc: str) -> tuple[bool, str]:
    """Attempt service restart. Returns (success, output).

    Uses 'systemctl restart' (not 'start') because:
    - sudoers grants NOPASSWD restart for all three managed services
      but only grants NOPASSWD start for jarvis/jarvis-telegram/openclaw — NOT ollama.
    - The -n flag makes sudo fail fast (non-TTY cron context) rather than hanging
      on a password prompt until the 30s timeout expires.
    """
    r = subprocess.run(
        ["sudo", "-n", "systemctl", "restart", svc],
        capture_output=True,
        text=True,
        timeout=30,
    )
    time.sleep(5)
    is_up = check_service(svc)
    return is_up, r.stderr.strip() or r.stdout.strip()


def check_and_heal_services(state: dict) -> list[str]:
    alerts = []
    now = datetime.now().isoformat()
    for svc, label in SERVICES:
        is_up = check_service(svc)
        key = f"svc_{svc}"
        was_down = state.get(key, {}).get("down", False)

        if not is_up:
            log(f"Service DOWN: {svc}")
            if not was_down:
                # First detection — attempt auto-restart
                success, out = restart_service(svc)
                if success:
                    msg = f"🔧 *Jarvis Healer*: `{svc}` was DOWN — auto-restarted successfully."
                    log(f"Auto-restarted {svc}")
                    record_heal(f"restart_{svc}", "success", True)
                    send_telegram(msg)
                    alerts.append(msg)
                    state[key] = {"down": False, "last_recovered": now}
                else:
                    msg = f"🚨 *Jarvis Healer*: `{svc}` is DOWN and could not be restarted.\nManual fix needed: `sudo systemctl start {svc}`"
                    log(f"Failed to restart {svc}: {out}")
                    record_heal(f"restart_{svc}", f"failed: {out}", False)
                    send_telegram(msg)
                    alerts.append(msg)
                    state[key] = {"down": True, "since": now, "last_alert": now}
            else:
                last_alert = state[key].get("last_alert", "")
                # Re-alert every 30 minutes if still down
                if not last_alert or (datetime.now() - datetime.fromisoformat(last_alert)).seconds > 1800:
                    msg = f"🚨 *Jarvis Healer*: `{svc}` still DOWN — manual fix needed."
                    send_telegram(msg)
                    state[key]["last_alert"] = now
        else:
            if was_down:
                msg = f"✅ *Jarvis Healer*: `{svc}` has recovered."
                log(f"Service recovered: {svc}")
                send_telegram(msg)
            state[key] = {"down": False, "last_seen_up": now}
    return alerts


def rotate_log(log_path: Path):
    """Rotate a log file if it exceeds LOG_ROTATE_BYTES."""
    try:
        if not log_path.exists():
            return
        size = log_path.stat().st_size
        if size < LOG_ROTATE_BYTES:
            return
        # Keep last LOG_KEEP_BYTES
        content = log_path.read_bytes()
        trimmed = content[-LOG_KEEP_BYTES:]
        # Find next newline to avoid splitting a line
        nl = trimmed.find(b"\n")
        if nl > 0:
            trimmed = trimmed[nl + 1:]
        archive = log_path.with_suffix(f".{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak")
        log_path.rename(archive)
        log_path.write_bytes(trimmed)
        log(f"Rotated {log_path.name}: {size//1024}KB → kept {len(trimmed)//1024}KB, archive={archive.name}")
        # Remove archives older than 7 days (keep only 2 most recent)
        backups = sorted(log_path.parent.glob(f"{log_path.stem}.*.bak"), reverse=True)
        for old in backups[2:]:
            old.unlink()
            log(f"Deleted old log backup: {old.name}")
    except Exception as e:
        log(f"Log rotation error for {log_path.name}: {e}")


def check_and_rotate_logs():
    logs_dir = JARVIS_HOME / "logs"
    if not logs_dir.exists():
        return
    for lf in logs_dir.glob("*.log"):
        rotate_log(lf)


def check_interactions_size():
    """Archive interactions.jsonl if it gets too large."""
    ipath = JARVIS_HOME / "data" / "interactions.jsonl"
    if not ipath.exists():
        return
    size = ipath.stat().st_size
    if size > INTERACTIONS_MAX:
        archive = ipath.with_suffix(f".{datetime.now().strftime('%Y%m%d')}.bak")
        ipath.rename(archive)
        ipath.touch()
        log(f"Archived interactions.jsonl ({size//1024//1024}MB) → {archive.name}")
        send_telegram(f"📦 *Jarvis Healer*: `interactions.jsonl` was {size//1024//1024}MB — archived. Fresh log started.")


def check_disk_space():
    """Alert if disk is critically low."""
    import shutil
    usage = shutil.disk_usage("/")
    pct = usage.used / usage.total * 100
    if pct > 92:
        msg = f"🚨 *Jarvis Healer*: Disk at *{pct:.1f}%* — critical! Free: {usage.free//1024//1024//1024}GB"
        log(f"CRITICAL disk: {pct:.1f}%")
        send_telegram(msg)
        record_heal("disk_alert", f"{pct:.1f}%", False)


def check_ollama_models():
    """Verify required models are available in Ollama."""
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        if not r.ok:
            return
        models = [m.get("name", "") for m in r.json().get("models", [])]
        joined = " ".join(models).lower()
        missing = []
        for model in REQUIRED_MODELS:
            name_check = model.split(":")[0].lower()
            if name_check not in joined:
                missing.append(model)
        if missing:
            msg = f"⚠️ *Jarvis Healer*: Missing Ollama models: `{', '.join(missing)}`\nRun: `ollama pull <model>`"
            log(f"Missing models: {missing}")
            send_telegram(msg)
            record_heal("model_check", f"missing: {missing}", False)
    except Exception:
        pass  # Ollama down is caught by service check


def check_jarvis_api():
    """Verify Jarvis API responds to /health."""
    try:
        r = requests.get("http://localhost:8181/health", timeout=5)
        if r.ok:
            data = r.json()
            if data.get("ollama") != "ok":
                log("API healthy but Ollama connection bad")
                send_telegram("⚠️ *Jarvis Healer*: API is up but Ollama connection is failing inside API.")
        else:
            log(f"API health returned {r.status_code}")
    except Exception:
        # If API is unreachable here, service check will handle it
        pass


def cleanup_expired_approvals():
    """Clear expired pending approvals."""
    path = JARVIS_HOME / "data" / "pending_approvals.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
        now = datetime.now()
        cleaned = {}
        for rid, req in data.items():
            expires = req.get("expires_at", "")
            try:
                if expires and datetime.fromisoformat(expires) > now:
                    cleaned[rid] = req
            except Exception:
                pass
        if len(cleaned) < len(data):
            path.write_text(json.dumps(cleaned, indent=2))
            log(f"Cleaned {len(data) - len(cleaned)} expired approval requests")
    except Exception as e:
        log(f"Approval cleanup error: {e}")


def main():
    log("Healer run started")
    state = load_state()

    check_and_heal_services(state)
    check_and_rotate_logs()
    check_interactions_size()
    check_disk_space()
    check_ollama_models()
    check_jarvis_api()
    cleanup_expired_approvals()

    save_state(state)
    log("Healer run complete")


if __name__ == "__main__":
    main()
