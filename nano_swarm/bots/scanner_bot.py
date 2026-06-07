"""Scanner Bot — read-only data collection."""

from typing import Any, Dict
from .base import BaseBot, BotType


class ScannerBot(BaseBot):
    """Collects information. Restricted to read-only (READ scope) tools."""

    bot_type = BotType.SCANNER

    def can_handle(self, tool_meta: Dict[str, Any]) -> bool:
        return tool_meta.get("scope", "READ") == "READ" or \
            tool_meta.get("rank", "R0") in ("R0", "R1")
