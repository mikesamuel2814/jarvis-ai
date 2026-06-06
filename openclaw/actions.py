"""
Low-level OpenClaw gateway client for Jarvis.

This module knows *how* to talk to the locally-running OpenClaw gateway and
nothing about Jarvis' higher-level dispatch policy. ``bridge.py`` builds the
public, caller-friendly API on top of these primitives.

Discovered OpenClaw interface (OpenClaw 2026.6.1, service ``openclaw.service``)
================================================================================

The gateway is a WebSocket + HTTP multiplex server bound to loopback on
``127.0.0.1:18789`` (``gateway.mode=local``, ``gateway.bind=loopback`` in
``~/.openclaw/openclaw.json``). Two surfaces matter to Jarvis:

1. HTTP — preferred, used here as the primary path:

     * ``GET /health``  -> ``{"ok": true, "status": "live"}``  (no auth).
       Cheapest liveness probe; used by :func:`gateway_alive`.

     * ``POST /tools/invoke``  (Bearer auth, always enabled). Invoke a single
       tool directly with Gateway auth + tool policy. Request:
           ``{"tool": <name>, "action"?: <str>, "args"?: {...},
              "sessionKey"?: <str>}``
       Response:
           ``200 {"ok": true,  "result": {...}}``
           ``4xx/5xx {"ok": false, "error": {"type", "message"}}``
       The gateway hard-denies RCE/control-plane tools over HTTP by default
       (``exec``, ``spawn``, ``shell``, ``fs_write``, ``fs_delete``, ``cron``,
       ``gateway``, ``nodes`` …) — those return ``404 not_found``. This is a
       safety feature we rely on: Jarvis never gets raw shell via OpenClaw HTTP.

2. CLI — fallback for things the HTTP tool surface deliberately cannot do
   (notably ``message send`` reply-delivery and full ``agent`` turns). The
   ``openclaw`` node binary connects to the running gateway:
       * ``openclaw agent --agent <id> --session-key <k> --message <t> --json``
       * ``openclaw message send --channel <c> --target <t> --message <m>``
       * ``openclaw gateway call <method> --json --params <json>``
   Shelling out to node is heavier and may be blocked in restricted sandboxes,
   so the HTTP path is always tried first where it is capable.

Authentication
--------------
``gateway.auth.mode = "token"``. The bearer token is resolved (in order) from:
  1. ``$OPENCLAW_GATEWAY_TOKEN``
  2. ``gateway.auth.token`` in ``~/.openclaw/openclaw.json``
Possession of this token == full operator access to the gateway, so it is
treated like a secret: never logged, never echoed back to callers.

Safety rules enforced here
--------------------------
  * never ``shell=True``; always list-arg subprocess.
  * every subprocess gets ``env={**os.environ, "MALLOC_ARENA_MAX": "2"}``
    (node/bun mitigation noted in the Jarvis environment).
  * reasonable per-call timeouts; nothing blocks the caller forever.
  * secrets (the gateway token) are never logged or echoed back.
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

# OpenClaw home + config (token lives here when not in the environment).
OC_HOME = Path(os.environ.get("OPENCLAW_HOME", Path.home() / ".openclaw"))
OC_CONFIG = Path(os.environ.get("OPENCLAW_CONFIG", OC_HOME / "openclaw.json"))

# Resolve the openclaw CLI. Prefer a binary on PATH; fall back to the symlinked
# bin, then to invoking the bundled .mjs entrypoint with node.
_OC_BIN = shutil.which("openclaw")
if not _OC_BIN:
    _cand = Path.home() / ".npm-global" / "bin" / "openclaw"
    if _cand.exists():
        _OC_BIN = str(_cand)
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
# an ``agent`` turn with no session selector, so we always bind to a stable,
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

# Cache the resolved token so we don't re-read the config on every call.
_TOKEN_CACHE: str | None = None
_TOKEN_RESOLVED = False


# ── Auth ──────────────────────────────────────────────────────────


def _gateway_token() -> str | None:
    """
    Resolve the gateway bearer token (env first, then ``openclaw.json``).

    Cached. Returns None if no token can be found. Never logs the value.
    """
    global _TOKEN_CACHE, _TOKEN_RESOLVED
    if _TOKEN_RESOLVED:
        return _TOKEN_CACHE

    _TOKEN_RESOLVED = True
    tok = os.environ.get("OPENCLAW_GATEWAY_TOKEN")
    if tok:
        _TOKEN_CACHE = tok.strip() or None
        return _TOKEN_CACHE

    try:
        if OC_CONFIG.exists():
            cfg = json.loads(OC_CONFIG.read_text(encoding="utf-8"))
            tok = (
                cfg.get("gateway", {})
                .get("auth", {})
                .get("token")
            )
            if isinstance(tok, str) and tok.strip():
                _TOKEN_CACHE = tok.strip()
    except Exception as e:  # noqa: BLE001 - defensive; never raise to caller
        log.debug("could not read gateway token from config: %s", e)

    if _TOKEN_CACHE is None:
        log.debug("no OpenClaw gateway token resolved (env or config).")
    return _TOKEN_CACHE


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


# ── HTTP tool invocation (PRIMARY path) ──────────────────────────


def _extract_tool_text(result: Any) -> str:
    """
    Best-effort flatten of a ``/tools/invoke`` result into a string.

    OpenClaw tool results look like
    ``{"content": [{"type": "text", "text": "..."}], "details": {...}}``.
    Prefer the joined text blocks; fall back to JSON of ``details`` or the
    whole result. Never raises.
    """
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts = [
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            joined = "\n".join(p for p in parts if p)
            if joined:
                return joined
        details = result.get("details")
        if details is not None:
            try:
                return json.dumps(details)
            except (TypeError, ValueError):
                pass
    try:
        return json.dumps(result)
    except (TypeError, ValueError):
        return str(result)


def tools_invoke(
    tool: str,
    args: dict | None = None,
    action: str | None = None,
    session_key: str | None = None,
    channel: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """
    Invoke a single OpenClaw tool over HTTP ``POST /tools/invoke``.

    This is the preferred way for Jarvis to make OpenClaw *do* something: it is
    fast, needs no node subprocess, and is gated by the gateway's tool policy.

    Args:
        tool: tool name (e.g. ``"sessions_list"``, ``"memory_search"``).
        args: tool-specific argument object.
        action: optional ``action`` discriminator some tools accept.
        session_key: target session key (defaults to the gateway main session).
        channel: optional message-channel hint (``x-openclaw-message-channel``).
        timeout: hard HTTP timeout in seconds.

    Returns a dict, never raising:
        {"ok": True,  "result": <raw>, "text": <flattened str>}
        {"ok": False, "error": <str>, "status": <int|None>,
         "denied": <bool>}   # denied=True when the tool isn't allowed (404)
    """
    if not tool:
        return {"ok": False, "error": "tool name required", "status": None,
                "denied": False}

    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "requests not installed", "status": None,
                "denied": False}

    token = _gateway_token()
    if not token:
        return {"ok": False, "error": "no gateway token available",
                "status": None, "denied": False}

    body: dict[str, Any] = {"tool": tool, "args": args or {}}
    if action:
        body["action"] = action
    if session_key:
        body["sessionKey"] = session_key

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if channel:
        headers["x-openclaw-message-channel"] = channel

    try:
        r = requests.post(
            f"{OC_HTTP_URL}/tools/invoke",
            headers=headers,
            data=json.dumps(body),
            timeout=timeout,
        )
    except Exception as e:  # noqa: BLE001
        log.debug("tools_invoke(%s) transport error: %s", tool, e)
        return {"ok": False, "error": f"transport error: {e}", "status": None,
                "denied": False}

    # Parse the JSON body if we can; OpenClaw always returns JSON.
    try:
        data = r.json()
    except ValueError:
        data = None

    if r.status_code == 200 and isinstance(data, dict) and data.get("ok"):
        result = data.get("result")
        return {"ok": True, "result": result,
                "text": _extract_tool_text(result)}

    # Failure paths. 404 == tool not allowed by policy (caller should fall back).
    err_msg = "tool invoke failed"
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            err_msg = err.get("message") or err.get("type") or err_msg
        elif isinstance(err, str):
            err_msg = err
    denied = r.status_code in (404, 401, 403)
    return {"ok": False, "error": err_msg, "status": r.status_code,
            "denied": denied}


# ── CLI invocation (FALLBACK path) ───────────────────────────────


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

    NOTE: This is the CLI (WebSocket RPC) path and requires the node CLI to be
    runnable. For most "do an action" needs prefer :func:`tools_invoke` (HTTP).
    ``gateway call`` is kept for read-scope introspection methods such as
    ``health``, ``status``, ``system-presence``, ``logs.tail``.

    Args:
        method: RPC method name.
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
    # CLI --timeout is in milliseconds; give the subprocess a little headroom.
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
    gateway CLI: ``openclaw agent --message <text> --json``.

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
    # agent --timeout is in SECONDS (unlike gateway call which is ms).
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


def message_send(message: str, target: str,
                 channel: str | None = None,
                 timeout: float = 20.0) -> dict[str, Any]:
    """
    Send a chat message through OpenClaw: ``openclaw message send``.

    ``--channel`` is required by OpenClaw when more than one channel is
    configured; we pass it through when given. On this host the only configured
    channel is Telegram, so ``channel`` may be omitted and OpenClaw will infer
    it.

    Returns: {ok, raw?, error?}. Never raises.
    """
    if not message or not target:
        return {"ok": False, "error": "message and target are required"}
    args = ["message", "send", "--target", target, "--message", message]
    if channel:
        args = ["message", "send", "--channel", channel,
                "--target", target, "--message", message]
    res = _run_cli(args, timeout=timeout)
    if not res["ok"]:
        return {"ok": False,
                "error": res.get("error") or res.get("stderr") or "message send failed"}
    return {"ok": True, "raw": res["stdout"]}
