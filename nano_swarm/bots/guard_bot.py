"""
Jarvis v3 Nano-Bot Swarm — Guard Bot (Security Gatekeeper)

The Guard bot is the single chokepoint through which every state-changing tool
execution must pass. It enforces, in order:

  1. Scope    — tool scope must not exceed the current session scope.
  2. Rank     — R0/R1 auto-pass; R2+ require a saved trust pattern.
  3. Trust    — R2+ tools must match a saved trust entry (Allow & Save).

R0/R1 read-only and user-space tools run freely. Anything higher is blocked
unless explicitly trusted, mirroring the Progressive Trust model (spec Part 2).
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple

from .base import BaseBot, BotType, BotResult
from security.scope_enforcer import ScopeEnforcer, ScopeLevel
from security.trust_registry import TrustRegistry

log = logging.getLogger("jarvis.nano_swarm.bots.guard")

AUTO_RANKS = {"R0", "R1"}


class GuardBot(BaseBot):
    """Central security gatekeeper for all tool executions."""

    bot_type = BotType.GUARD

    def __init__(self, scope_enforcer: Optional[ScopeEnforcer] = None,
                 trust_registry: Optional[TrustRegistry] = None,
                 tool_registry=None, allow_destructive: bool = False, **kw):
        super().__init__(**kw)
        self.scope_enforcer = scope_enforcer or ScopeEnforcer()
        self.trust_registry = trust_registry or TrustRegistry()
        self.tool_registry = tool_registry
        self.allow_destructive = allow_destructive
        self._audit: list = []

    def _meta(self, tool_name: str, tool_meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if tool_meta:
            return tool_meta
        if self.tool_registry is not None:
            return self.tool_registry.get(tool_name) or {}
        return {}

    async def authorize(self, tool_name: str, params: Dict[str, Any],
                        tool_meta: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """Return (allowed, reason). Logs every decision to the audit trail."""
        meta = self._meta(tool_name, tool_meta)
        rank = meta.get("rank", "R0")
        scope_str = meta.get("scope", "READ")
        try:
            scope = ScopeLevel(scope_str)
        except ValueError:
            scope = ScopeLevel.READ

        decision, reason = self._decide(tool_name, params, rank, scope)
        self._audit.append({
            "ts": time.time(), "tool": tool_name, "rank": rank,
            "scope": scope.value, "allowed": decision, "reason": reason,
        })
        if not decision:
            log.warning("Guard BLOCK %s (%s/%s): %s", tool_name, rank, scope.value, reason)
            # Notify on blocks of privileged/security-critical/destructive actions
            # (R4+). Routine R2/R3 gating is normal and stays silent.
            if rank in ("R4", "R5", "R6"):
                try:
                    from notifier import get_notifier
                    get_notifier().blocked(f"{tool_name} ({rank})", reason, "guard")
                except Exception:  # noqa: BLE001
                    pass
        return decision, reason

    def _decide(self, tool_name, params, rank, scope) -> Tuple[bool, str]:
        # 1. Scope check — never exceed the granted session scope.
        if not self.scope_enforcer.can_execute(scope):
            return False, (f"scope {scope.value} exceeds session "
                           f"{self.scope_enforcer.current_scope.value}")

        # 2. Rank gate — read-only / user-space always passes.
        if rank in AUTO_RANKS:
            return True, "auto-rank"

        # 3. R6 destructive — never via the swarm; needs typed confirmation.
        if rank == "R6":
            return False, "R6 destructive requires typed confirmation"

        # 4. R2-R5 — require a saved trust pattern unless destructive mode armed.
        if self.trust_registry.is_trusted(tool_name, params):
            return True, "trusted pattern"
        if self.allow_destructive:
            return True, "allow_destructive session flag"
        return False, f"{rank} requires approval or saved trust"

    async def run(self, task, tool_meta: Optional[Dict[str, Any]] = None) -> BotResult:
        """Guard does not execute tools; it authorizes them."""
        tool_name = getattr(task, "tool_name", "") or ""
        params = getattr(task, "params", {}) or {}
        allowed, reason = await self.authorize(tool_name, params, tool_meta)
        return BotResult.ok(self.name, getattr(task, "id", "?"),
                            output=reason, data={"allowed": allowed})

    def audit_log(self) -> list:
        return list(self._audit)
