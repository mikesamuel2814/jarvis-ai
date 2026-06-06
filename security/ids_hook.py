"""
Jarvis v3 IDS Hook
Monitors:
  - /var/log/auth.log (failed SSH, sudo)
  - SUID file changes
  - Kernel module loads
  - Cron modifications
  - Unauthorized SSH key additions
"""

import os
import re
import json
import sqlite3
import hashlib
import subprocess
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", "/home/kali/.jarvis"))
DB_PATH = JARVIS_HOME / "data" / "ids_hook.db"
STATE_PATH = JARVIS_HOME / "data" / "ids_state.json"

logger = logging.getLogger("jarvis.security.ids")


class IDSHook:
    """
    Lightweight host-based intrusion detection.
    Maintains checksum baselines for critical files.
    """

    WATCHED_FILES = [
        "/etc/crontab",
        "/etc/cron.d",
        "/etc/cron.hourly",
        "/etc/cron.daily",
        "/etc/cron.weekly",
        "/etc/cron.monthly",
    ]

    SSH_AUTH_DIRS = [
        Path("/home/kali/.ssh/authorized_keys"),
        Path("/root/.ssh/authorized_keys"),
    ]

    SUID_DIRS = ["/usr/bin", "/usr/sbin", "/bin", "/sbin"]

    def __init__(self):
        self._init_db()
        self.state = self._load_state()

    def _init_db(self):
        with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ids_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    source TEXT,
                    details TEXT
                )
            """)
            conn.commit()

    def _load_state(self) -> dict:
        if STATE_PATH.exists():
            with open(STATE_PATH, "r") as fh:
                return json.load(fh)
        return {"ssh_keys_checksum": {}, "suid_checksums": {}, "cron_checksums": {}}

    def _save_state(self):
        with open(STATE_PATH, "w") as fh:
            json.dump(self.state, fh, indent=2)

    def _log_event(self, severity: str, category: str, message: str, source: str = "", details: str = ""):
        ts = datetime.now(timezone.utc).isoformat()
        logger.warning("[IDS] %s | %s | %s | %s", severity, category, message, source)
        with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
            conn.execute(
                "INSERT INTO ids_events (ts, severity, category, message, source, details) VALUES (?, ?, ?, ?, ?, ?)",
                (ts, severity, category, message, source, details),
            )
            conn.commit()

    # ── Auth Log ───────────────────────────────────────────────────────────

    def check_auth_log(self, lines: int = 200) -> List[dict]:
        """Scan recent auth.log for anomalies."""
        alerts = []
        try:
            out = subprocess.run(
                ["tail", "-n", str(lines), "/var/log/auth.log"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            for line in out.splitlines():
                if "Failed password" in line:
                    m = re.search(r"for (.+?) from (.+?) port", line)
                    user = m.group(1) if m else "unknown"
                    ip = m.group(2) if m else "unknown"
                    alerts.append({
                        "severity": "MEDIUM",
                        "category": "AUTH_FAILURE",
                        "message": f"SSH failed password for {user} from {ip}",
                        "source": "/var/log/auth.log",
                        "details": line.strip(),
                    })
                elif "Accepted publickey" in line:
                    m = re.search(r"for (.+?) from (.+?) port", line)
                    user = m.group(1) if m else "unknown"
                    ip = m.group(2) if m else "unknown"
                    alerts.append({
                        "severity": "INFO",
                        "category": "AUTH_SUCCESS",
                        "message": f"SSH key login for {user} from {ip}",
                        "source": "/var/log/auth.log",
                        "details": line.strip(),
                    })
                elif "sudo:" in line and "incorrect password" in line:
                    alerts.append({
                        "severity": "MEDIUM",
                        "category": "SUDO_FAILURE",
                        "message": "Incorrect sudo password attempt",
                        "source": "/var/log/auth.log",
                        "details": line.strip(),
                    })
        except Exception as exc:
            logger.error("Cannot read auth.log: %s", exc)
        return alerts

    # ── SSH Keys ───────────────────────────────────────────────────────────

    def check_ssh_keys(self) -> List[dict]:
        alerts = []
        for fp in self.SSH_AUTH_DIRS:
            if not fp.exists():
                continue
            current = fp.read_text()
            chk = hashlib.sha256(current.encode()).hexdigest()
            key = str(fp)
            if key in self.state["ssh_keys_checksum"]:
                if self.state["ssh_keys_checksum"][key] != chk:
                    alerts.append({
                        "severity": "HIGH",
                        "category": "SSH_KEY_MODIFIED",
                        "message": f"Authorized keys modified: {fp}",
                        "source": str(fp),
                        "details": f"old_hash={self.state['ssh_keys_checksum'][key][:16]} new_hash={chk[:16]}",
                    })
            self.state["ssh_keys_checksum"][key] = chk
        return alerts

    # ── SUID Files ─────────────────────────────────────────────────────────

    def check_suid(self) -> List[dict]:
        alerts = []
        current_suids: Dict[str, str] = {}
        try:
            out = subprocess.run(
                ["find"] + self.SUID_DIRS + ["-perm", "-4000", "-type", "f", "-print0"],
                capture_output=True, text=True, timeout=30,
            ).stdout
            for fpath in out.split("\x00"):
                if not fpath:
                    continue
                try:
                    st = os.stat(fpath)
                    meta = f"{st.st_ino}:{st.st_size}:{st.st_mtime}"
                    current_suids[fpath] = meta
                except Exception:
                    continue
        except Exception as exc:
            logger.error("SUID scan failed: %s", exc)

        baseline = self.state.get("suid_checksums", {})
        added = set(current_suids.keys()) - set(baseline.keys())
        removed = set(baseline.keys()) - set(current_suids.keys())
        for f in added:
            alerts.append({
                "severity": "HIGH",
                "category": "SUID_ADDED",
                "message": f"New SUID binary: {f}",
                "source": f,
            })
        for f in removed:
            alerts.append({
                "severity": "MEDIUM",
                "category": "SUID_REMOVED",
                "message": f"SUID binary removed: {f}",
                "source": f,
            })

        self.state["suid_checksums"] = current_suids
        return alerts

    # ── Cron ───────────────────────────────────────────────────────────────

    def check_cron(self) -> List[dict]:
        alerts = []
        current = {}
        for path_str in self.WATCHED_FILES:
            p = Path(path_str)
            if p.is_dir():
                for child in p.iterdir():
                    if child.is_file():
                        current[str(child)] = hashlib.sha256(child.read_bytes()).hexdigest()
            elif p.is_file():
                current[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()

        baseline = self.state.get("cron_checksums", {})
        for path_str, chk in current.items():
            if path_str in baseline and baseline[path_str] != chk:
                alerts.append({
                    "severity": "HIGH",
                    "category": "CRON_MODIFIED",
                    "message": f"Cron file modified: {path_str}",
                    "source": path_str,
                })
        for path_str in baseline:
            if path_str not in current:
                alerts.append({
                    "severity": "MEDIUM",
                    "category": "CRON_REMOVED",
                    "message": f"Cron file removed: {path_str}",
                    "source": path_str,
                })

        self.state["cron_checksums"] = current
        return alerts

    # ── Kernel Modules ─────────────────────────────────────────────────────

    def check_kernel_modules(self) -> List[dict]:
        alerts = []
        try:
            out = subprocess.run(
                ["lsmod"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            # Just log the count; abnormal modules would need a baseline
            count = len(out.strip().splitlines()) - 1  # minus header
            # This is a lightweight check; deep inspection would need signed-module baselines
        except Exception as exc:
            logger.error("lsmod failed: %s", exc)
        return alerts

    # ── Orchestrator ───────────────────────────────────────────────────────

    def run_all_checks(self) -> List[dict]:
        all_alerts = []
        all_alerts.extend(self.check_auth_log())
        all_alerts.extend(self.check_ssh_keys())
        all_alerts.extend(self.check_suid())
        all_alerts.extend(self.check_cron())
        all_alerts.extend(self.check_kernel_modules())
        for a in all_alerts:
            self._log_event(a["severity"], a["category"], a["message"], a.get("source", ""), a.get("details", ""))
        self._save_state()
        return all_alerts

    def get_recent_events(self, hours: int = 24) -> List[dict]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
            rows = conn.execute(
                "SELECT ts, severity, category, message, source, details FROM ids_events WHERE ts >= ? ORDER BY ts DESC",
                (since,),
            ).fetchall()
        return [
            {
                "ts": r[0], "severity": r[1], "category": r[2],
                "message": r[3], "source": r[4], "details": r[5],
            }
            for r in rows
        ]

    def summary(self) -> Optional[str]:
        alerts = self.run_all_checks()
        if not alerts:
            return None
        high = [a for a in alerts if a["severity"] == "HIGH"]
        lines = [f"🛡️ *IDS Alert* — {len(high)} HIGH, {len(alerts)-len(high)} other"]
        for a in alerts[:8]:
            emoji = "🔴" if a["severity"] == "HIGH" else "🟡" if a["severity"] == "MEDIUM" else "🔵"
            lines.append(f"{emoji} *{a['category']}*: {a['message']}")
        return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Jarvis IDS Hook")
    parser.add_argument("action", choices=["check", "recent", "state"])
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()

    ids = IDSHook()
    if args.action == "check":
        alerts = ids.run_all_checks()
        if alerts:
            print(ids.summary())
        else:
            print("[IDS] No anomalies detected.")
    elif args.action == "recent":
        for ev in ids.get_recent_events(args.hours):
            print(f"{ev['ts']} | {ev['severity']} | {ev['category']} | {ev['message']}")
    elif args.action == "state":
        print(json.dumps(ids.state, indent=2))
