"""Fetcher Bot — external data with rate limiting."""

import time
from typing import Any, Dict
from .base import BaseBot, BotType, BotResult


class FetcherBot(BaseBot):
    """
    Fetches external data (NETWORK scope). Applies a simple token-bucket rate
    limit so the swarm never floods remote endpoints or leaks credentials via
    rapid retries.
    """

    bot_type = BotType.FETCHER
    MAX_PER_WINDOW = 10
    WINDOW_SEC = 10.0

    def __init__(self, **kw):
        super().__init__(**kw)
        self._calls: list = []

    def can_handle(self, tool_meta: Dict[str, Any]) -> bool:
        return tool_meta.get("scope", "READ") in ("NETWORK", "READ") and \
            tool_meta.get("category") in ("network", "web", None)

    def _rate_limited(self) -> bool:
        now = time.time()
        self._calls = [t for t in self._calls if now - t < self.WINDOW_SEC]
        if len(self._calls) >= self.MAX_PER_WINDOW:
            return True
        self._calls.append(now)
        return False

    async def run(self, task, tool_meta=None) -> BotResult:
        if self._rate_limited():
            return BotResult.fail(self.name, getattr(task, "id", "?"),
                                  "rate limited — too many external fetches")
        return await super().run(task, tool_meta)
