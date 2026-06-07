"""
Jarvis v3 Nano-Bot Swarm — Bot Base Class

Defines the common contract for all seven bot roles. A bot receives a Task,
optionally consults the Blackboard / GossipBus, executes via the shared tool
executor, and returns a BotResult.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional

log = logging.getLogger("jarvis.nano_swarm.bots")


class BotType(Enum):
    SCANNER = "scanner"      # Data collection — read-only scope
    VERIFIER = "verifier"    # Cross-checks scanner results
    FETCHER = "fetcher"      # External data — rate limited
    ANALYZER = "analyzer"    # Pattern detection — read-only
    BUILDER = "builder"      # Code/tool generation — no execution
    TEST = "test"            # Sandboxed validation
    GUARD = "guard"          # Central security gatekeeper


@dataclass
class BotResult:
    bot: str
    task_id: str
    success: bool
    output: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration_ms: float = 0.0

    @classmethod
    def ok(cls, bot, task_id, output="", data=None, duration_ms=0.0):
        return cls(bot=bot, task_id=task_id, success=True, output=output,
                   data=data or {}, duration_ms=duration_ms)

    @classmethod
    def fail(cls, bot, task_id, error, duration_ms=0.0):
        return cls(bot=bot, task_id=task_id, success=False, error=error,
                   duration_ms=duration_ms)


# Tool executor signature: async (tool_name, params) -> dict
ToolExecutor = Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]


class BaseBot:
    """Base class for all nano-bot roles."""

    bot_type: BotType = BotType.SCANNER

    def __init__(self, tool_executor: Optional[ToolExecutor] = None,
                 blackboard=None, gossip=None, guard=None):
        self.tool_executor = tool_executor
        self.blackboard = blackboard
        self.gossip = gossip
        self.guard = guard  # GuardBot instance (None for the guard itself)

    @property
    def name(self) -> str:
        return self.bot_type.value

    def can_handle(self, tool_meta: Dict[str, Any]) -> bool:
        """Whether this bot role is appropriate for a tool. Override per role."""
        return True

    async def run(self, task, tool_meta: Optional[Dict[str, Any]] = None) -> BotResult:
        """
        Execute a task. The default implementation runs the task's tool through
        the Guard gate (if present) then the shared executor. Specialized bots
        override to add role-specific behavior.
        """
        t0 = time.time()
        tool_name = getattr(task, "tool_name", "") or ""
        params = getattr(task, "params", {}) or {}
        task_id = getattr(task, "id", "?")

        # Guard gate — every execution passes through the gatekeeper.
        if self.guard is not None:
            allowed, reason = await self.guard.authorize(tool_name, params, tool_meta)
            if not allowed:
                return BotResult.fail(self.name, task_id,
                                      f"blocked by guard: {reason}",
                                      (time.time() - t0) * 1000)

        if not self.tool_executor:
            return BotResult.fail(self.name, task_id, "no tool executor configured",
                                  (time.time() - t0) * 1000)

        try:
            res = await self.tool_executor(tool_name, params)
        except Exception as exc:  # noqa: BLE001
            return BotResult.fail(self.name, task_id, str(exc),
                                  (time.time() - t0) * 1000)

        dur = (time.time() - t0) * 1000
        success = bool(res.get("success", True))
        result = BotResult(
            bot=self.name, task_id=task_id, success=success,
            output=res.get("output", ""), data=res.get("data", {}) or {},
            error=res.get("error"), duration_ms=dur,
        )
        if self.gossip is not None:
            await self.gossip.publish("task_complete", {
                "bot": self.name, "task": task_id, "tool": tool_name,
                "success": success,
            })
        return result
