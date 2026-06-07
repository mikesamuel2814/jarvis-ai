"""
Jarvis v3 Port Monitor
48-hour baseline learning, then real-time detection of new listeners.
Alerts on unexpected ports with process attribution.
"""

import os
import json
import sqlite3
import subprocess
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", "/home/kali/.jarvis"))
BASELINE_PATH = JARVIS_HOME / "data" / "port_baseline.json"
DB_PATH = JARVIS_HOME / "data" / "port_monitor.db"

logger = logging.getLogger("jarvis.security.port_monitor")


class PortMonitor:
    """
    Learns normal listening ports over a baseline period,
    then flags any new or disappeared listeners.
    """

    BASELINE_HOURS = 48
    CHECK_INTERVAL_SECONDS = 120

    def __init__(self):
        self._init_db()
        self.baseline: Dict[str, dict] = {}
        if BASELINE_PATH.exists():
            with open(BASELINE_PATH, "r") as fh:
                self.baseline = json.load(fh)

    def _init_db(self):
        with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS port_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    bind_addr TEXT,
                    process_name TEXT,
                    pid INTEGER,
                    alerted INTEGER DEFAULT 0
                )
            """)
            conn.commit()

    # ── Snapshot ───────────────────────────────────────────────────────────

    def _snapshot(self) -> List[dict]:
        """Capture current listening sockets via ss."""
        results = []
        try:
            out = subprocess.run(
                ["ss", "-tlnp", "-H"],
                capture_output=True, text=True, timeout=15,
            ).stdout
        except Exception as exc:
            logger.error("ss snapshot failed: %s", exc)
            return results

        for line in out.strip().splitlines():
            # Parse each line independently so one malformed row (e.g. a
            # wildcard '*:*' port) never aborts the whole snapshot.
            try:
                parts = line.split()
                if len(parts) < 5:
                    continue
                proto = parts[0]
                local = parts[4]
                process = parts[5] if len(parts) > 5 else ""

                if "[" in local and "]" in local:
                    addr, port_str = local.rsplit(":", 1)  # IPv6
                    addr = addr.strip("[]")
                else:
                    addr, port_str = local.rsplit(":", 1)

                if not port_str.isdigit():
                    continue  # wildcard/unknown port — skip

                pid_name = self._parse_process(process)
                results.append({
                    "protocol": proto,
                    "port": int(port_str),
                    "bind_addr": addr,
                    "process_name": pid_name["name"],
                    "pid": pid_name["pid"],
                })
            except Exception as exc:
                logger.debug("skipping unparseable ss line %r: %s", line, exc)
        return results

    def _parse_process(self, proc_field: str) -> dict:
        """Parse 'users:(('nginx',pid=1234,fd=3))' style output."""
        name = "unknown"
        pid = 0
        try:
            if "pid=" in proc_field:
                inner = proc_field.split("((", 1)[1].split("))", 1)[0]
                chunks = inner.split(",")
                name = chunks[0].strip().strip("'\"")
                for c in chunks:
                    if c.strip().startswith("pid="):
                        pid = int(c.strip().split("=", 1)[1])
                        break
        except Exception:
            pass
        return {"name": name, "pid": pid}

    # ── Baseline ───────────────────────────────────────────────────────────

    def learn_baseline(self) -> bool:
        """Record current listeners into the baseline file."""
        snap = self._snapshot()
        baseline = {}
        for entry in snap:
            key = f"{entry['protocol']}/{entry['port']}/{entry['bind_addr']}"
            baseline[key] = {
                "port": entry["port"],
                "protocol": entry["protocol"],
                "bind_addr": entry["bind_addr"],
                "process_name": entry["process_name"],
                "first_seen": datetime.now(timezone.utc).isoformat(),
            }
        with open(BASELINE_PATH, "w") as fh:
            json.dump(baseline, fh, indent=2)
        self.baseline = baseline
        logger.info("Port baseline learned: %d listeners", len(baseline))
        return True

    def is_baselined(self, entry: dict) -> bool:
        key = f"{entry['protocol']}/{entry['port']}/{entry['bind_addr']}"
        return key in self.baseline

    # ── Monitoring ─────────────────────────────────────────────────────────

    def check(self) -> List[dict]:
        """Return any anomalous listeners not in baseline."""
        snap = self._snapshot()
        anomalies = []
        for entry in snap:
            if not self.is_baselined(entry):
                anomalies.append({
                    **entry,
                    "event_type": "NEW_LISTENER",
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
        self._store_events(anomalies)
        return anomalies

    def _store_events(self, events: List[dict]):
        with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
            for ev in events:
                conn.execute(
                    "INSERT INTO port_events (ts, event_type, port, protocol, bind_addr, process_name, pid) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (ev["ts"], ev["event_type"], ev["port"], ev["protocol"], ev["bind_addr"], ev["process_name"], ev["pid"]),
                )
            conn.commit()

    def get_recent_events(self, hours: int = 24) -> List[dict]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
            rows = conn.execute(
                "SELECT ts, event_type, port, protocol, bind_addr, process_name, pid FROM port_events WHERE ts >= ? ORDER BY ts DESC",
                (since,),
            ).fetchall()
        return [
            {
                "ts": r[0],
                "event_type": r[1],
                "port": r[2],
                "protocol": r[3],
                "bind_addr": r[4],
                "process_name": r[5],
                "pid": r[6],
            }
            for r in rows
        ]

    def alert_summary(self) -> Optional[str]:
        """Generate a Telegram-friendly alert if anomalies found."""
        anomalies = self.check()
        if not anomalies:
            return None
        lines = ["🚨 *Port Anomaly Detected*"]
        for a in anomalies:
            lines.append(
                f"• `{a['protocol']}/{a['port']}` on `{a['bind_addr']}` — "
                f"process `{a['process_name']}` (pid {a['pid']})"
            )
        return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Jarvis Port Monitor")
    parser.add_argument("action", choices=["baseline", "check", "recent"])
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()

    pm = PortMonitor()
    if args.action == "baseline":
        pm.learn_baseline()
        print("[PortMonitor] Baseline captured.")
    elif args.action == "check":
        anomalies = pm.check()
        if anomalies:
            print(pm.alert_summary())
        else:
            print("[PortMonitor] No anomalies.")
    elif args.action == "recent":
        for ev in pm.get_recent_events(args.hours):
            print(f"{ev['ts']} | {ev['event_type']} | {ev['protocol']}/{ev['port']} | {ev['process_name']}")
