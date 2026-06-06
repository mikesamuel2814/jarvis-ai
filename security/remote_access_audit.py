"""
Jarvis v3 Remote Access Audit
Inventory and monitor all remote access tools per SEC-08.
"""

import os
import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", "/home/kali/.jarvis"))
REPORT_PATH = JARVIS_HOME / "data" / "remote_access_inventory.json"


def check_service(name: str) -> dict:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=5,
        )
        status = out.stdout.strip()
    except Exception:
        status = "unknown"
    return {"name": name, "status": status}


def audit() -> dict:
    """Generate remote access tool inventory."""
    report = {
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "services": [
            check_service("ssh"),
            check_service("anydesk"),
            check_service("cloudflared"),
            check_service("tailscaled"),
        ],
        "ssh_config_notes": [
            "Check /etc/ssh/sshd_config for PasswordAuthentication, PermitRootLogin, MaxAuthTries",
            "Check ~/.ssh/authorized_keys for unauthorized keys",
        ],
        "anydesk_notes": [
            "Review ~/.anydesk/user.conf for unattended access settings",
            "Confirm AnyDesk password complexity if unattended access enabled",
        ],
        "cloudflared_notes": [
            "Token moved to config file (not command line) — verify in systemd unit",
        ],
        "recommendations": [
            "Disable PasswordAuthentication if only key auth is needed",
            "Set PermitRootLogin=no",
            "Review AnyDesk unattended access necessity",
            "Consider fail2ban for SSH brute-force protection",
        ],
    }
    with open(REPORT_PATH, "w") as fh:
        json.dump(report, fh, indent=2)
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Remote Access Audit")
    parser.add_argument("action", choices=["audit"])
    args = parser.parse_args()
    if args.action == "audit":
        r = audit()
        print(json.dumps(r, indent=2))
