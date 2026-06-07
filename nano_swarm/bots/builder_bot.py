"""Builder Bot — code/tool generation. NEVER executes generated code."""

import time
from typing import Any, Dict, Optional
from .base import BaseBot, BotType, BotResult


class BuilderBot(BaseBot):
    """
    Generates code (tools, scripts, patches) using a code-generation callable —
    Kimi K2.6 by default. Output is returned as text only; the Builder never
    executes what it produces (spec Part 5.2). Validation/execution is delegated
    to the Test bot in a sandbox.
    """

    bot_type = BotType.BUILDER

    def __init__(self, generator: Optional[Any] = None, **kw):
        super().__init__(**kw)
        # generator: callable(prompt:str, system:str) -> str  (e.g. KimiClient.complete)
        self._generator = generator

    def _gen(self, prompt: str, system: str) -> str:
        if self._generator is None:
            from kimi.client import get_client
            self._generator = get_client().complete
        return self._generator(prompt, system=system)

    async def run(self, task, tool_meta=None) -> BotResult:
        t0 = time.time()
        task_id = getattr(task, "id", "?")
        params = getattr(task, "params", {}) or {}
        spec = params.get("spec") or params.get("prompt") or \
            getattr(task, "description", "")
        if not spec:
            return BotResult.fail(self.name, task_id, "no build spec provided")

        system = ("You are a senior Python engineer. Produce only valid, "
                  "self-contained Python code. No execution, no explanations.")
        try:
            code = self._gen(str(spec), system)
        except Exception as exc:  # noqa: BLE001
            return BotResult.fail(self.name, task_id, f"generation failed: {exc}",
                                  (time.time() - t0) * 1000)

        if self.blackboard is not None:
            self.blackboard.write(f"build:{task_id}:code", code, persist=False)
        return BotResult.ok(self.name, task_id,
                            output=f"generated {len(code)} chars (not executed)",
                            data={"code": code},
                            duration_ms=(time.time() - t0) * 1000)
