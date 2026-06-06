#!/usr/bin/env python3
"""
Jarvis Monitor — watches VPS and Kali systems, sends Telegram alerts.
Run every 5 min via cron: */5 * * * * /usr/bin/python3 /home/kali/.jarvis/monitor.py
"""

import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import psutil
import requests
import yaml

JARVIS_HOME = Path.home() / ".jarvis"
CONFIG_FILE = JARVIS_HOME / "config" / "jarvis.yaml"
LOG_FILE = JARVIS_HOME / "logs" / "monitor.log"
STATE_FILE = JARVIS_HOME / "data" / "monitor_state.json"
INTERACTIONS_FILE = JARVIS_HOME / "data" / "interactions.jsonl"

# Alert thresholds
CPU_THRESHOLD = 85
RAM_THRESHOLD = 85
DISK_THRESHOLD = 90
GPU_TEMP_THRESHOLD = 85
LOG_SIZE_WARN_MB = 3


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return yaml.safe_load(CONFIG_FILE.read_text()) or {}
    return {}


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
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def send_telegram(msg: str, cfg: dict):
    """Send directly via Telegram Bot API using stored chat_id — never calls getUpdates."""
    try:
        token_file = JARVIS_HOME / "config" / "telegram.json"
        chat_id_file = JARVIS_HOME / "data" / "telegram_chat_id.json"
        if not token_file.exists() or not chat_id_file.exists():
            return
        token = json.loads(token_file.read_text()).get("bot_token", "")
        chat_id = json.loads(chat_id_file.read_text()).get("chat_id", "")
        if not token or not chat_id or token == "YOUR_BOT_TOKEN_HERE":
            return
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        log(f"Telegram send failed: {e}")


# ---------------------------------------------------------------------------
# 1. Jarvis uptime tracking
# ---------------------------------------------------------------------------

