"""
Tool Builder — 5-Phase Pipeline Orchestrator
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .security_bot import SecurityBot

log = logging.getLogger("jarvis.tool_builder")

JARVIS_HOME = Path("/home/kali/.jarvis")
DYNAMIC_DIR = JARVIS_HOME / "tools" / "dynamic"

# Rank/scope inference heuristics from the requirement text.
_PRIV = re.compile(r"\b(sudo|root|systemctl|iptables|reboot|mount|kill)\b", re.I)
_NET = re.compile(r"\b(http|url|fetch|download|api|request|dns|ping|scan)\b", re.I)
_WRITE = re.compile(r"\b(write|create|delete|modify|move|chmod|commit)\b", re.I)


@dataclass
class BuildResult:
    name: str
    success: bool
    phase: str = ""
    code: str = ""
    rank: str = "R0"
    scope: str = "READ"
    security: Dict[str, Any] = field(default_factory=dict)
    test: Dict[str, Any] = field(default_factory=dict)
    deployed_path: str = ""
    error: str = ""


class ToolBuilder:
    def __init__(self, generator: Optional[Any] = None,
                 blackboard=None, gossip=None):
        # generator: callable(prompt, system) -> str  (KimiClient.complete)
        self._generator = generator
        self.security_bot = SecurityBot()
        self.blackboard = blackboard
        self.gossip = gossip

    # ── Phase 1 ────────────────────────────────────────────────────────────
    def analyze(self, requirement: str, name: Optional[str] = None) -> Dict[str, Any]:
        scope, rank = "READ", "R0"
        if _PRIV.search(requirement):
            scope, rank = "PRIVILEGED", "R4"
        elif _NET.search(requirement):
            scope, rank = "NETWORK", "R0"
        elif _WRITE.search(requirement):
            scope, rank = "LOCAL", "R2"
        if not name:
            words = re.findall(r"[a-z]+", requirement.lower())[:3]
            name = "_".join(words) or f"tool_{int(time.time())}"
        return {"name": name, "scope": scope, "rank": rank}

    # ── Phase 2 ────────────────────────────────────────────────────────────
    async def generate(self, requirement: str, spec: Dict[str, Any]) -> str:
        from nano_swarm.bots import BuilderBot
        from nano_swarm.task_queue import Task
        bot = BuilderBot(generator=self._generator, blackboard=self.blackboard)
        prompt = (
            f"Write a single Python function named `{spec['name']}` decorated with "
            f"@jarvis_tool(name='{spec['name']}', description=..., params={{}}, "
            f"rank='{spec['rank']}', scope='{spec['scope']}', category='dynamic'). "
            f"It must return a ToolResult (from tools.result). Requirement: {requirement}"
        )
        task = Task(id=f"build_{spec['name']}", tool_name="build:tool",
                    params={"spec": prompt})
        res = await bot.run(task)
        if not res.success:
            raise RuntimeError(res.error or "generation failed")
        return self._strip_fences(res.data.get("code", ""))

    @staticmethod
    def _strip_fences(code: str) -> str:
        code = code.strip()
        if code.startswith("```"):
            code = re.sub(r"^```[a-zA-Z]*\n", "", code)
            code = re.sub(r"\n```$", "", code)
        return code.strip()

    # ── Phase 4 ────────────────────────────────────────────────────────────
    async def test(self, code: str, name: str) -> Dict[str, Any]:
        from nano_swarm.bots import TestBot
        from nano_swarm.task_queue import Task
        bot = TestBot()
        task = Task(id=f"test_{name}", tool_name="test:code", params={"code": code})
        res = await bot.run(task)
        return {"passed": res.success, "detail": res.output or res.error}

    # ── Phase 5 ────────────────────────────────────────────────────────────
    def deploy(self, name: str, code: str, spec: Dict[str, Any],
               security: Dict[str, Any], test: Dict[str, Any]) -> str:
        base = DYNAMIC_DIR / name
        existing = [int(p.name[1:]) for p in base.glob("v*") if p.name[1:].isdigit()] \
            if base.exists() else []
        version = (max(existing) + 1) if existing else 1
        vdir = base / f"v{version}"
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "tool.py").write_text(code)
        (vdir / "security_audit.json").write_text(json.dumps(security, indent=2))
        (vdir / "manifest.json").write_text(json.dumps({
            "name": name, "rank": spec["rank"], "scope": spec["scope"],
            "version": version, "created": time.time(), "test": test,
        }, indent=2))
        return str(vdir)

    # ── Full pipeline ────────────────────────────────────────────────────────
    async def build(self, requirement: str, name: Optional[str] = None,
                    code: Optional[str] = None, deploy: bool = True) -> BuildResult:
        spec = self.analyze(requirement, name)
        r = BuildResult(name=spec["name"], success=False,
                        rank=spec["rank"], scope=spec["scope"])

        # Phase 2 — generate (skippable by passing code directly, e.g. for tests).
        if code is None:
            try:
                code = await self.generate(requirement, spec)
            except Exception as exc:  # noqa: BLE001
                r.phase, r.error = "generate", str(exc)
                return r
        r.code = code

        # Phase 3 — security review (blocks on CRITICAL/HIGH).
        report = self.security_bot.review(code)
        r.security = report.to_dict()
        if not report.passed:
            r.phase = "security"
            r.error = f"{len(report.findings)} security finding(s)"
            return r

        # Phase 4 — sandboxed test.
        r.test = await self.test(code, spec["name"])
        if not r.test.get("passed"):
            r.phase, r.error = "test", r.test.get("detail", "test failed")
            return r

        # Phase 5 — deploy.
        if deploy:
            r.deployed_path = self.deploy(spec["name"], code, spec,
                                          r.security, r.test)
        r.phase, r.success = "deployed", True
        return r


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    good = (
        "from tools.result import ToolResult\n"
        "def disk_free():\n"
        "    import shutil\n"
        "    u = shutil.disk_usage('/')\n"
        "    return ToolResult.ok(output=f'{u.free // (1024**3)}GB free')\n"
    )
    bad = "API_KEY = 'sk-supersecretkey1234567890'\nimport os\nos.system('rm -rf /')\n"

    async def main():
        tb = ToolBuilder()
        r1 = await tb.build("check disk free space", name="disk_free",
                            code=good, deploy=True)
        print("GOOD:", r1.success, r1.phase, "->", r1.deployed_path)
        r2 = await tb.build("leak secrets", name="evil", code=bad, deploy=True)
        print("BAD :", r2.success, r2.phase, r2.error,
              "| findings:", [f['category'] for f in r2.security['findings']])

    asyncio.run(main())
