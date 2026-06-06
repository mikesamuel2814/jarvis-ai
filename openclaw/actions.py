"""
Low-level OpenClaw gateway client for Jarvis.

This module knows *how* to talk to the locally-running OpenClaw gateway and
nothing about Jarvis' higher-level dispatch policy. ``bridge.py`` builds the
public, caller-friendly API on top of these primitives.

Discovered OpenClaw interface (OpenClaw 2026.6.1, service `openclaw.service`):

  * HTTP control surface — gateway binds loopback on 127.0.0.1:18789.
      - ``GET /health``  -> ``{"ok": true, "status": "live"}``  (no auth).
        This is the cheapest liveness probe and what ``gateway_alive`` uses.
      - The rest of the surface is a WebSocket RPC gateway, not REST, so we do
        NOT hand-roll a WS client here — we shell out to the official CLI which
        speaks it correctly.

  * CLI / RPC — the ``openclaw`` node binary connects to the running gateway:
      - ``openclaw gateway call <method> --json --params <json>``
            structured RPC. methods: health, status, system-presence, cron.* …
      - ``openclaw agent --message <text> --json``
            run one agent turn (natural-language plan + execute) via the
            gateway and return the result as JSON.
      - ``openclaw message send --target <t> --message <m>``
            deliver a chat message through a configured channel.

Safety rules enforced here:
  * never ``shell=True``; always list-arg subprocess.
  * every subprocess gets ``env={**os.environ, "MALLOC_ARENA_MAX": "2"}``
    (Bun/node mitigation noted in the Jarvis environment).
  * reasonable per-call timeouts; nothing blocks the caller forever.
  * secrets are never logged or echoed back.
  * a privacy boundary blocks outward NL dispatch that references sensitive
    paths (AsthaCash / Payment-Gateway / .ssh / secrets).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.openclaw.actions")

# ── Configuration ────────────────────────────────────────────────

OC_HTTP_URL = os.environ.get("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")

# Resolve the openclaw CLI. Prefer a binary on PATH; fall back to invoking the
# bundled .mjs entrypoint with node (matches the systemd ExecStart).
_OC_BIN = shutil.which("openclaw")
_OC_NODE = shutil.which("node") or "/usr/bin/node"
_OC_MJS = Path(
    os.environ.get(
        "OPENCLAW_MJS",
        "/home/kali/.npm-global/lib/node_modules/openclaw/openclaw.mjs",
    )
)

# Default subprocess environment (node/bun mitigation).
_SAFE_ENV = {**os.environ, "MALLOC_ARENA_MAX": "2"}

# Default agent + session used for non-interactive NL dispatch. OpenClaw refuses
# an ``agent`` turn with no target session, so we always bind to a stable,
# headless session that is NOT tied to any chat channel.
OC_AGENT = os.environ.get("OPENCLAW_AGENT", "main")
OC_SESSION_KEY = os.environ.get("OPENCLAW_SESSION_KEY", "jarvis-bridge")

# Patterns that must never be shipped outward through OpenClaw (privacy boundary).
_PRIVACY_DENY = (
    "asthacash",
    "payment-gateway",
    "/.ssh",
    ".ssh/",
    "secrets.env",
    "secrets",
    "id_rsa",
    "id_ed25519",
    "private key",
)


def _cli_argv() -> list[str] | None:
    """Return the argv prefix used to invoke the OpenClaw CLI, or None."""
    if _OC_BIN:
        return [_OC_BIN]
    if _OC_MJS.exists():
        return [_OC_NODE, str(_OC_MJS)]
    return None


def cli_available() -> bool:
    """True if the OpenClaw CLI can be invoked at all."""
    return _cli_argv() is not None


# ── HTTP liveness ────────────────────────────────────────────────


def gateway_alive(timeout: float = 4.0) -> bool:
    """
    Cheap liveness probe against the gateway's unauthenticated ``/health``.

    Returns True only when the gateway answers ``{"ok": true}``. Never raises.
    """
    try:
        import requests
    except ImportError:
        log.debug("requests not available for gateway_alive()")
        return False
    try:
        r = requests.get(f"{OC_HTTP_URL}/health", timeout=timeout)
        if r.status_code != 200:
            return False
        data = r.json()
        return bool(data.get("ok"))
    except Exception as e:  # noqa: BLE001 - defensive by design
        log.debug("gateway_alive() probe failed: %s", e)
        return False


# ── CLI invocation ───────────────────────────────────────────────


def _run_cli(args: list[str], timeout: float) -> dict[str, Any]:
    """
    Run ``openclaw <args>`` as a list-arg subprocess. Never raises.

    Returns a dict: {ok, returncode, stdout, stderr, error?}.
    """
    argv = _cli_argv()
    if argv is None:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "",
                "error": "openclaw CLI not found"}

    full = argv + args
    # Log the subcommand verb only, never the full args (may carry message text).
    log.debug("openclaw CLI: %s", args[0] if args else "<none>")
    try:
        proc = subprocess.run(
            full,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_SAFE_ENV,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "",
                "error": f"openclaw call timed out after {timeout}s"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "",
                "error": f"openclaw call failed: {e}"}

    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def _parse_json_stdout(stdout: str) -> Any | None:
    """Best-effort parse of CLI JSON output (handles leading banner lines)."""
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass
    # CLI sometimes prints a banner before the JSON body; find first { or [.
    for i, ch in enumerate(stdout):
        if ch in "{[":
            try:
                return json.loads(stdout[i:])
            except json.JSONDecodeError:
                break
    return None


def gateway_call(method: str, params: dict | None = None,
                 timeout: float = 15.0, expect_final: bool = False) -> dict[str, Any]:
    """
    Invoke an OpenClaw gateway RPC method via ``openclaw gateway call``.

    Args:
        method: RPC method name (e.g. "health", "status", "system-presence",
                "cron.list").
        params: JSON-serialisable params object.
        timeout: hard timeout in seconds.
        expect_final: pass ``--expect-final`` (wait for an agent's final reply).

    Returns: {ok, data?, raw?, error?}. ``data`` is the parsed JSON when the
    method returns JSON; ``raw`` holds text otherwise. Never raises.
    """
    args = ["gateway", "call", method, "--json"]
    if params:
        try:
            args += ["--params", json.dumps(params)]
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": f"params not JSON-serialisable: {e}"}
    if expect_final:
        args.append("--expect-final")
    # CLI default RPC timeout is 10s; give the subprocess a little more headroom.
    args += ["--timeout", str(int(max(1.0, timeout - 2.0) * 1000))]

    res = _run_cli(args, timeout=timeout)
    if not res["ok"]:
        return {"ok": False,
                "error": res.get("error") or res.get("stderr") or "gateway call failed",
                "returncode": res.get("returncode")}

    data = _parse_json_stdout(res["stdout"])
    if data is not None:
        return {"ok": True, "data": data}
    return {"ok": True, "raw": res["stdout"]}


def is_private(text: str) -> bool:
    """True if ``text`` references a privacy-protected resource."""
    low = (text or "").lower()
    return any(token in low for token in _PRIVACY_DENY)


def agent_turn(message: str, timeout: float = 120.0,
               deliver: bool = False, channel: str | None = None) -> dict[str, Any]:
    """
    Run one OpenClaw agent turn (natural-language plan + execute) via the
    gateway: ``openclaw agent --message <text> --json``.

    Enforces the privacy boundary: refuses messages that reference sensitive
    paths/resources. Never delivers to a chat channel unless ``deliver=True``.

    Returns: {ok, data?, raw?, error?}. Never raises.
    """
    if not message or not message.strip():
        return {"ok": False, "error": "empty message"}
    if is_private(message):
        log.warning("agent_turn refused: message hit privacy boundary.")
        return {"ok": False, "error": "refused: message references protected resources"}

    # Bind to a stable headless agent+session so the gateway has a target.
    args = ["agent", "--agent", OC_AGENT,
            "--session-key", f"agent:{OC_AGENT}:{OC_SESSION_KEY}",
            "--message", message, "--json"]
    if deliver:
        args.append("--deliver")
    if channel:
        args += ["--channel", channel]
    args += ["--timeout", str(int(timeout))]

    res = _run_cli(args, timeout=timeout + 10.0)
    if not res["ok"]:
        return {"ok": False,
                "error": res.get("error") or res.get("stderr") or "agent turn failed",
                "returncode": res.get("returncode")}

    data = _parse_json_stdout(res["stdout"])
    if data is not None:
        return {"ok": True, "data": data}
    return {"ok": True, "raw": res["stdout"]}


def message_send(message: str, target: str, timeout: float = 20.0) -> dict[str, Any]:
    """
    Send a chat message through OpenClaw: ``openclaw message send``.

    Returns: {ok, raw?, error?}. Never raises.
    """
    if not message or not target:
        return {"ok": False, "error": "message and target are required"}
    args = ["message", "send", "--target", target, "--message", message]
    res = _run_cli(args, timeout=timeout)
    if not res["ok"]:
        return {"ok": False,
                "error": res.get("error") or res.get("stderr") or "message send failed"}
    return {"ok": True, "raw": res["stdout"]}
