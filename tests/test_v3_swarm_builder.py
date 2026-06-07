#!/usr/bin/env python3
"""
Jarvis v3 — Swarm Bots + Auto-Remediation + Tool Builder tests.
Run: python3 tests/test_v3_swarm_builder.py
"""

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path("/home/kali/.jarvis")))

import tools
from tools.registry import ToolRegistry
from nano_swarm.bots import BotDispatcher
from nano_swarm.blackboard import Blackboard
from nano_swarm.gossip import GossipBus
from guardian.auto_remedy import AutoRemediator
from tool_builder.builder import ToolBuilder


@dataclass
class Tk:
    id: str
    tool_name: str
    params: dict = field(default_factory=dict)
    description: str = ""


def check(label, cond):
    print(f"  {'PASS' if cond else 'FAIL'}: {label}")
    assert cond, label


async def test_swarm():
    print("[swarm bots]")
    tools.load_all_tools()
    reg = ToolRegistry()

    async def execu(name, params):
        r = reg.execute(name, **params)
        return {"success": r.success, "output": r.output, "data": r.data, "error": r.error}

    bb = Blackboard(persist_path=Path("/tmp/v3_test_bb.json"))
    disp = BotDispatcher(tool_executor=execu, blackboard=bb,
                         gossip=GossipBus(), tool_registry=reg)

    r1 = await disp.dispatch(Tk("t1", "os_info"))
    check("scanner runs R0 read-only tool", r1.bot == "scanner" and r1.success)

    r2 = await disp.dispatch(Tk("t2", "service_restart", {"service": "nginx"}))
    check("guard blocks R3/LOCAL tool", (not r2.success) and "guard" in (r2.error or ""))

    bb.write("task:x:result", {"output": "login failed unauthorized critical"})
    r3 = await disp.dispatch(Tk("a1", "analyze:risk"))
    check("analyzer flags HIGH risk", r3.success and r3.data["risk"] == "HIGH")

    r4 = await disp.dispatch(Tk("c1", "test:code", {"code": "x = 1\n"}))
    check("test bot passes good code", r4.success)
    r5 = await disp.dispatch(Tk("c2", "test:code", {"code": "def f(:\n"}))
    check("test bot rejects bad code", not r5.success)


def test_remedy():
    print("[auto-remediation]")
    ar = AutoRemediator(
        executor=lambda a, arg: {"success": True, "output": f"ran {a}"},
        autonomy_gate=lambda a: (a == "restart_ollama", "test"),
        enable_auto=True,
    )
    res = ar.handle_all([
        {"dimension": "service_health", "severity": "P1", "title": "Service Down: ollama"},
        {"dimension": "port_anomalies", "severity": "P5", "title": "Port Anomaly"},
    ])
    by = {r.alert_title: r for r in res}
    check("safe service restart auto-applied",
          by["Service Down: ollama"].executed and by["Service Down: ollama"].success)
    check("security event not auto-remediated",
          by["Port Anomaly"].action == "investigate" and not by["Port Anomaly"].executed)


async def test_builder():
    print("[tool builder]")
    tb = ToolBuilder()
    good = ("from tools.result import ToolResult\n"
            "def t():\n    return ToolResult.ok(output='ok')\n")
    bad = "K='sk-abcdefghij1234567890'\nimport os\nos.system('id')\n"
    r1 = await tb.build("demo", name="v3_test_tool", code=good, deploy=True)
    check("good tool deploys", r1.success and r1.phase == "deployed")
    r2 = await tb.build("evil", name="v3_evil", code=bad, deploy=True)
    check("malicious tool blocked at security phase",
          (not r2.success) and r2.phase == "security")
    # cleanup
    import shutil
    for n in ("v3_test_tool", "v3_evil"):
        shutil.rmtree(Path("/home/kali/.jarvis/tools/dynamic") / n, ignore_errors=True)


def test_codegen():
    print("[code generator — provider independence]")
    from tool_builder.code_generator import CodeGenerator
    gen = CodeGenerator(prefer_local=True)
    # Synthesis path needs no LLM and self-verifies the shell command.
    code = gen._gen_synth("a tool to check disk free space", "")
    import ast
    ast.parse(code)
    check("synthesis produces valid decorated tool",
          "@jarvis_tool" in code and "subprocess" in code)
    check("synthesis maps disk intent to df", "df -h" in code)
    # Chain falls through to synth when no other provider yields code.
    g2 = CodeGenerator()
    g2._gen_kimi = lambda p, s: (_ for _ in ()).throw(RuntimeError("no key"))
    g2._gen_local_code = lambda p, s: ""
    g2._gen_local_reason = lambda p, s: ""
    out, provider = g2.generate("show memory usage", "")
    check("falls back to synth when LLMs unavailable", provider == "synth")


async def main():
    await test_swarm()
    test_remedy()
    test_codegen()
    await test_builder()
    print("\nALL V3 SWARM/BUILDER TESTS PASSED ✅")


if __name__ == "__main__":
    asyncio.run(main())
