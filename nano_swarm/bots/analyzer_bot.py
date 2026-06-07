"""Analyzer Bot — pattern detection over collected data (read-only)."""

import time
from typing import Any, Dict, List
from .base import BaseBot, BotType, BotResult

# Lightweight signal keywords the analyzer flags in tool output.
RISK_SIGNALS = [
    "failed", "error", "denied", "unauthorized", "vulnerable", "open",
    "world-readable", "nopasswd", "root", "expired", "critical", "exposed",
]


class AnalyzerBot(BaseBot):
    """
    Scans accumulated Blackboard results (or a single task output) for risk
    signals and surfaces findings. Never executes state-changing tools; alerts
    flow out via the GossipBus 'alert' channel.
    """

    bot_type = BotType.ANALYZER

    def can_handle(self, tool_meta: Dict[str, Any]) -> bool:
        return tool_meta.get("scope", "READ") == "READ"

    def _scan_text(self, text: str) -> List[str]:
        low = (text or "").lower()
        return [s for s in RISK_SIGNALS if s in low]

    async def run(self, task, tool_meta=None) -> BotResult:
        t0 = time.time()
        task_id = getattr(task, "id", "?")
        findings: Dict[str, List[str]] = {}

        # Analyze either the whole scan section or the single tool result.
        if self.blackboard is not None:
            section = self.blackboard.read_section("task:")
            for key, val in section.items():
                if isinstance(val, dict):
                    hits = self._scan_text(val.get("output", ""))
                    if hits:
                        findings[key] = hits

        if not findings:
            res = await super().run(task, tool_meta)
            hits = self._scan_text(res.output)
            if hits:
                findings[task_id] = hits
            base_out = res.output
        else:
            base_out = ""

        risk = "HIGH" if any("critical" in v or "vulnerable" in v
                             for v in findings.values()) else \
               "MEDIUM" if findings else "LOW"

        if findings and self.gossip is not None:
            await self.gossip.publish("alert", {
                "bot": self.name, "risk": risk, "findings": findings,
            })

        out = f"[risk={risk}] {len(findings)} finding(s)"
        if base_out:
            out += f" | {base_out[:120]}"
        return BotResult.ok(self.name, task_id, output=out,
                            data={"risk": risk, "findings": findings},
                            duration_ms=(time.time() - t0) * 1000)
