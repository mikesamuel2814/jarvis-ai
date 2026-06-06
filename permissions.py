#!/usr/bin/env python3
"""
Jarvis Permission System — approval gating for actions.
Uses a JSON file for shared state between the API process and the bot process.
"""

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

JARVIS_HOME = Path.home() / ".jarvis"
PENDING_FILE = JARVIS_HOME / "data" / "pending_approvals.json"
AUDIT_LOG = JARVIS_HOME / "logs" / "permissions.log"
TIMEOUT_MINUTES = 30

AUTO = "auto"
CONFIRM = "confirm"
APPROVE = "approve"
DENY = "deny"


def _load_pending() -> dict:
    if not PENDING_FILE.exists():
        return {}
    try:
        raw = json.loads(PENDING_FILE.read_text())
        # Expire old requests
        now = datetime.now()
        active = {
            k: v for k, v in raw.items()
            if datetime.fromisoformat(v["expires_at"]) > now and v.get("response") is None
        }
        if len(active) != len(raw):
            _save_pending(active)
        return active
    except Exception:
        return {}


def _save_pending(data: dict):
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps(data, indent=2))


def _audit(req_id: str, event: str):
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG, "a") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {req_id} {event}\n")


def create_request(action: str, description: str, tier: str, arg: str = "", uid: int = 0) -> dict:
    """Create a pending approval request. Returns the request dict."""
    req_id = str(uuid.uuid4())[:8].upper()
    req = {
        "id": req_id,
        "action": action,
        "description": description,
        "tier": tier,
        "arg": arg,
        "uid": uid,
        "created_at": datetime.now().isoformat(),
        "expires_at": (datetime.now() + timedelta(minutes=TIMEOUT_MINUTES)).isoformat(),
        "response": None,
    }
    pending = _load_pending()
    pending[req_id] = req
    _save_pending(pending)
    _audit(req_id, f"created: {action}({arg!r}) tier={tier}")
    return req


def create_task_request(description: str, steps: list[dict], summary: str = "", uid: int = 0) -> dict:
    """Create ONE pending approval for a whole multi-step task.

    steps: [{"action","arg","desc"}, ...] executed in order on a single approval.
    This is the task-level gate — Sir approves the flow once, then Jarvis runs
    every step autonomously (only catastrophic ops re-prompt at execution time).
    """
    req_id = str(uuid.uuid4())[:8].upper()
    req = {
        "id": req_id,
        "kind": "task",
        "action": "task_flow",
        "description": description,
        "summary": summary,
        "steps": steps,
        "tier": APPROVE,
        "arg": "",
        "uid": uid,
        "created_at": datetime.now().isoformat(),
        "expires_at": (datetime.now() + timedelta(minutes=TIMEOUT_MINUTES)).isoformat(),
        "response": None,
    }
    pending = _load_pending()
    pending[req_id] = req
    _save_pending(pending)
    _audit(req_id, f"task created: {len(steps)} step(s) — {description}")
    return req


def respond(req_id: str, response: str) -> dict | None:
    """Record a response (approve/deny) for a pending request."""
    pending = _load_pending()
    req_id = req_id.upper()
    if req_id not in pending:
        return None
    pending[req_id]["response"] = response
    _save_pending(pending)
    _audit(req_id, f"response: {response}")
    return pending[req_id]


def get_request(req_id: str) -> dict | None:
    return _load_pending().get(req_id.upper())


def list_pending() -> list[dict]:
    return list(_load_pending().values())


def format_telegram_message(req: dict) -> str:
    tier_label = {"confirm": "⚡ Quick action", "approve": "🔐 High-risk action"}
    msg = f"{tier_label.get(req['tier'], '🔔 Action')} — ID: `{req['id']}`\n\n"
    msg += f"**{req['description']}**\n"
    if req.get("arg"):
        msg += f"Argument: `{req['arg']}`\n"
    expires_in = int((datetime.fromisoformat(req["expires_at"]) - datetime.now()).total_seconds() / 60)
    msg += f"\nExpires in {expires_in} min. Tap to respond:"
    return msg
