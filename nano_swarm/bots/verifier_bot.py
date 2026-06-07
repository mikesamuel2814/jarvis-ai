"""Verifier Bot — cross-checks Scanner results for consistency."""

import time
from typing import Any, Dict
from .base import BaseBot, BotType, BotResult


class VerifierBot(BaseBot):
    """
    Re-runs or cross-references a scanner result to detect flapping/inconsistent
    data. Reads the prior result from the Blackboard, re-executes the read-only
    tool, and reports whether the two agree.
    """

    bot_type = BotType.VERIFIER

    def can_handle(self, tool_meta: Dict[str, Any]) -> bool:
        return tool_meta.get("scope", "READ") == "READ"

    async def run(self, task, tool_meta=None) -> BotResult:
        t0 = time.time()
        task_id = getattr(task, "id", "?")
        prior = None
        if self.blackboard is not None:
            prior = self.blackboard.read(f"task:{task_id}:result")

        res = await super().run(task, tool_meta)
        if not res.success:
            return res

        agree = True
        if prior is not None:
            agree = (prior.get("output", "") == res.output)
        res.data["verified"] = agree
        res.data["consistent_with_prior"] = agree
        res.output = f"[verified={'ok' if agree else 'MISMATCH'}] {res.output}"
        res.duration_ms = (time.time() - t0) * 1000
        return res
