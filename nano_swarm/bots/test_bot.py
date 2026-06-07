"""Test Bot — sandboxed validation of generated code."""

import ast
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict
from .base import BaseBot, BotType, BotResult


class TestBot(BaseBot):
    """
    Validates code produced by the Builder bot in an isolated subprocess.
    Performs (1) AST syntax check and (2) optional import/compile smoke test
    via `python -m py_compile`. Never runs untrusted code in-process.
    """

    bot_type = BotType.TEST

    async def run(self, task, tool_meta=None) -> BotResult:
        t0 = time.time()
        task_id = getattr(task, "id", "?")
        params = getattr(task, "params", {}) or {}
        code = params.get("code")
        if code is None and self.blackboard is not None:
            code = self.blackboard.read(f"build:{task_id}:code")
        if not code:
            return BotResult.fail(self.name, task_id, "no code to test")

        # 1. AST syntax check.
        try:
            ast.parse(code)
        except SyntaxError as exc:
            return BotResult.fail(self.name, task_id,
                                  f"syntax error: line {exc.lineno}: {exc.msg}",
                                  (time.time() - t0) * 1000)

        # 2. Compile in an isolated subprocess (no execution of module body).
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(code)
            tmp = fh.name
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", tmp],
                capture_output=True, text=True, timeout=20,
            )
            ok = proc.returncode == 0
            err = proc.stderr.strip() if not ok else None
        except subprocess.TimeoutExpired:
            ok, err = False, "compile timed out"
        finally:
            Path(tmp).unlink(missing_ok=True)

        dur = (time.time() - t0) * 1000
        if not ok:
            return BotResult.fail(self.name, task_id, f"compile failed: {err}", dur)
        return BotResult.ok(self.name, task_id, output="syntax+compile OK",
                            data={"passed": True}, duration_ms=dur)
