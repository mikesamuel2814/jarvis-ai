"""
root_client — Jarvis-side client for the jarvis-root privileged helper.

Jarvis (running as the non-root `kali` user) calls run_root() to execute a
command as root through the audited jarvis-root.service socket. If the helper
isn't installed/running, it returns a structured failure (never raises) so
callers can fall back to the normal (non-root) executor path.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

SOCK_PATH = "/run/jarvis/jarvis-root.sock"
SECRETS   = Path("/home/kali/.jarvis/config/secrets.env")


def _token() -> str:
    if SECRETS.exists():
        for line in SECRETS.read_text().splitlines():
            if line.strip().startswith("JARVIS_ROOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    return ""


def root_available() -> bool:
    return os.path.exists(SOCK_PATH)


def run_root(cmd: str, *, force: bool = False, timeout: int = 620) -> dict:
    """
    Execute `cmd` as root via the helper.

    Returns {success, output, blocked?, rc?}. `force=True` is required to run a
    command that matches the catastrophic-op tripwire — Jarvis must NEVER set
    force automatically from model/scraped/RAG content; only from an explicit,
    trusted operator instruction.
    """
    if not root_available():
        return {"success": False, "output": "jarvis-root helper not running", "unavailable": True}
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(SOCK_PATH)
        payload = json.dumps({"cmd": cmd, "force": force, "token": _token()}) + "\n"
        s.sendall(payload.encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        s.close()
        return json.loads(buf.decode().strip())
    except Exception as exc:
        return {"success": False, "output": f"root helper error: {exc}"}
