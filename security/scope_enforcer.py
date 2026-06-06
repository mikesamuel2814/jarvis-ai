"""
Jarvis v3 Scope Enforcer
Every tool declares a scope (READ, LOCAL, NETWORK, PRIVILEGED).
The enforcer blocks actions that exceed the current session scope.
"""

from enum import Enum
from typing import Optional, Set
import logging

logger = logging.getLogger("jarvis.security.scope")


class ScopeLevel(Enum):
    READ = "READ"           # Information gathering only
    LOCAL = "LOCAL"         # Modifies local files/services
    NETWORK = "NETWORK"     # External network operations
    PRIVILEGED = "PRIVILEGED"  # Requires root/sudo


class ScopeEnforcer:
    """
    Validates that tool invocations stay within the granted session scope.
    Escalation requires explicit user approval.
    """

    # Ordered escalation path
    ESCALATION = [
        ScopeLevel.READ,
        ScopeLevel.LOCAL,
        ScopeLevel.NETWORK,
        ScopeLevel.PRIVILEGED,
    ]

    def __init__(self, current_scope: ScopeLevel = ScopeLevel.READ):
        self.current_scope = current_scope
        self._escalation_log: list = []

    def can_execute(self, tool_scope: ScopeLevel) -> bool:
        """Check if a tool's scope is within current session scope."""
        return self._level_index(tool_scope) <= self._level_index(self.current_scope)

    def check(self, tool_name: str, tool_scope: ScopeLevel) -> bool:
        """Raise ScopeViolation if tool exceeds current scope."""
        if not self.can_execute(tool_scope):
            logger.warning(
                "Scope violation: tool=%s scope=%s current=%s",
                tool_name, tool_scope.value, self.current_scope.value
            )
            return False
        logger.debug("Scope OK: %s (%s)", tool_name, tool_scope.value)
        return True

    def escalate(self, new_scope: ScopeLevel, reason: str, approved_by: str = "user") -> bool:
        """Elevate session scope with audit trail."""
        if self._level_index(new_scope) <= self._level_index(self.current_scope):
            return True
        logger.info(
            "Scope escalation: %s → %s | reason=%s | approved_by=%s",
            self.current_scope.value, new_scope.value, reason, approved_by
        )
        self._escalation_log.append({
            "from": self.current_scope.value,
            "to": new_scope.value,
            "reason": reason,
            "approved_by": approved_by,
        })
        self.current_scope = new_scope
        return True

    def _level_index(self, scope: ScopeLevel) -> int:
        return self.ESCALATION.index(scope)

    def get_log(self) -> list:
        return self._escalation_log.copy()


class ScopeViolation(Exception):
    """Raised when a tool's scope exceeds the session scope."""
    pass


# ── Decorator integration ────────────────────────────────────────────────

SCOPE_REGISTRY: dict = {}


def register_scope(tool_name: str, scope: ScopeLevel):
    """Register a tool's scope (called by @jarvis_tool decorator)."""
    SCOPE_REGISTRY[tool_name] = scope


def get_scope(tool_name: str) -> Optional[ScopeLevel]:
    return SCOPE_REGISTRY.get(tool_name)


def require_scope(enforcer: ScopeEnforcer, tool_name: str) -> bool:
    scope = get_scope(tool_name)
    if scope is None:
        logger.warning("No scope registered for tool %s; defaulting to READ", tool_name)
        scope = ScopeLevel.READ
    return enforcer.check(tool_name, scope)
