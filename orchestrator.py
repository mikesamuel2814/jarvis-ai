"""
Jarvis v3 Master Orchestrator
6-step pipeline: Intent → Decompose → Scope → Trust → Dispatch → Synthesize
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from models.tier import BrainTier
from nano_swarm.task_queue import PriorityTaskQueue, Task, Priority
from nano_swarm.blackboard import Blackboard
from nano_swarm.gossip import GossipBus
from security.scope_enforcer import ScopeEnforcer, ScopeLevel
from security.trust_registry import TrustRegistry
from security.permission_engine import PermissionEngine
from thinking.intent_classifier import IntentClassifier, Intent
from thinking.decomposer import TaskDecomposer, SubTask
from tools.registry import ToolRegistry

log = logging.getLogger("jarvis.orchestrator")


@dataclass
class OrchestratorResult:
    answer: str = ""
    steps: List[dict] = field(default_factory=list)
    tasks_dispatched: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    needs_approval: Optional[str] = None
    permission_request_id: str = ""
    permission_keyboard: List[dict] = field(default_factory=list)
    elapsed_sec: float = 0.0


class Orchestrator:
    """
    Master orchestrator that routes requests through the 6-step pipeline.
    """

    def __init__(
        self,
        tool_registry: Optional[ToolRegistry] = None,
        scope_enforcer: Optional[ScopeEnforcer] = None,
        trust_registry: Optional[TrustRegistry] = None,
        permission_engine: Optional[PermissionEngine] = None,
        worker_count: int = 50,
    ):
        self.tools = tool_registry or ToolRegistry()
        self.scope_enforcer = scope_enforcer or ScopeEnforcer()
        self.trust_registry = trust_registry or TrustRegistry()
        self.permission_engine = permission_engine or PermissionEngine()
        self.intent_classifier = IntentClassifier()
        self.decomposer = TaskDecomposer()
        self.brain_router = None  # Lazy import to avoid circular deps

        # Progressive-trust permission handler (shared across the process so the
        # API can resolve button presses that arrive via the Telegram bot).
        from telegram_ui.callbacks import PermissionCallbackHandler
        self.permission_handler = PermissionCallbackHandler(
            trust_registry=self.trust_registry,
            executor=self._execute_approved,
        )

        # Swarm
        self.queue = PriorityTaskQueue()
        self.blackboard = Blackboard()
        self.gossip = GossipBus()

        # Guard-gated bot dispatcher: every tool execution passes through the
        # GuardBot (shares the orchestrator's scope enforcer + trust registry),
        # then runs through the appropriate bot role.
        from nano_swarm.bots import BotDispatcher, GuardBot
        self._guard = GuardBot(
            scope_enforcer=self.scope_enforcer,
            trust_registry=self.trust_registry,
            tool_registry=self.tools,
            blackboard=self.blackboard,
            gossip=self.gossip,
        )
        self._bot_dispatcher = BotDispatcher(
            tool_executor=self._raw_execute_tool,
            blackboard=self.blackboard,
            gossip=self.gossip,
            guard=self._guard,
            tool_registry=self.tools,
        )

        # Worker pool is available for background/long-running task queues;
        # the orchestrator uses asyncio.gather for immediate per-request
        # dispatch (lower latency, no worker startup cost).

    async def _raw_execute_tool(self, tool_name: str, params: dict) -> dict:
        """Low-level tool execution (called by bots after the Guard gate)."""
        result = self.tools.execute(tool_name, **params)
        return {
            "success": result.success,
            "output": result.output,
            "data": result.data,
            "error": result.error,
            "duration_ms": result.duration_ms,
        }

    async def _execute_tool(self, tool_name: str, params: dict) -> dict:
        """Executor passed to the worker pool — routes through the bot swarm so
        the Guard gate applies. Builds a lightweight task wrapper."""
        from nano_swarm.task_queue import Task
        task = Task(id=f"x_{tool_name}", tool_name=tool_name, params=params)
        res = await self._bot_dispatcher.dispatch(task)
        return {
            "success": res.success, "output": res.output,
            "data": res.data, "error": res.error, "duration_ms": res.duration_ms,
        }

    async def _execute_approved(self, tool_name: str, params: dict) -> dict:
        """Run a tool that Sir has explicitly approved via Telegram. Approval
        grants execution, so this bypasses the Guard's trust gate but still runs
        through the registry."""
        return await self._raw_execute_tool(tool_name, params)

    async def run(self, request: str, allow_destructive: bool = False) -> OrchestratorResult:
        """Run the full orchestration pipeline."""
        t0 = time.time()
        result = OrchestratorResult()

        # Step 1: Intent Classification
        intent = self.intent_classifier.classify(request)
        log.info("Intent: %s | Urgency: %s | Scope: %s | Complexity: %d",
                 intent.category.value, intent.urgency.value, intent.scope, intent.complexity)

        # Step 2: Task Decomposition
        subtasks = self.decomposer.decompose(request, intent)
        available_tools = self.tools.list_tools()
        subtasks = self.decomposer.assign_tools(subtasks, available_tools)
        log.info("Decomposed into %d sub-tasks", len(subtasks))

        # Steps 3+4: Scope + Trust gate (unified).
        # An action is auto-cleared only if it's within the session scope AND
        # low-rank (R0/R1), or already trusted under allow_destructive. Anything
        # that exceeds scope OR needs trust routes to the SAME approval pause —
        # producing a Telegram button — rather than a silent hard-block. The
        # pause never executes; only Sir pressing Allow does (via the handler,
        # which bypasses the gate for that one approved call).
        #
        # Escalate scope BEFORE the gate if destructive actions are allowed,
        # so pre-trusted tools can auto-run.
        if allow_destructive:
            self.scope_enforcer.escalate(
                ScopeLevel.PRIVILEGED, "orchestrator allow_destructive run")
        paused_task = None
        paused_rank = "R2"
        for st in subtasks:
            meta = self.tools.get(st.tool_name or "")
            scope = ScopeLevel(meta.get("scope", "READ")) if meta else ScopeLevel.READ
            rank = meta.get("rank", "R0") if meta else "R0"
            scope_ok = self.scope_enforcer.can_execute(scope)
            if scope_ok and rank in ("R0", "R1"):
                continue  # Within scope + low rank → auto-run.
            if scope_ok and allow_destructive and \
                    self.trust_registry.is_trusted(st.tool_name or "", st.params):
                continue  # Pre-trusted destructive run → auto-run.
            paused_task = st
            paused_rank = rank if rank not in ("R0", "R1") else (
                "R3" if scope != ScopeLevel.READ else "R2")
            break

        if paused_task:
            req = self.permission_engine.build_request(
                tool_name=paused_task.tool_name or "",
                command_display=str(paused_task.description),
                rank=paused_rank,
            )
            # Register the pending permission so a Telegram button press (which
            # arrives in a different process, via the API) can resolve it.
            rank = paused_rank
            pending = self.permission_handler.register(
                tool_name=paused_task.tool_name or "",
                params=paused_task.params,
                rank=rank,
                command_display=str(paused_task.description),
            )
            result.needs_approval = paused_task.tool_name
            result.permission_request_id = pending.request_id
            result.permission_keyboard = self.permission_handler.build_keyboard(pending)
            result.answer = req["text"]
            result.elapsed_sec = time.time() - t0
            # HIGH: Jarvis paused an action awaiting Sir's approval.
            try:
                from notifier import get_notifier
                get_notifier().decision(
                    f"awaiting approval: {paused_task.tool_name}",
                    str(paused_task.description)[:120],
                    level=__import__("notifier").Level.HIGH, source="orchestrator")
            except Exception:  # noqa: BLE001
                pass
            return result

        # Step 5: Nano-Bot Dispatch
        # The orchestrator has already cleared scope+trust (steps 3-4); align the
        # in-dispatch Guard so it doesn't re-block approved work.
        if allow_destructive:
            self._guard.allow_destructive = True
        # Python 3.13 compat: worker pool segfaults with asyncio tasks + C extensions.
        # Execute subtasks directly via asyncio.gather instead.
        result.tasks_dispatched = len(subtasks)
        coros = [self._execute_tool(st.tool_name or "", st.params) for st in subtasks]
        exec_results = await asyncio.gather(*coros, return_exceptions=True)
        for st, res in zip(subtasks, exec_results):
            if isinstance(res, Exception):
                log.warning("Task %s failed: %s", st.id, res)
                result.tasks_failed += 1
                self.blackboard.write(f"task:{st.id}:result", {"error": str(res)})
            else:
                result.tasks_completed += 1
                self.blackboard.write(f"task:{st.id}:result", res)
                if st.tool_name:
                    result.steps.append({
                        "task": st.id,
                        "tool": st.tool_name,
                        "result": res,
                    })

        # Step 6: Result Synthesis (LLM-backed, template fallback)
        result.answer = await self._synthesize_llm(request, result.steps, intent)
        result.elapsed_sec = time.time() - t0
        return result

    async def _synthesize_llm(self, request: str, steps: List[dict],
                              intent: Intent) -> str:
        """Synthesize a natural 'Sir, ...' answer using the brain router. Falls
        back to the deterministic template if no model backend is reachable."""
        if not steps:
            return "Sir, I couldn't gather any information on that."
        facts = "\n".join(
            f"- {s['tool']}: {str(s.get('result', {}).get('output', ''))[:300]}"
            for s in steps)
        try:
            if self.brain_router is None:
                from brain_router import BrainRouter
                self.brain_router = BrainRouter()
            system = ("You are Jarvis, addressing the user as Sir. Summarize the "
                      "tool findings into a concise, professional answer. Be direct.")
            prompt = f"Request: {request}\n\nTool findings:\n{facts}\n\nAnswer:"
            resp = await self.brain_router.execute(prompt, system_prompt=system)
            if resp and resp.content.strip():
                return resp.content.strip()
        except Exception as exc:  # noqa: BLE001
            log.debug("LLM synthesis failed, using template: %s", exc)
        return self._synthesize(request, steps, intent)

    def _synthesize(self, request: str, steps: List[dict], intent: Intent) -> str:
        """Generate a professional 'Sir, ...' answer from step results."""
        if not steps:
            return "Sir, I couldn't gather any information on that."

        lines = ["Sir, here's what I found:"]
        for step in steps:
            r = step.get("result", {})
            output = r.get("output", "")[:200]
            if output:
                lines.append(f"• *{step['tool']}*: {output}")

        if intent.category.value == "security":
            lines.append("\n🔒 Security assessment complete. Review any HIGH findings above.")
        elif intent.category.value == "system":
            lines.append("\n📊 System stats gathered. Let me know if you need deeper analysis.")

        return "\n".join(lines)
