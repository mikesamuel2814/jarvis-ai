"""
Jarvis v3 Key Rotation Manager
Tracks secret ages and reminds/forces rotation:
  - API keys: 90 days
  - Service accounts: 30 days
  - SSH keys: 180 days
  - SSL certs: auto-renew 14 days before expiry
"""

import os
import json
import sqlite3
import re
import subprocess
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", "/home/kali/.jarvis"))
DB_PATH = JARVIS_HOME / "data" / "key_rotator.db"

logger = logging.getLogger("jarvis.security.key_rotator")


class KeyRotator:
    """
    Maintains a registry of all tracked secrets/credentials with rotation policies.
    Integrates with the Secret Vault for encrypted storage.
    """

    DEFAULT_POLICIES = {
        "api_key": {"days": 90, "severity": "HIGH"},
        "service_account": {"days": 30, "severity": "HIGH"},
        "ssh_key": {"days": 180, "severity": "MEDIUM"},
        "ssl_cert": {"days": 90, "severity": "CRITICAL", "renew_before_days": 14},
        "bot_token": {"days": 90, "severity": "HIGH"},
        "webhook_secret": {"days": 90, "severity": "MEDIUM"},
        "database_password": {"days": 90, "severity": "HIGH"},
        "oauth_refresh": {"days": 30, "severity": "MEDIUM"},
    }

    def __init__(self):
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS key_registry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_name TEXT UNIQUE NOT NULL,
                    key_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_rotated TEXT,
                    next_due TEXT,
                    severity TEXT,
                    auto_renew INTEGER DEFAULT 0,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rotation_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    key_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    old_hint TEXT,
                    new_hint TEXT,
                    initiated_by TEXT
                )
            """)
            conn.commit()

    def register(
        self,
        key_name: str,
        key_type: str,
        created_at: Optional[datetime] = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Register a new secret for rotation tracking."""
        policy = self.DEFAULT_POLICIES.get(key_type, {"days": 90, "severity": "MEDIUM"})
        created = created_at or datetime.now(timezone.utc)
        due = created + timedelta(days=policy["days"])
        try:
            with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
                conn.execute(
                    """INSERT INTO key_registry
                        (key_name, key_type, created_at, last_rotated, next_due, severity, auto_renew, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(key_name) DO UPDATE SET
                         key_type=excluded.key_type,
                         next_due=excluded.next_due,
                         severity=excluded.severity,
                         metadata=excluded.metadata""",
                    (
                        key_name,
                        key_type,
                        created.isoformat(),
                        created.isoformat(),
                        due.isoformat(),
                        policy["severity"],
                        1 if policy.get("renew_before_days") else 0,
                        json.dumps(metadata or {}),
                    ),
                )
                conn.commit()
            return True
        except Exception as exc:
            logger.error("Failed to register key %s: %s", key_name, exc)
            return False

    def check_rotation_due(self) -> List[dict]:
        """Return all secrets that need rotation."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
            rows = conn.execute(
                "SELECT key_name, key_type, last_rotated, next_due, severity, metadata FROM key_registry WHERE next_due <= ? ORDER BY severity, next_due",
                (now,),
            ).fetchall()
        return [
            {
                "key_name": r[0],
                "key_type": r[1],
                "last_rotated": r[2],
                "next_due": r[3],
                "severity": r[4],
                "metadata": json.loads(r[5]),
            }
            for r in rows
        ]

    def check_upcoming(self, days: int = 7) -> List[dict]:
        """Return secrets due within N days."""
        future = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
            rows = conn.execute(
                "SELECT key_name, key_type, next_due, severity FROM key_registry WHERE next_due > ? AND next_due <= ? ORDER BY next_due",
                (now, future),
            ).fetchall()
        return [
            {"key_name": r[0], "key_type": r[1], "next_due": r[2], "severity": r[3]}
            for r in rows
        ]

    def record_rotation(self, key_name: str, old_hint: str = "", new_hint: str = "", initiated_by: str = "user") -> bool:
        now = datetime.now(timezone.utc)
        policy = self.DEFAULT_POLICIES.get("api_key", {"days": 90})
        # Try to infer policy from existing record
        with sqlite3.connect(str(DB_PATH), check_same_thread=False) as conn:
            row = conn.execute("SELECT key_type FROM key_registry WHERE key_name=?", (key_name,)).fetchone()
            if row:
                policy = self.DEFAULT_POLICIES.get(row[0], {"days": 90})
            due = now + timedelta(days=policy["days"])
            conn.execute(
                """UPDATE key_registry SET last_rotated=?, next_due=? WHERE key_name=?""",
                (now.isoformat(), due.isoformat(), key_name),
            )
            conn.execute(
                "INSERT INTO rotation_log (ts, key_name, action, old_hint, new_hint, initiated_by) VALUES (?, ?, ?, ?, ?, ?)",
                (now.isoformat(), key_name, "ROTATED", old_hint, new_hint, initiated_by),
            )
            conn.commit()
        return True

    # ── SSL Cert Checks ────────────────────────────────────────────────────

    def check_ssl_certs(self, cert_paths: Optional[List[Path]] = None) -> List[dict]:
        """Check SSL certificate expiry."""
        alerts = []
        paths = cert_paths or [
            Path("/etc/nginx/ssl/fullchain.pem"),
            Path("/etc/letsencrypt/live"),
        ]
        for p in paths:
            if p.is_dir():
                for cert in p.rglob("fullchain.pem"):
                    alerts.extend(self._check_one_cert(cert))
            elif p.is_file():
                alerts.extend(self._check_one_cert(p))
        return alerts

    def _check_one_cert(self, cert_path: Path) -> List[dict]:
        alerts = []
        try:
            out = subprocess.run(
                ["openssl", "x509", "-in", str(cert_path), "-noout", "-dates"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            m = re.search(r"notAfter=(.+)", out)
            if m:
                expiry = datetime.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z")
                expiry = expiry.replace(tzinfo=timezone.utc)
                days_left = (expiry - datetime.now(timezone.utc)).days
                if days_left <= 14:
                    alerts.append({
                        "severity": "CRITICAL",
                        "key_name": str(cert_path),
                        "key_type": "ssl_cert",
                        "message": f"SSL cert expires in {days_left} days",
                        "next_due": expiry.isoformat(),
                    })
                elif days_left <= 30:
                    alerts.append({
                        "severity": "HIGH",
                        "key_name": str(cert_path),
                        "key_type": "ssl_cert",
                        "message": f"SSL cert expires in {days_left} days",
                        "next_due": expiry.isoformat(),
                    })
        except Exception as exc:
            logger.warning("Cert check failed for %s: %s", cert_path, exc)
        return alerts

    def summary(self) -> Optional[str]:
        due = self.check_rotation_due()
        upcoming = self.check_upcoming(7)
        ssl = self.check_ssl_certs()
        if not due and not upcoming and not ssl:
            return None
        lines = ["🔑 *Key Rotation Status*"]
        if due:
            lines.append(f"*Overdue:* {len(due)} secrets need rotation now")
            for d in due[:5]:
                lines.append(f"  • `{d['key_name']}` ({d['key_type']}) — {d['severity']}")
        if upcoming:
            lines.append(f"*Upcoming (7d):* {len(upcoming)} secrets")
        if ssl:
            lines.append(f"*SSL Certs:* {len(ssl)} need attention")
        return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Jarvis Key Rotator")
    parser.add_argument("action", choices=["register", "check", "upcoming", "rotate", "summary"])
    parser.add_argument("--key", "-k")
    parser.add_argument("--type", "-t", default="api_key")
    parser.add_argument("--hint", default="")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    kr = KeyRotator()
    if args.action == "register":
        kr.register(args.key, args.type)
        print(f"[KeyRotator] Registered '{args.key}' as {args.type}.")
    elif args.action == "check":
        due = kr.check_rotation_due()
        if due:
            for d in due:
                print(f"OVERDUE: {d['key_name']} ({d['key_type']}) since {d['next_due']}")
        else:
            print("[KeyRotator] No overdue keys.")
    elif args.action == "upcoming":
        up = kr.check_upcoming(args.days)
        for u in up:
            print(f"UPCOMING: {u['key_name']} ({u['key_type']}) due {u['next_due']}")
    elif args.action == "rotate":
        kr.record_rotation(args.key, old_hint=args.hint)
        print(f"[KeyRotator] Rotation recorded for '{args.key}'.")
    elif args.action == "summary":
        print(kr.summary() or "[KeyRotator] All clear.")