def check_jarvis_uptime(state: dict, alerts: list):
    """Track jarvis.service uptime; warn if recently restarted."""
    try:
        r = subprocess.run(
            ["systemctl", "show", "jarvis", "--property=ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return
        raw = r.stdout.strip()
        # Format: ActiveEnterTimestamp=Thu 2025-01-01 00:00:00 UTC
        if "=" not in raw:
            return
        ts_str = raw.split("=", 1)[1].strip()
        if not ts_str or ts_str == "n/a":
            return
        # Parse timestamp — systemd format varies, try a few
        for fmt in ("%a %Y-%m-%d %H:%M:%S %Z", "%a %Y-%m-%d %H:%M:%S"):
            try:
                restart_time = datetime.strptime(ts_str, fmt)
                break
            except ValueError:
                continue
        else:
            return

        now = datetime.now()
        minutes_up = int((now - restart_time).total_seconds() / 60)

        # Store for daily summary
        state["jarvis_uptime_minutes"] = minutes_up
        state["jarvis_last_restart"] = ts_str

        # Notify if within last 10 minutes and not already notified for this restart
        last_notified = state.get("jarvis_restart_notified_ts", "")
        if minutes_up <= 10 and last_notified != ts_str:
            alerts.append(
                f"ℹ️ Jarvis API restarted {minutes_up} minute{'s' if minutes_up != 1 else ''} ago (was down briefly)"
            )
            state["jarvis_restart_notified_ts"] = ts_str

    except Exception as e:
        log(f"Jarvis uptime check error: {e}")


# ---------------------------------------------------------------------------
# 2. Improved CPU/RAM alerts with top process
# ---------------------------------------------------------------------------

def get_top_cpu_process() -> str:
    """Return name and CPU% of the top CPU-consuming process."""
    try:
        # First call seeds the cpu_percent counters
        procs = list(psutil.process_iter(['name', 'cpu_percent', 'memory_percent']))
        import time as _time
        _time.sleep(0.3)
        # Refresh
        procs = list(psutil.process_iter(['name', 'cpu_percent', 'memory_percent']))
        top = max(procs, key=lambda p: p.info.get('cpu_percent') or 0.0)
        return f"{top.info['name']} ({top.info['cpu_percent']:.1f}%)"
    except Exception:
        return "unknown"


def get_top_ram_process() -> str:
    """Return name and RAM% of the top RAM-consuming process."""
    try:
        procs = list(psutil.process_iter(['name', 'memory_percent']))
        top = max(procs, key=lambda p: p.info.get('memory_percent') or 0.0)
        return f"{top.info['name']} ({top.info['memory_percent']:.1f}%)"
    except Exception:
        return "unknown"


def check_kali(state: dict, alerts: list):
    """Check local Kali machine health."""
    try:
        cpu = psutil.cpu_percent(interval=2)
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent

        if cpu > CPU_THRESHOLD and state.get("cpu_alerted") != True:
            top = get_top_cpu_process()
            alerts.append(f"⚠️ Kali CPU high: {cpu}% — Top process: {top}")
            state["cpu_alerted"] = True
        elif cpu <= CPU_THRESHOLD:
            state["cpu_alerted"] = False

        if ram > RAM_THRESHOLD and not state.get("ram_alerted"):
            top = get_top_ram_process()
            alerts.append(f"⚠️ Kali RAM high: {ram}% — Top process: {top}")
            state["ram_alerted"] = True
        elif ram <= RAM_THRESHOLD:
            state["ram_alerted"] = False

        if disk > DISK_THRESHOLD and not state.get("disk_alerted"):
            alerts.append(f"⚠️ Kali disk full: {disk}%")
            state["disk_alerted"] = True
        elif disk <= DISK_THRESHOLD:
            state["disk_alerted"] = False

    except Exception as e:
        log(f"Kali health check error: {e}")


def check_gpu(state: dict, alerts: list):
    """Check GPU temperature."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            parts = [p.strip() for p in r.stdout.strip().split(",")]
            temp = int(parts[0])
            if temp > GPU_TEMP_THRESHOLD and not state.get("gpu_alerted"):
                alerts.append(f"🔥 GPU temp high: {temp}°C")
                state["gpu_alerted"] = True
            elif temp <= GPU_TEMP_THRESHOLD:
                state["gpu_alerted"] = False
    except Exception:
        pass


def check_services(state: dict, alerts: list):
    """Check critical systemd services are running."""
    services = ["ollama", "jarvis", "jarvis-telegram"]
    for svc in services:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=5,
            )
        except subprocess.TimeoutExpired:
            continue
        is_active = r.stdout.strip() == "active"
        was_down = state.get(f"svc_down_{svc}", False)
        if not is_active and not was_down:
            alerts.append(f"🔴 Service DOWN: {svc}")
            state[f"svc_down_{svc}"] = True
        elif is_active and was_down:
            alerts.append(f"✅ Service recovered: {svc}")
            state[f"svc_down_{svc}"] = False


# ---------------------------------------------------------------------------
# 3. Ollama model load check
# ---------------------------------------------------------------------------

def check_ollama_model(state: dict, alerts: list):
    """
    If interactions.jsonl was modified recently, verify deepseek-r1:7b is
    still loaded in Ollama.
    """
    try:
        # Only check if interactions file touched in last 30 minutes
        if INTERACTIONS_FILE.exists():
            mtime = INTERACTIONS_FILE.stat().st_mtime
            age_minutes = (datetime.now().timestamp() - mtime) / 60
            if age_minutes > 30:
                # No recent queries, skip check
                return
        else:
            return

        r = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/tags"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return

        data = json.loads(r.stdout)
        model_names = " ".join(m["name"] for m in data.get("models", []))
        deepseek_loaded = "deepseek" in model_names

        if not deepseek_loaded and not state.get("ollama_model_missing"):
            alerts.append("⚠️ Ollama: deepseek-r1:7b is NOT loaded — model may have been evicted")
            state["ollama_model_missing"] = True
        elif deepseek_loaded:
            state["ollama_model_missing"] = False

    except Exception as e:
        log(f"Ollama model check error: {e}")


# ---------------------------------------------------------------------------
# 4. Network connectivity check
# ---------------------------------------------------------------------------

def check_internet(state: dict, alerts: list):
    """
    Check internet connectivity via DNS port.
    Alert only once per hour to avoid spam.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(("8.8.8.8", 53))
        sock.close()
        online = True
    except Exception:
        online = False

    last_internet_alert = state.get("internet_alert_time", "")
    now_str = datetime.now().strftime("%Y-%m-%d %H")  # hour-level key

    if not online:
        if last_internet_alert != now_str and not state.get("internet_down"):
            alerts.append("🌐 Internet connectivity lost — cannot reach 8.8.8.8:53")
            state["internet_down"] = True
            state["internet_alert_time"] = now_str
    else:
        if state.get("internet_down"):
            alerts.append("✅ Internet connectivity restored")
        state["internet_down"] = False


# ---------------------------------------------------------------------------
# 5. Git sync check — is ~/.jarvis behind remote?
# ---------------------------------------------------------------------------

def check_git_sync(state: dict, alerts: list):
    """Warn if local ~/.jarvis repo is behind its remote."""
    try:
        r = subprocess.run(
            ["git", "-C", str(JARVIS_HOME), "fetch", "--dry-run"],
            capture_output=True, text=True, timeout=10,
        )
        output = (r.stdout + r.stderr).strip()
        # 'git fetch --dry-run' prints lines like:
        #   = [up to date]      main -> origin/main  (nothing to do)
        #   * branch            main -> FETCH_HEAD    (behind)
        # Any non-empty output that isn't purely "up to date" means behind
        is_behind = bool(output) and "up to date" not in output

        if is_behind and not state.get("git_behind_alerted"):
            alerts.append(f"🔄 ~/.jarvis repo is behind remote — consider pulling:\n`git -C ~/.jarvis pull`")
            state["git_behind_alerted"] = True
        elif not is_behind:
            state["git_behind_alerted"] = False

    except Exception as e:
        log(f"Git sync check error: {e}")


# ---------------------------------------------------------------------------
# 6. Daily summary at first run after midnight
# ---------------------------------------------------------------------------

def check_daily_summary(cfg: dict, state: dict):
    """Send overnight summary once per day at the first run after 00:01."""
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    if state.get("last_daily_summary") == today_str:
        return  # Already sent today
    if now.hour != 0:
        return  # Only send in the midnight hour (00:xx)

    try:
        # Memory chunk count
        try:
            mem_r = requests.get("http://localhost:8181/health", timeout=5)
            chunk_count = mem_r.json().get("memory_chunks", "N/A") if mem_r.ok else "N/A"
        except Exception:
            chunk_count = "N/A"

        # Disk usage
        disk_pct = psutil.disk_usage("/").percent

        # Last training time
        last_train_file = JARVIS_HOME / "data" / "last_training.txt"
        if last_train_file.exists():
            last_train = last_train_file.read_text().strip()
        else:
            last_train = "unknown"

        # Service uptime minutes (populated by check_jarvis_uptime)
        uptime_min = state.get("jarvis_uptime_minutes", 0)
        if uptime_min >= 60:
            uptime_str = f"{uptime_min // 60}h {uptime_min % 60}m"
        else:
            uptime_str = f"{uptime_min}m"

        # VPS PM2 summary (brief)
        vps_host = cfg.get("openclaw", {}).get("vps_host", "")
        vps_status = "N/A"
        if vps_host:
            try:
                vr = subprocess.run(
                    ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                     f"admin93@{vps_host}",
                     "pm2 list --no-color 2>/dev/null | grep -c online || echo 0"],
                    capture_output=True, text=True, timeout=12,
                )
                if vr.returncode == 0:
                    count = vr.stdout.strip().splitlines()[-1].strip()
                    vps_status = f"{count} PM2 processes online"
            except Exception:
                vps_status = "unreachable"

        msg = (
            f"🌙 Jarvis overnight summary ({today_str}):\n"
            f"• Services: all up {uptime_str}\n"
            f"• Memory: {chunk_count} chunks\n"
            f"• Disk: {disk_pct:.1f}% used\n"
            f"• Last training: {last_train}\n"
            f"• VPS: {vps_status}"
        )

        send_telegram(msg, cfg)
        log(f"Daily summary sent for {today_str}")
        state["last_daily_summary"] = today_str

    except Exception as e:
        log(f"Daily summary error: {e}")


# ---------------------------------------------------------------------------
# 7. Log rotation warning
# ---------------------------------------------------------------------------

def check_log_sizes(state: dict, alerts: list):
    """Warn if any log file in ~/.jarvis/logs/ exceeds LOG_SIZE_WARN_MB."""
    logs_dir = JARVIS_HOME / "logs"
    if not logs_dir.exists():
        return
    try:
        for log_path in logs_dir.glob("*.log"):
            size_mb = log_path.stat().st_size / (1024 * 1024)
            if size_mb > LOG_SIZE_WARN_MB:
                key = f"log_size_warned_{log_path.name}"
                if not state.get(key):
                    alerts.append(
                        f"📁 Log file large: {log_path.name} is {size_mb:.1f}MB "
                        f"(>{LOG_SIZE_WARN_MB}MB) — healer.py should rotate"
                    )
                    state[key] = True
            else:
                state[f"log_size_warned_{log_path.name}"] = False
    except Exception as e:
        log(f"Log size check error: {e}")


# ---------------------------------------------------------------------------
# VPS / SSL (unchanged)
# ---------------------------------------------------------------------------

def check_vps(cfg: dict, state: dict, alerts: list):
    """Check VPS health via SSH."""
    vps_host = cfg.get("openclaw", {}).get("vps_host", "")
    if not vps_host:
        return
    user = "admin93"
    try:
        # Quick connectivity check
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
             f"{user}@{vps_host}", "uptime && pm2 list --no-color 2>/dev/null | grep -E 'online|stopped|errored' | head -10"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            if not state.get("vps_down"):
                alerts.append(f"🔴 VPS unreachable: {vps_host}")
                state["vps_down"] = True
        else:
            if state.get("vps_down"):
                alerts.append(f"✅ VPS back online: {vps_host}")
                state["vps_down"] = False
            # Check for stopped PM2 processes
            output = r.stdout
            for line in output.splitlines():
                if "stopped" in line or "errored" in line:
                    parts = line.split()
                    if len(parts) > 1:
                        name = parts[1].strip("│").strip()
                        key = f"pm2_down_{name}"
                        if not state.get(key):
                            alerts.append(f"⚠️ PM2 process down: {name}")
                            state[key] = True
                elif "online" in line:
                    parts = line.split()
                    if len(parts) > 1:
                        name = parts[1].strip("│").strip()
                        key = f"pm2_down_{name}"
                        if state.get(key):
                            alerts.append(f"✅ PM2 recovered: {name}")
                            state[key] = False
    except subprocess.TimeoutExpired:
        if not state.get("vps_timeout"):
            alerts.append(f"⚠️ VPS SSH timeout: {vps_host}")
            state["vps_timeout"] = True
    except Exception as e:
        log(f"VPS check error: {e}")


def check_ssl(cfg: dict, alerts: list):
    """Check SSL certificate expiry on VPS."""
    vps_host = cfg.get("openclaw", {}).get("vps_host", "")
    if not vps_host:
        return
    try:
        import ssl
        for domain in [vps_host]:
            try:
                ctx = ssl.create_default_context()
                conn = ctx.wrap_socket(socket.socket(), server_hostname=domain)
                conn.settimeout(5)
                conn.connect((domain, 443))
                cert = conn.getpeercert()
                conn.close()
                expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                days_left = (expires - datetime.now()).days
                if days_left < 30:
                    alerts.append(f"⚠️ SSL cert for {domain} expires in {days_left} days")
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    cfg = load_config()
    state = load_state()
    alerts = []

    check_jarvis_uptime(state, alerts)   # 1. uptime tracking
    check_kali(state, alerts)            # 2. CPU/RAM with top process
    check_gpu(state, alerts)
    check_services(state, alerts)
    check_ollama_model(state, alerts)    # 3. Ollama model load check
    check_internet(state, alerts)        # 4. Network connectivity
    check_git_sync(state, alerts)        # 5. Git sync check
    check_log_sizes(state, alerts)       # 7. Log size warnings
    check_vps(cfg, state, alerts)
    check_ssl(cfg, alerts)

    save_state(state)

    if alerts:
        msg = "🤖 Jarvis Monitor Alert\n" + "\n".join(alerts)
        log(f"Sending {len(alerts)} alerts")
        send_telegram(msg, cfg)
        for a in alerts:
            log(f"ALERT: {a}")
    else:
        log("All systems OK")

    check_daily_summary(cfg, state)      # 6. Daily summary (sends its own message)
    save_state(state)                    # Save again after daily summary updates state

    # Run AI decision engine
    try:
        sys.path.insert(0, str(JARVIS_HOME))
        from decision_engine import run_decision_engine
        run_decision_engine()
    except Exception as e:
        log(f"Decision engine error: {e}")


if __name__ == "__main__":
    main()
