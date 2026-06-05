#!/usr/bin/env python3
"""
Jarvis Monitor — watches VPS and Kali systems, sends Telegram alerts.
Run every 5 min via cron: */5 * * * * /usr/bin/python3 /home/kali/.jarvis/monitor.py
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import psutil
import requests
import yaml

JARVIS_HOME = Path.home() / ".jarvis"
CONFIG_FILE = JARVIS_HOME / "config" / "jarvis.yaml"
LOG_FILE = JARVIS_HOME / "logs" / "monitor.log"
STATE_FILE = JARVIS_HOME / "data" / "monitor_state.json"

# Alert thresholds
CPU_THRESHOLD = 85
RAM_THRESHOLD = 85
DISK_THRESHOLD = 90
GPU_TEMP_THRESHOLD = 85


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
    token_file = JARVIS_HOME / "config" / "telegram.json"
    if not token_file.exists():
        return
    try:
        token = json.loads(token_file.read_text()).get("bot_token", "")
        if not token or token == "YOUR_BOT_TOKEN_HERE":
            return
        # Try API endpoint first
        api_port = cfg.get("interfaces", {}).get("api_port", 8181)
        try:
            requests.post(
                f"http://localhost:{api_port}/telegram/send",
                json={"text": msg},
                timeout=10,
            )
            return
        except Exception:
            pass
        # Fallback: get chat_id from recent updates and send directly
        updates = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            timeout=10,
        ).json()
        messages = updates.get("result", [])
        if not messages:
            return
        chat_id = messages[-1]["message"]["chat"]["id"]
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        log(f"Telegram send failed: {e}")


def check_kali(state: dict, alerts: list):
    """Check local Kali machine health."""
    try:
        cpu = psutil.cpu_percent(interval=2)
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent

        if cpu > CPU_THRESHOLD and state.get("cpu_alerted") != True:
            alerts.append(f"⚠️ Kali CPU high: {cpu}%")
            state["cpu_alerted"] = True
        elif cpu <= CPU_THRESHOLD:
            state["cpu_alerted"] = False

        if ram > RAM_THRESHOLD and not state.get("ram_alerted"):
            alerts.append(f"⚠️ Kali RAM high: {ram}%")
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
        r = subprocess.run(
            ["systemctl", "is-active", svc],
            capture_output=True, text=True,
        )
        is_active = r.stdout.strip() == "active"
        was_down = state.get(f"svc_down_{svc}", False)
        if not is_active and not was_down:
            alerts.append(f"🔴 Service DOWN: {svc}")
            state[f"svc_down_{svc}"] = True
        elif is_active and was_down:
            alerts.append(f"✅ Service recovered: {svc}")
            state[f"svc_down_{svc}"] = False


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
                    # Extract process name
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
        import socket
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


def main():
    cfg = load_config()
    state = load_state()
    alerts = []

    check_kali(state, alerts)
    check_gpu(state, alerts)
    check_services(state, alerts)
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

    # Run AI decision engine
    try:
        sys.path.insert(0, str(JARVIS_HOME))
        from decision_engine import run_decision_engine
        run_decision_engine()
    except Exception as e:
        log(f"Decision engine error: {e}")


if __name__ == "__main__":
    main()
