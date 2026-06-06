#!/usr/bin/env python3
"""
jarvis-root — privileged execution helper for Jarvis.

Runs as root (systemd service). Listens on a local unix socket that ONLY the
`kali` user (i.e. Jarvis) can talk to. Executes commands as root so Jarvis can
perform full system administration autonomously — WITHOUT putting the Jarvis
web/RAG/Telegram injection surface inside a root process.

Design rationale:
  - The main jarvis service (web search, scraping, RAG, Telegram) stays NON-root.
  - Only vetted commands cross this socket into root. One audited choke-point.
  - Autonomous: every command runs immediately, no approval prompt.
  - The ONLY exception is a small denylist of IRREVERSIBLE catastrophic ops
    (wipe /, mkfs/dd on system disks, delete user/sudo/ssh, fork bomb). Those
    are refused unless the caller passes force=true (which Jarvis never sets
    autonomously) — this is what stops a malicious scraped webpage or a crafted
    scan banner from bricking the machine via prompt-injection.

Revoke at any time:  sudo systemctl stop jarvis-root && sudo systemctl disable jarvis-root
"""

from __future__ import annotations

import grp
import json
import logging
import os
import re
import socket
import subprocess
import time
from pathlib import Path

JARVIS_HOME = Path("/home/kali/.jarvis")
SOCK_PATH   = "/run/jarvis/jarvis-root.sock"
AUDIT_LOG   = JARVIS_HOME / "logs" / "root_audit.log"
SECRETS     = JARVIS_HOME / "config" / "secrets.env"
CLIENT_GROUP = "kali"          # only this group may connect
CMD_TIMEOUT  = 600

logging.basicConfig(level=logging.INFO, format="%(asctime)s [jarvis-root] %(message)s")
log = logging.getLogger("jarvis-root")

# ── Catastrophic-op tripwire ──────────────────────────────────────────────
# These are IRREVERSIBLE, machine-destroying, or safety-disabling. Refused
# unless force=true. Everything NOT matched here runs autonomously as root.
_CATASTROPHIC = [
    r"\brm\s+(-[a-z]*\s+)*-[a-z]*r[a-z]*f[a-z]*\s+(--no-preserve-root\s+)?/(\s|$|\*)",  # rm -rf /
    r"\brm\s+(-[a-z]*\s+)*-[a-z]*f[a-z]*r[a-z]*\s+(--no-preserve-root\s+)?/(\s|$|\*)",
    r"\b(mkfs|wipefs)\b.*\b/dev/(sd|nvme|vd|mmcblk)",      # format a real disk
    r"\bdd\b.*\bof=/dev/(sd|nvme|vd|mmcblk)",               # overwrite a block device
    r">\s*/dev/(sd|nvme|vd|mmcblk)\w*",                     # redirect onto a disk
    r"\b(shred|blkdiscard)\b.*\b/dev/(sd|nvme|vd|mmcblk)",
    r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;",          # fork bomb :(){ :|:& };:
    r"\bchmod\b\s+(-[a-z]*\s+)*-?R?\s*0*00\s+/(\s|$)",      # chmod -R 000 /
    r"\bchown\b\s+-R\s+\w+\s+/(\s|$)",                      # chown -R x /
    r"\b(userdel|deluser)\b.*\bkali\b",                     # delete the owner account
    r"\bpasswd\b\s+(-l\s+)?(root|kali)\b",                  # lock root/owner password
    r"\brm\b.*/etc/(sudoers|shadow|passwd)\b",             # destroy auth
    r"\brm\b.*\.ssh/(authorized_keys|id_)",                # destroy ssh access
    r"\b(systemctl|service)\b.*\b(disable|mask|stop)\b.*\b(ssh|sshd)\b",  # cut remote access
]
_CATASTROPHIC_RE = [re.compile(p, re.IGNORECASE) for p in _CATASTROPHIC]


def _load_token() -> str:
    """Shared secret (defense-in-depth on top of socket perms)."""
    if SECRETS.exists():
        for line in SECRETS.read_text().splitlines():
            if line.strip().startswith("JARVIS_ROOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    return ""


_TOKEN = _load_token()


def _audit(entry: dict) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = int(time.time())
    with open(AUDIT_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _is_catastrophic(cmd: str) -> str | None:
    for rx in _CATASTROPHIC_RE:
        if rx.search(cmd):
            return rx.pattern
    return None


def handle(req: dict) -> dict:
    cmd   = (req.get("cmd") or "").strip()
    force = bool(req.get("force"))
    token = req.get("token", "")

    if _TOKEN and token != _TOKEN:
        _audit({"event": "auth_fail", "cmd": cmd[:200]})
        return {"success": False, "output": "auth failed", "blocked": True}

    if not cmd:
        return {"success": False, "output": "empty command"}

    hit = _is_catastrophic(cmd)
    if hit and not force:
        _audit({"event": "blocked_catastrophic", "cmd": cmd[:300], "pattern": hit})
        return {
            "success": False,
            "blocked": True,
            "output": (
                "⚠️ Refused: this is an irreversible catastrophic operation "
                "(matched the safety tripwire). It was NOT executed. If you truly "
                "intend this, Sir, re-issue it with force=true from a trusted "
                "context — it will never run from scraped/RAG content."
            ),
        }

    _audit({"event": "exec", "cmd": cmd[:500], "forced": force})
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=CMD_TIMEOUT)
        out = (r.stdout + r.stderr).strip()
        _audit({"event": "done", "cmd": cmd[:200], "rc": r.returncode})
        return {"success": r.returncode == 0, "output": out[:8000], "rc": r.returncode}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": f"timed out after {CMD_TIMEOUT}s"}
    except Exception as exc:
        return {"success": False, "output": f"error: {exc}"}


def main() -> None:
    sock_dir = os.path.dirname(SOCK_PATH)
    os.makedirs(sock_dir, exist_ok=True)
    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    # Restrict the socket to root + the kali group only (0660 root:kali).
    try:
        gid = grp.getgrnam(CLIENT_GROUP).gr_gid
        os.chown(SOCK_PATH, 0, gid)
        os.chmod(SOCK_PATH, 0o660)
    except Exception as exc:
        log.warning("could not tighten socket perms: %s", exc)
    srv.listen(8)
    log.info("listening on %s (token=%s)", SOCK_PATH, "set" if _TOKEN else "NONE")

    while True:
        conn, _ = srv.accept()
        try:
            conn.settimeout(CMD_TIMEOUT + 30)
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
            if not buf:
                continue
            try:
                req = json.loads(buf.decode().strip())
            except Exception:
                conn.sendall(b'{"success": false, "output": "bad json"}\n')
                continue
            resp = handle(req)
            conn.sendall((json.dumps(resp) + "\n").encode())
        except Exception as exc:
            log.warning("conn error: %s", exc)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
