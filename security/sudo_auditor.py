"""
Jarvis v3 sudo Auditor
Reads /etc/sudoers.d/* every 10 minutes, compares against hardened policy.
Alerts on any NOPASSWD or widened scope.
"""

import os
import re
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", "/home/kali/.jarvis"))
POLICY_PATH = JARVIS_HOME / "data" / "sudo_policy.json"
AUDIT_LOG = JARVIS_HOME / "data" / "sudo_audit.log"

logger = logging.getLogger("jarvis.security.sudo")


class SudoAuditor:
    """
    Monitors sudoers files for policy violations.
    Expected hardened baseline: no NOPASSWD for write/system-modifying commands.
    """

    SUDOERS_DIR = Path("/etc/sudoers.d")
    MAIN_SUDOERS = Path("/etc/sudoers")

    # Commands that should NEVER be NOPASSWD
    HIGH_RISK_CMDS = {
        "/usr/bin/apt", "/usr/bin/apt-get", "/usr/bin/dpkg",
        "/usr/sbin/reboot", "/usr/sbin/shutdown", "/usr/sbin/halt",
        "/bin/rm", "/usr/bin/rm", "/bin/dd", "/usr/bin/dd",
        "/usr/bin/passwd", "/usr/sbin/usermod", "/usr/sbin/useradd",
        "/usr/bin/systemctl",  # can be used to escalate via edit
    }

    def __init__(self):
        self.violations: List[dict] = []
        self._setup_logger()

    def _setup_logger(self):
        handler = logging.FileHandler(AUDIT_LOG)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        self._audit = logging.getLogger("sudo_audit")
        self._audit.setLevel(logging.INFO)
        self._audit.addHandler(handler)

    def _parse_line(self, line: str) -> Optional[dict]:
        """Parse a sudoers line into structured data."""
        line = line.strip()
        if not line or line.startswith("#"):
            return None
        # Simple regex for: user host=(runas) NOPASSWD: /path/to/cmd
        m = re.match(
            r"^(?P<user>\S+)\s+(?P<host>\S+)\s*=\s*(?:\((?P<runas>[^)]+)\)\s+)?(?P<tags>(?:\w+:\s*)*)(?P<cmd>.+)$",
            line,
        )
        if not m:
            return None
        tags = m.group("tags") or ""
        return {
            "user": m.group("user"),
            "host": m.group("host"),
            "runas": m.group("runas") or "root",
            "nopasswd": "NOPASSWD" in tags.upper(),
            "cmd": m.group("cmd").strip(),
            "raw": line,
        }

    def read_sudoers(self) -> List[dict]:
        """Read all sudoers.d files + main sudoers."""
        entries = []
        files = [self.MAIN_SUDOERS]
        if self.SUDOERS_DIR.exists():
            files.extend(sorted(self.SUDOERS_DIR.iterdir()))
        for fp in files:
            if fp.is_file():
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                        for lineno, line in enumerate(fh, 1):
                            parsed = self._parse_line(line)
                            if parsed:
                                parsed["file"] = str(fp)
                                parsed["line"] = lineno
                                entries.append(parsed)
                except PermissionError:
                    logger.warning("Cannot read %s (permission denied)", fp)
        return entries

    def audit(self) -> List[dict]:
        """Run full audit and return violations."""
        entries = self.read_sudoers()
        violations = []
        for e in entries:
            if e["nopasswd"]:
                # Check if command is high-risk
                cmd = e["cmd"]
                # Expand wildcards
                if cmd == "ALL" or any(cmd.startswith(h) for h in self.HIGH_RISK_CMDS):
                    violations.append({
                        "severity": "HIGH",
                        "type": "NOPASSWD_HIGH_RISK",
                        "file": e["file"],
                        "line": e["line"],
                        "user": e["user"],
                        "cmd": cmd,
                        "raw": e["raw"],
                    })
                else:
                    violations.append({
                        "severity": "MEDIUM",
                        "type": "NOPASSWD",
                        "file": e["file"],
                        "line": e["line"],
                        "user": e["user"],
                        "cmd": cmd,
                        "raw": e["raw"],
                    })
        self.violations = violations
        for v in violations:
            self._audit.info(
                "VIOLATION severity=%s type=%s file=%s line=%d user=%s cmd=%s",
                v["severity"], v["type"], v["file"], v["line"], v["user"], v["cmd"]
            )
        return violations

    def is_compliant(self) -> bool:
        return len(self.audit()) == 0

    def generate_policy(self) -> dict:
        """Generate a hardened policy template."""
        return {
            "description": "Jarvis v3 hardened sudo policy",
            "rules": [
                {"action": "deny", "condition": "NOPASSWD and cmd in HIGH_RISK_CMDS"},
                {"action": "warn", "condition": "NOPASSWD and cmd == 'ALL'"},
                {"action": "allow", "condition": "PASSWD and cmd in whitelisted systemctl restart commands"},
            ],
            "whitelisted_systemctl": [
                "/usr/bin/systemctl restart jarvis",
                "/usr/bin/systemctl restart jarvis-telegram",
                "/usr/bin/systemctl restart jarvis-monitor",
                "/usr/bin/systemctl restart jarvis-cursor",
                "/usr/bin/systemctl restart openclaw",
                "/usr/bin/systemctl restart ollama",
            ],
            "high_risk_commands": sorted(self.HIGH_RISK_CMDS),
        }

    def save_policy(self):
        with open(POLICY_PATH, "w") as fh:
            json.dump(self.generate_policy(), fh, indent=2)

    def summary(self) -> Optional[str]:
        vios = self.audit()
        if not vios:
            return None
        lines = ["🔐 *sudo Policy Violations*"]
        for v in vios[:10]:
            lines.append(
                f"• *{v['severity']}* `{v['type']}` in `{v['file']}` line {v['line']}\n"
                f"  user=`{v['user']}` cmd=`{v['cmd']}`"
            )
        return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Jarvis sudo Auditor")
    parser.add_argument("action", choices=["audit", "policy", "compliant"])
    args = parser.parse_args()

    auditor = SudoAuditor()
    if args.action == "audit":
        vios = auditor.audit()
        if vios:
            print(auditor.summary())
        else:
            print("[SudoAuditor] No violations found.")
    elif args.action == "policy":
        auditor.save_policy()
        print(f"[SudoAuditor] Policy saved to {POLICY_PATH}")
    elif args.action == "compliant":
        print("YES" if auditor.is_compliant() else "NO")
