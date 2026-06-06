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
from nano_swarm.worker_pool import WorkerPool
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

        # Swarm
        self.queue = PriorityTaskQueue()
        self.blackboard = Blackboard()
        self.gossip = GossipBus()
        self.worker_pool = WorkerPool(
            queue=self.queue,
            blackboard=self.blackboard,
            worker_count=worker_count,
            tool_executor=self._execute_tool,
        )

    async def _execute_tool(self, tool_name: str, params: dict) -> dict:
        """Tool executor passed to worker pool."""
        result = self.tools.execute(tool_name, **params)
        return {
            "success": result.success,
            "output": result.output,
            "data": result.data,
            "error": result.error,
            "duration_ms": result.duration_ms,
        }

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

        # Step 3: Scope Validation
        for st in subtasks:
            meta = self.tools.get(st.tool_name or "")
            scope = ScopeLevel.READ
            if meta:
                scope = ScopeLevel(meta.get("scope", "READ"))
            if not self.scope_enforcer.check(st.tool_name or "unknown", scope):
                result.answer = f"Sir, I cannot run *{st.tool_name}* because it exceeds the current session scope ({scope.value})."
                result.elapsed_sec = time.time() - t0
                return result

        # Step 4: Trust Check
        paused_task = None
        for st in subtasks:
            meta = self.tools.get(st.tool_name or "")
            rank = meta.get("rank", "R0") if meta else "R0"
            if rank in ("R0", "R1"):
                continue  # Auto-run
            if not allow_destructive:
                paused_task = st
                break
            if not self.trust_registry.is_trusted(st.tool_name or "", st.params):
                paused_task = st
                break

        if paused_task:
            req = self.permission_engine.build_request(
                tool_name=paused_task.tool_name or "",
                command_display=str(paused_task.description),
                rank=meta.get("rank", "R2") if meta else "R2",
            )
            result.needs_approval = paused_task.tool_name
            result.answer = req["text"]
            result.elapsed_sec = time.time() - t0
            return result

        # Step 5: Nano-Bot Dispatch
        await self.worker_pool.start()
        task_map = {}
        for st in subtasks:
            priority = Priority.NORMAL
            if intent.urgency.value == "critical":
                priority = Priority.CRITICAL
            elif intent.urgency.value == "high":
                priority = Priority.HIGH

            task = Task(
                id=st.id,
                tool_name=st.tool_name or "",
                params=st.params,
                priority=priority,
                deps=st.deps,
                timeout_sec=60.0,
            )
            await self.queue.submit(task)
            task_map[st.id] = st
            result.tasks_dispatched += 1

        # Wait for queue to drain
        await self.queue._queue.join()
        await self.worker_pool.stop()

        # Collect results
        for st in subtasks:
            bb_result = self.blackboard.read(f"task:{st.id}:result")
            if bb_result:
                result.tasks_completed += 1
                result.steps.append({
                    "task": st.id,
                    "tool": st.tool_name,
                    "result": bb_result,
                })
            else:
                result.tasks_failed += 1

        # Step 6: Result Synthesis
        result.answer = self._synthesize(request, result.steps, intent)
        result.elapsed_sec = time.time() - t0
        return result

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
