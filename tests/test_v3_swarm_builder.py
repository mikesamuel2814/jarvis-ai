#!/usr/bin/env python3
"""
Jarvis v3 — Swarm Bots + Auto-Remediation + Tool Builder tests.
Run: python3 tests/test_v3_swarm_builder.py
"""

import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Silence real Telegram notifications during tests.
os.environ["JARVIS_NOTIFY_DISABLED"] = "1"

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


def test_brain_clients():
    print("[brain router — model clients]")
    from models.clients import ModelDispatcher
    from models.tier import BrainTier
    d = ModelDispatcher()
    check("all 7 tiers have a client", len(d.clients) == 7)
    # Unavailable tier resolves to an available fallback (never crashes).
    _, used = d.resolve(BrainTier.KIMI)
    check("KIMI falls back to an available tier when key absent",
          d.clients[used].is_available())
    _, used2 = d.resolve(BrainTier.NANO)
    check("NANO resolves (CPU always-on)", used2 in (BrainTier.NANO, BrainTier.EDGE))


async def test_callbacks():
    print("[telegram permission callbacks]")
    from telegram_ui.callbacks import PermissionCallbackHandler
    from security.trust_registry import TrustRegistry
    tr = TrustRegistry()
    ran = []

    async def execu(tool, params):
        ran.append(tool)
        return {"ok": True}

    h = PermissionCallbackHandler(trust_registry=tr, executor=execu)
    p = h.register("v3cb_test_tool", {"x": 1}, "R3", "demo cmd")
    kb = h.build_keyboard(p)
    check("permission keyboard has 3 buttons", len(kb[0]) == 3)
    save_cd = kb[0][1]["callback_data"]
    res = await h.handle(save_cd)
    check("allow & save executes + persists trust",
          res["executed"] and tr.is_trusted("v3cb_test_tool", {"x": 1}))
    check("consumed request expires on re-press",
          (await h.handle(save_cd))["action"] == "expired")
    p6 = h.register("v3cb_destruct", {}, "R6", "dd")
    check("R6 destructive shows no buttons", h.build_keyboard(p6) == [])
    tr.revoke_tool("v3cb_test_tool")  # cleanup


def test_notifier():
    print("[notifier — severity threshold]")
    from notifier import Notifier, Level
    # Fresh instance with the kill-switch off so we can test pure logic.
    os.environ["JARVIS_NOTIFY_DISABLED"] = "0"
    n = Notifier()
    check("GENERAL suppressed", n.notify("x", Level.GENERAL, dry_run=True) is False)
    check("NORMAL suppressed", n.notify("x", Level.NORMAL, dry_run=True) is False)
    check("MEDIUM suppressed (HIGH-only policy)",
          n.notify("routine", Level.MEDIUM, "success", dry_run=True) is False)
    check("HIGH delivered", n.notify("bad", Level.HIGH, "failure", dry_run=True) is True)
    check("CRITICAL delivered",
          n.notify("down", Level.CRITICAL, "security", dry_run=True) is True)
    check("duplicate HIGH suppressed",
          n.notify("bad", Level.HIGH, "failure", dry_run=True) is False)
    # Autonomous-event helpers must classify at HIGH so they reach Sir.
    n2 = Notifier()
    n2._send = lambda text: n2.__dict__.setdefault("_last", text)  # capture, no HTTP
    check("bot_request delivered (HIGH)", n2.bot_request("docker_logs") is True)
    n2._recent.clear()
    check("remediation delivered (HIGH)", n2.remediation("restart ollama", True) is True)
    n2._recent.clear()
    check("routine success suppressed", n2.success("disk read") is False)
    os.environ["JARVIS_NOTIFY_DISABLED"] = "1"
    check("kill-switch suppresses all",
          n.notify("anything", Level.CRITICAL, dry_run=True) is False)


def test_routing_feedback():
    print("[learner — routing-accuracy feedback]")
    from learner import analyze_routing_feedback
    inter = [
        {"tier": "edge", "rating": "bad", "response": "x", "query": "why down"},
        {"tier": "edge", "rating": "bad", "response": "x", "query": "root cause"},
        {"tier": "edge", "rating": "bad", "response": "x", "query": "analyze it"},
        {"tier": "edge", "rating": "good", "response": "ok", "query": "cpu"},
        {"tier": "nano", "rating": "good", "response": "y" * 1500, "query": "explain"},
        {"tier": "nano", "rating": "good", "response": "y" * 1500, "query": "explain2"},
        {"tier": "nano", "rating": "good", "response": "y" * 1500, "query": "explain3"},
        {"tier": "approve", "rating": "good", "response": "z", "query": "legacy v2"},
    ]
    rf = analyze_routing_feedback(inter)  # no chromadb → no lesson upsert
    check("only v3 tiers counted (legacy 'approve' ignored)",
          set(rf["per_tier"]) == {"edge", "nano"})
    check("high-failure tier flagged",
          any("Tier 'edge' has a high failure" in i for i in rf["insights"]))
    check("under-routing (long nano answers) flagged",
          any("nano" in i and "escalation" in i for i in rf["insights"]))
    check("no lesson stored without a collection", rf["lessons_added"] == 0)


async def main():
    await test_swarm()
    test_remedy()
    test_codegen()
    await test_builder()
    test_brain_clients()
    await test_callbacks()
    test_notifier()
    test_routing_feedback()
    print("\nALL V3 SWARM/BUILDER/ROUTER/UI/NOTIFY TESTS PASSED ✅")


if __name__ == "__main__":
    asyncio.run(main())
