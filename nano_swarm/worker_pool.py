"""
Jarvis v3 Nano-Bot Swarm — Self-Healing Worker Pool
Asyncio worker coroutines with health monitoring, auto-restart, and
Python 3.13 safe-mode (sequential execution to avoid C-extension segfaults).
"""

import asyncio
import logging
import sys
import time
from typing import Callable, Dict, List, Optional, Set

from .task_queue import PriorityTaskQueue, Task, Priority
from .blackboard import Blackboard

log = logging.getLogger("jarvis.nano_swarm")

# Python 3.13 has a known eval-frame cache bug when C-extensions are imported
# concurrently inside asyncio tasks. We default to safe mode on 3.13+.
_PYTHON_313_SAFE_MODE = sys.version_info >= (3, 13)


class CircuitBreaker:
    """Simple circuit breaker for tools that fail repeatedly."""

    def __init__(self, failure_threshold: int = 5, recovery_seconds: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._failures: Dict[str, int] = {}
        self._last_failure: Dict[str, float] = {}
        self._open: Set[str] = set()

    def record_failure(self, tool_name: str):
        now = time.time()
        self._failures[tool_name] = self._failures.get(tool_name, 0) + 1
        self._last_failure[tool_name] = now
        if self._failures[tool_name] >= self.failure_threshold:
            self._open.add(tool_name)
            log.warning("Circuit breaker OPEN for %s (%d failures)", tool_name,
                        self._failures[tool_name])

    def record_success(self, tool_name: str):
        if tool_name in self._failures:
            del self._failures[tool_name]
        if tool_name in self._last_failure:
            del self._last_failure[tool_name]
        if tool_name in self._open:
            self._open.discard(tool_name)
            log.info("Circuit breaker CLOSED for %s", tool_name)

    def is_open(self, tool_name: str) -> bool:
        if tool_name not in self._open:
            return False
        # Auto-close after recovery time
        last = self._last_failure.get(tool_name, 0)
        if time.time() - last > self.recovery_seconds:
            self._open.discard(tool_name)
            self._failures.pop(tool_name, None)
            log.info("Circuit breaker recovered for %s", tool_name)
            return False
        return True


class WorkerPool:
    """
    Manages a pool of asyncio worker coroutines.
    Self-healing: monitors workers and restarts dead ones.
    Python 3.13 safe mode: falls back to sequential execution.
    """

    def __init__(
        self,
        queue: PriorityTaskQueue,
        blackboard: Blackboard,
        worker_count: int = 50,
        tool_executor: Optional[Callable] = None,
        monitor_interval: float = 10.0,
    ):
        self.queue = queue
        self.blackboard = blackboard
        self.worker_count = worker_count
        self.tool_executor = tool_executor
        self._workers: List[asyncio.Task] = []
        self._running = False
        self._stats = {"executed": 0, "failed": 0, "retried": 0, "restarted": 0}
        self._monitor_interval = monitor_interval
        self._monitor_task: Optional[asyncio.Task] = None
        self._circuit = CircuitBreaker()
        self._safe_mode = _PYTHON_313_SAFE_MODE
        if self._safe_mode:
            log.info("Python 3.13 detected — worker pool running in SAFE MODE (sequential)")

    async def start(self):
        """Start all worker coroutines and health monitor."""
        self._running = True
        if self._safe_mode:
            # Safe mode: single sequential worker to avoid concurrent C-extension imports
            self.worker_count = 1
            worker = asyncio.create_task(self._worker_loop(0), name="nano_bot_safe_0")
            self._workers.append(worker)
            log.info("Started 1 safe-mode nano-bot worker (Python 3.13 compat)")
        else:
            for i in range(self.worker_count):
                worker = asyncio.create_task(self._worker_loop(i), name=f"nano_bot_{i}")
                self._workers.append(worker)
            log.info("Started %d nano-bot workers", self.worker_count)
        # Start health monitor
        self._monitor_task = asyncio.create_task(self._monitor_workers(), name="nano_monitor")

    async def stop(self):
        """Gracefully stop all workers and monitor."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        log.info("Stopped all nano-bot workers")

    async def _monitor_workers(self):
        """Health monitor: restart dead workers every N seconds."""
        while self._running:
            try:
                await asyncio.sleep(self._monitor_interval)
            except asyncio.CancelledError:
                break
            if not self._running:
                break
            # Check for dead workers (done/cancelled/exception)
            dead_indices = []
            for i, w in enumerate(self._workers):
                if w.done():
                    dead_indices.append(i)
                    if w.exception():
                        log.warning("Worker %s died with exception: %s", w.get_name(), w.exception())
                    else:
                        log.debug("Worker %s exited normally", w.get_name())
            # Remove dead workers from list (reverse order to preserve indices)
            for i in reversed(dead_indices):
                self._workers.pop(i)
            # Respawn missing workers
            missing = self.worker_count - len(self._workers)
            for _ in range(missing):
                next_id = len(self._workers)
                name = f"nano_bot_safe_{next_id}" if self._safe_mode else f"nano_bot_{next_id}"
                worker = asyncio.create_task(self._worker_loop(next_id), name=name)
                self._workers.append(worker)
                self._stats["restarted"] += 1
                log.info("Restarted worker %s (self-heal)", name)

    async def _worker_loop(self, worker_id: int):
        """Main loop for a single worker."""
        while self._running:
            try:
                task = await asyncio.wait_for(self.queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue

            # Circuit breaker check
            if self._circuit.is_open(task.tool_name):
                log.warning("Worker %d: circuit open for %s, skipping task %s",
                            worker_id, task.tool_name, task.id)
                await self.queue.complete(task, {}, error="circuit_open")
                self.queue.task_done()
                continue

            try:
                result = await self._execute_task(task)
                await self.queue.complete(task, result)
                self._stats["executed"] += 1
                self._circuit.record_success(task.tool_name)
                self.blackboard.write(f"task:{task.id}:result", result)
            except Exception as exc:
                log.warning("Worker %d task %s failed: %s", worker_id, task.id, exc)
                self._circuit.record_failure(task.tool_name)
                if task.retry_count < task.max_retries:
                    task.retry_count += 1
                    self._stats["retried"] += 1
                    await self.queue.submit(task)
                else:
                    await self.queue.complete(task, {}, error=str(exc))
                    self._stats["failed"] += 1
            finally:
                self.queue.task_done()

    async def _execute_task(self, task: Task) -> dict:
        """Execute a single task. Override for custom logic."""
        if self.tool_executor:
            return await self.tool_executor(task.tool_name, task.params)
        # Default: simulate work
        await asyncio.sleep(0.01)
        return {"tool": task.tool_name, "status": "ok"}

    @property
    def stats(self) -> dict:
        return {
            **self._stats,
            "queue": self.queue.stats,
            "circuit_open": list(self._circuit._open),
            "safe_mode": self._safe_mode,
        }

    @property
    def is_healthy(self) -> bool:
        """True if all expected workers are running."""
        return len(self._workers) == self.worker_count and all(not w.done() for w in self._workers)

    def reprioritize_all(self, prefix: str, new_priority: Priority):
        """Bulk reprioritize tasks matching a tool name prefix."""
        # This would need queue iteration; simplified here
        pass
