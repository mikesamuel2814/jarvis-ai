"""
Jarvis v3 Telegram UI — Permission Callback Handler

Implements the Progressive Trust button flow (spec Part 2.4):

    [Allow]   [Allow & Save]   [Deny]

• Allow        — execute the pending action once.
• Allow & Save — write a trust pattern (so Jarvis never asks again for this
                 tool+params), then execute.
• Deny         — discard the request.

Framework-light: `build_keyboard()` returns rows of {text, callback_data} dicts
that the bot converts to InlineKeyboardMarkup; `handle()` processes the
callback_data string. R6 destructive actions get no buttons — they require typed
confirmation handled elsewhere.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

from security.trust_registry import TrustRegistry

log = logging.getLogger("jarvis.telegram_ui.callbacks")

CB_PREFIX = "v3perm"
EXPIRY_SEC = 1800  # pending requests expire after 30 min


@dataclass
class PendingPermission:
    request_id: str
    tool_name: str
    params: dict
    rank: str
    command_display: str
    executor: Optional[Callable[..., Awaitable[Any]]] = None
    created: float = field(default_factory=time.time)


class PermissionCallbackHandler:
    """Holds pending permission requests and resolves button callbacks."""

    def __init__(self, trust_registry: Optional[TrustRegistry] = None,
                 executor: Optional[Callable] = None):
        self.trust = trust_registry or TrustRegistry()
        self._default_executor = executor   # async (tool_name, params) -> dict
        self._pending: Dict[str, PendingPermission] = {}
        self._counter = 0

    # ── Registration ─────────────────────────────────────────────────────────
    def register(self, tool_name: str, params: dict, rank: str,
                 command_display: str = "",
                 executor: Optional[Callable] = None) -> PendingPermission:
        self._gc()
        self._counter += 1
        rid = f"{int(time.time())}{self._counter}"
        p = PendingPermission(
            request_id=rid, tool_name=tool_name, params=params, rank=rank,
            command_display=command_display or tool_name,
            executor=executor or self._default_executor,
        )
        self._pending[rid] = p
        return p

    def build_keyboard(self, p: PendingPermission) -> list:
        """Return inline-keyboard rows. R6 → no buttons (typed confirm only)."""
        if p.rank == "R6":
            return []
        return [[
            {"text": "✅ Allow", "callback_data": f"{CB_PREFIX}:{p.request_id}:allow"},
            {"text": "💾 Allow & Save", "callback_data": f"{CB_PREFIX}:{p.request_id}:save"},
            {"text": "❌ Deny", "callback_data": f"{CB_PREFIX}:{p.request_id}:deny"},
        ]]

    # ── Callback resolution ──────────────────────────────────────────────────
    @staticmethod
    def owns(callback_data: str) -> bool:
        return callback_data.startswith(CB_PREFIX + ":")

    async def handle(self, callback_data: str) -> dict:
        """Process a button press. Returns {action, message, executed, result}."""
        try:
            _, rid, action = callback_data.split(":", 2)
        except ValueError:
            return {"action": "error", "message": "Sir, malformed request.",
                    "executed": False}

        p = self._pending.get(rid)
        if p is None:
            return {"action": "expired",
                    "message": "Sir, that request has expired.", "executed": False}

        if action == "deny":
            del self._pending[rid]
            return {"action": "deny",
                    "message": f"Sir, denied: `{p.command_display}`.", "executed": False}

        if action == "save":
            self.trust.add_trust(p.tool_name, p.params, p.rank,
                                 notes="Allow & Save via Telegram")
            log.info("Trust saved for %s %s", p.tool_name, p.params)

        # Allow / Save both execute.
        executed, result = await self._execute(p)
        del self._pending[rid]
        saved = " (saved — won't ask again)" if action == "save" else ""
        status = "✅ done" if executed else "⚠️ no executor"
        return {"action": action, "executed": executed, "result": result,
                "message": f"Sir, {status}: `{p.command_display}`{saved}."}

    async def _execute(self, p: PendingPermission) -> tuple:
        if p.executor is None:
            return False, None
        try:
            import asyncio
            if asyncio.iscoroutinefunction(p.executor):
                res = await p.executor(p.tool_name, p.params)
            else:
                res = p.executor(p.tool_name, p.params)
            return True, res
        except Exception as exc:  # noqa: BLE001
            log.warning("Execution failed for %s: %s", p.tool_name, exc)
            return False, {"error": str(exc)}

    def _gc(self):
        now = time.time()
        for rid in [k for k, v in self._pending.items()
                    if now - v.created > EXPIRY_SEC]:
            del self._pending[rid]

    @property
    def pending_count(self) -> int:
        return len(self._pending)
