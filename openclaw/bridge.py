"""
OpenClaw ↔ Jarvis bridge.

This is the clean integration layer callers (api_v2, telegram_bot, brain,
decision_engine …) import to perform REAL system/dev actions "smoothly via
OpenClaw where possible, falling back to executor.py otherwise."

Public action API (built on ``openclaw.actions``):
  * ``openclaw_available() -> bool``
        Is the OpenClaw gateway reachable and healthy right now.
  * ``run_action(action, args=None) -> dict``
        Attempt a known action via OpenClaw. Returns
        ``{ok, output, via, ...}``. When OpenClaw cannot perform the action,
        returns ``ok=False`` with a ``reason`` so the *caller* can fall back to
        ``executor.run_action`` (this module never imports executor — avoids a
        circular dependency).
  * ``dispatch(natural_language) -> dict``
        Let OpenClaw plan + execute a real action from natural language.
  * ``tool_invoke(tool, args=None, ...) -> dict``
        Invoke one named OpenClaw tool directly via the HTTP tool surface
        (``POST /tools/invoke``). Same ``{ok, output, via, fallback}`` shape.

Plus the original memory/sync + alert helpers:
  * push_memory_to_openclaw / pull_skills_from_openclaw
  * send_via_openclaw
  * heartbeat event processing (see heartbeat_handler.py)

The discovered OpenClaw interface (port, HTTP vs CLI, RPC methods) is
documented in ``openclaw.actions``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import requests

from . import actions

log = logging.getLogger("jarvis.openclaw.bridge")

JARVIS_HOME    = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
OPENCLAW_WS    = Path(os.environ.get("OPENCLAW_WORKSPACE",
                                      Path.home() / ".openclaw" / "workspace"))
SYNC_LOG       = JARVIS_HOME / "data" / "openclaw_sync.jsonl"
OC_GATEWAY_URL = os.environ.get("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")


# ── Action map: which Jarvis actions OpenClaw can serve natively ──
#
# Maps a Jarvis action name -> how OpenClaw should serve it. Anything NOT in
# this map (or whose OpenClaw attempt fails) is reported back with ok=False so
# the caller falls back to executor.run_action. Keep this conservative: only
# list actions OpenClaw can genuinely do better/equally and safely.
#
# kind="tool" -> POST /tools/invoke (HTTP, preferred — fast, no node subprocess)
#                spec: {"tool": <name>, "action"?: <str>, "args"?: {...}}
#                Note: OpenClaw HTTP-denies RCE/control-plane tools (exec, shell,
#                gateway, nodes, cron, fs_write …) so they are NOT mappable here
#                by design — those always fall back to executor.py.
# kind="rpc"  -> openclaw gateway call <method>  (CLI/WebSocket, read-scope)
# kind="nl"   -> phrase a natural-language agent turn (CLI)

_OC_ACTION_MAP: dict[str, dict] = {
    # Native gateway introspection — OpenClaw owns its own health/status.
    # These go over the CLI WebSocket RPC (the gateway control plane is not
    # reachable through HTTP /tools/invoke on purpose).
    "openclaw_health": {"kind": "rpc", "method": "health"},
    "openclaw_status": {"kind": "rpc", "method": "status"},
    "openclaw_presence": {"kind": "rpc", "method": "system-presence"},

    # Session / memory introspection via the HTTP tool surface (no node needed).
    "openclaw_sessions": {"kind": "tool", "tool": "sessions_list",
                          "action": "json"},
}


# ── ChromaDB → OpenClaw workspace ────────────────────────────────


def push_memory_to_openclaw(limit: int = 100) -> int:
    """
    Export pending ChromaDB chunks to OpenClaw workspace as markdown.
    Only syncs chunks with oc_sync_status == "pending".
    """
    try:
        import chromadb
    except ImportError:
        log.error("chromadb not installed")
        return 0

    mem_path = str(JARVIS_HOME / "memory")
    client   = chromadb.PersistentClient(path=mem_path)
    col      = client.get_or_create_collection("jarvis_memory")

    # Get pending chunks — ChromaDB returns dict of parallel lists
    result = col.get(where={"oc_sync_status": "pending"}, limit=limit)
    ids       = result.get("ids", [])
    documents = result.get("documents", [])
    metadatas = result.get("metadatas", [])

    if not ids:
        log.info("No pending chunks to sync to OpenClaw.")
        return 0

    mem_dir = OPENCLAW_WS / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)

    synced = 0
    for chunk_id, doc, meta in zip(ids, documents, metadatas):
        md = (
            f"# Memory: {chunk_id}\n\n"
            f"**Source:** {meta.get('source', 'unknown')}  \n"
            f"**Priority:** {meta.get('priority', 1)}  \n"
            f"**Type:** {meta.get('type', 'general')}  \n"
            f"**Timestamp:** {meta.get('ts', '')}  \n\n"
            f"## Content\n\n{doc}\n\n"
            f"---\n"
        )
        out_path = mem_dir / f"{chunk_id[:60]}.md"
        out_path.write_text(md, encoding="utf-8")

        # Mark as synced
        try:
            col.update(ids=[chunk_id], metadatas=[{**meta, "oc_sync_status": "synced"}])
            synced += 1
        except Exception as e:
            log.error("Failed to mark chunk %s as synced: %s", chunk_id, e)

    _log_sync({"direction": "push", "count": synced, "ts": int(time.time())})
    log.info("Pushed %d chunks to OpenClaw workspace.", synced)
    return synced


def pull_skills_from_openclaw() -> int:
    """
    Pull OpenClaw skill-generated content back into ChromaDB.
    Reads *.md files from the skills output directory.
    """
    try:
        import chromadb
    except ImportError:
        return 0

    skills_dir = OPENCLAW_WS / "skills" / "jarvis-system" / "outputs"
    if not skills_dir.exists():
        return 0

    client = chromadb.PersistentClient(path=str(JARVIS_HOME / "memory"))
    col    = client.get_or_create_collection("jarvis_memory")

    pulled = 0
    for md_file in skills_dir.glob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
            doc_id  = f"oc_skill_{md_file.stem}"
            col.add(
                documents=[content[:2000]],
                metadatas=[{
                    "source": "openclaw_skill",
                    "priority": 3,
                    "type": "skill_output",
                    "ts": str(int(md_file.stat().st_mtime)),
                    "oc_sync_status": "synced",
                }],
                ids=[doc_id],
            )
            pulled += 1
        except Exception as e:
            log.error("Failed to import skill file %s: %s", md_file, e)

    _log_sync({"direction": "pull", "count": pulled, "ts": int(time.time())})
    return pulled


# ── Alert via OpenClaw ────────────────────────────────────────────


def send_via_openclaw(message: str, target: str | None = None) -> bool:
    """
    Send a message via the OpenClaw gateway (safe list-arg CLI client).

    Returns True on success, False otherwise (caller may fall back to the
    Telegram bot token directly). Never raises.
    """
    if not target:
        # `message send` requires a target; without one we cannot route safely.
        log.warning("send_via_openclaw called without a target; skipping.")
        return False
    res = actions.message_send(message, target)
    if not res.get("ok"):
        log.warning("openclaw message send failed: %s", res.get("error"))
    return bool(res.get("ok"))


# ── Public action API (OpenClaw-first, executor fallback) ─────────


def openclaw_available() -> bool:
    """
    True when the OpenClaw gateway is reachable and healthy right now.

    Cheap, non-raising. Uses the unauthenticated ``/health`` HTTP probe.
    """
    return actions.gateway_alive()


def run_action(action: str, args: dict | None = None) -> dict:
    """
    Attempt a known action via OpenClaw.

    Returns a structured dict the caller can act on:
        {"ok": True,  "output": <str>, "via": "openclaw", "action": <action>}
        {"ok": False, "reason": <str>, "via": "openclaw", "action": <action>,
         "fallback": True}

    ``fallback=True`` is a hint that the caller should retry the action via
    ``executor.run_action`` (this module deliberately does NOT import executor
    to avoid a circular dependency). Never raises.
    """
    args = args or {}
    spec = _OC_ACTION_MAP.get(action)

    # Action OpenClaw doesn't natively serve -> tell caller to use executor.
    if spec is None:
        return {"ok": False, "via": "openclaw", "action": action,
                "reason": "action not served by OpenClaw", "fallback": True}

    if not openclaw_available():
        return {"ok": False, "via": "openclaw", "action": action,
                "reason": "OpenClaw gateway unavailable", "fallback": True}

    try:
        if spec["kind"] == "tool":
            res = actions.tools_invoke(
                spec["tool"],
                args=args.get("args") or spec.get("args"),
                action=spec.get("action"),
                session_key=args.get("session_key"),
            )
            if res.get("ok"):
                return {"ok": True, "via": "openclaw", "action": action,
                        "output": res.get("text", "")}
            return {"ok": False, "via": "openclaw", "action": action,
                    "reason": res.get("error", "tool invoke failed"),
                    "fallback": True}

        if spec["kind"] == "rpc":
            res = actions.gateway_call(spec["method"], params=args.get("params"))
            if res.get("ok"):
                payload = res.get("data", res.get("raw", ""))
                output = payload if isinstance(payload, str) else json.dumps(payload)
                return {"ok": True, "via": "openclaw", "action": action,
                        "output": output}
            return {"ok": False, "via": "openclaw", "action": action,
                    "reason": res.get("error", "rpc failed"), "fallback": True}

        if spec["kind"] == "nl":
            phrase = spec["template"].format(**args) if args else spec["template"]
            res = actions.agent_turn(phrase, deliver=False)
            if res.get("ok"):
                payload = res.get("data", res.get("raw", ""))
                output = payload if isinstance(payload, str) else json.dumps(payload)
                return {"ok": True, "via": "openclaw", "action": action,
                        "output": output}
            return {"ok": False, "via": "openclaw", "action": action,
                    "reason": res.get("error", "agent turn failed"), "fallback": True}

    except Exception as e:  # noqa: BLE001 - never raise to the caller
        log.error("run_action(%s) crashed: %s", action, e)
        return {"ok": False, "via": "openclaw", "action": action,
                "reason": f"bridge error: {e}", "fallback": True}

    return {"ok": False, "via": "openclaw", "action": action,
            "reason": "unknown action kind", "fallback": True}


def dispatch(natural_language: str) -> dict:
    """
    Let OpenClaw plan + execute a real action from natural language.

    Returns:
        {"ok": True,  "output": <str>, "via": "openclaw"}
        {"ok": False, "reason": <str>, "via": "openclaw", "fallback": True}

    Enforces the privacy boundary (refuses NL that references AsthaCash /
    Payment-Gateway / .ssh / secrets). Does NOT deliver to any chat channel.
    Never raises.
    """
    text = (natural_language or "").strip()
    if not text:
        return {"ok": False, "via": "openclaw",
                "reason": "empty instruction", "fallback": False}

    if actions.is_private(text):
        # Hard refusal — do not fall back to executor for protected resources.
        return {"ok": False, "via": "openclaw",
                "reason": "refused: references protected resources",
                "fallback": False}

    if not openclaw_available():
        return {"ok": False, "via": "openclaw",
                "reason": "OpenClaw gateway unavailable", "fallback": True}

    try:
        res = actions.agent_turn(text, deliver=False)
    except Exception as e:  # noqa: BLE001
        log.error("dispatch crashed: %s", e)
        return {"ok": False, "via": "openclaw",
                "reason": f"bridge error: {e}", "fallback": True}

    if res.get("ok"):
        payload = res.get("data", res.get("raw", ""))
        output = payload if isinstance(payload, str) else json.dumps(payload)
        return {"ok": True, "via": "openclaw", "output": output}
    return {"ok": False, "via": "openclaw",
            "reason": res.get("error", "dispatch failed"), "fallback": True}


def tool_invoke(tool: str, args: dict | None = None,
                action: str | None = None,
                session_key: str | None = None) -> dict:
    """
    Invoke a single named OpenClaw tool directly over the HTTP tool surface.

    This is the low-friction "do one thing" entrypoint for callers that already
    know which OpenClaw tool they want (e.g. ``sessions_list``,
    ``memory_search``). It never reaches the gateway control plane or any RCE
    tool — OpenClaw hard-denies those over HTTP (returned here as
    ``ok=False`` with ``fallback=True``).

    Returns:
        {"ok": True,  "output": <str>, "via": "openclaw", "tool": <tool>}
        {"ok": False, "reason": <str>, "via": "openclaw", "tool": <tool>,
         "fallback": True}

    Never raises.
    """
    if not tool:
        return {"ok": False, "via": "openclaw", "tool": tool,
                "reason": "tool name required", "fallback": False}

    if not openclaw_available():
        return {"ok": False, "via": "openclaw", "tool": tool,
                "reason": "OpenClaw gateway unavailable", "fallback": True}

    try:
        res = actions.tools_invoke(tool, args=args, action=action,
                                   session_key=session_key)
    except Exception as e:  # noqa: BLE001
        log.error("tool_invoke(%s) crashed: %s", tool, e)
        return {"ok": False, "via": "openclaw", "tool": tool,
                "reason": f"bridge error: {e}", "fallback": True}

    if res.get("ok"):
        return {"ok": True, "via": "openclaw", "tool": tool,
                "output": res.get("text", "")}
    return {"ok": False, "via": "openclaw", "tool": tool,
            "reason": res.get("error", "tool invoke failed"),
            "fallback": True}


# ── Sync logging ──────────────────────────────────────────────────


def _log_sync(entry: dict) -> None:
    SYNC_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(SYNC_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    pushed = push_memory_to_openclaw()
    pulled = pull_skills_from_openclaw()
    print(f"Sync complete: pushed={pushed}, pulled={pulled}")
