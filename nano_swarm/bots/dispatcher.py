"""
Bot Dispatcher — selects the appropriate bot role for each task.

Routing logic (by tool metadata):
  • category network/web + NETWORK scope  → Fetcher
  • build/test pseudo-tools                → Builder / Test
  • verify: prefix                         → Verifier
  • analyze: prefix                        → Analyzer
  • everything else (read/exec)            → Scanner

Every execution bot shares one GuardBot instance, so all state-changing
executions pass through a single security chokepoint.
"""

import logging
from typing import Any, Dict, Optional

from .base import BaseBot, BotType
from .scanner_bot import ScannerBot
from .verifier_bot import VerifierBot
from .fetcher_bot import FetcherBot
from .analyzer_bot import AnalyzerBot
from .builder_bot import BuilderBot
from .test_bot import TestBot
from .guard_bot import GuardBot

log = logging.getLogger("jarvis.nano_swarm.bots.dispatcher")


class BotDispatcher:
    def __init__(self, tool_executor=None, blackboard=None, gossip=None,
                 guard: Optional[GuardBot] = None, tool_registry=None,
                 generator=None):
        self.tool_registry = tool_registry
        self.guard = guard or GuardBot(tool_registry=tool_registry,
                                       blackboard=blackboard, gossip=gossip)
        common = dict(tool_executor=tool_executor, blackboard=blackboard,
                      gossip=gossip, guard=self.guard)
        self.bots: Dict[BotType, BaseBot] = {
            BotType.SCANNER: ScannerBot(**common),
            BotType.VERIFIER: VerifierBot(**common),
            BotType.FETCHER: FetcherBot(**common),
            BotType.ANALYZER: AnalyzerBot(**common),
            BotType.BUILDER: BuilderBot(generator=generator, **common),
            BotType.TEST: TestBot(**common),
            BotType.GUARD: self.guard,
        }

    def _meta(self, tool_name: str) -> Dict[str, Any]:
        if self.tool_registry is not None:
            return self.tool_registry.get(tool_name) or {}
        return {}

    def select(self, task) -> BaseBot:
        tool_name = getattr(task, "tool_name", "") or ""
        if tool_name.startswith("verify:"):
            return self.bots[BotType.VERIFIER]
        if tool_name.startswith("analyze:"):
            return self.bots[BotType.ANALYZER]
        if tool_name.startswith("build:"):
            return self.bots[BotType.BUILDER]
        if tool_name.startswith("test:"):
            return self.bots[BotType.TEST]

        meta = self._meta(tool_name)
        if meta.get("scope") == "NETWORK" and \
                meta.get("category") in ("network", "web"):
            return self.bots[BotType.FETCHER]
        return self.bots[BotType.SCANNER]

    async def dispatch(self, task):
        """Route a task to its bot and execute. Returns a BotResult."""
        bot = self.select(task)
        tool_name = getattr(task, "tool_name", "") or ""
        meta = self._meta(tool_name) if not tool_name.startswith(
            ("verify:", "analyze:", "build:", "test:")) else {}
        log.debug("Dispatch %s -> %s", tool_name, bot.name)
        return await bot.run(task, meta)
