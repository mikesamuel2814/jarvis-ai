"""
Jarvis v3 Nano-Bot Swarm — Worker Pool
Asyncio worker coroutines that execute tasks from the PriorityTaskQueue.
"""

import asyncio
import logging
import time
from typing import Callable, Dict, List, Optional

from .task_queue import PriorityTaskQueue, Task, Priority
from .blackboard import Blackboard

log = logging.getLogger("jarvis.nano_swarm")


class WorkerPool:
    """
    Manages a pool of asyncio worker coroutines.
    Each worker pulls tasks from the queue, executes them, and posts results.
    """

    def __init__(
        self,
        queue: PriorityTaskQueue,
        blackboard: Blackboard,
        worker_count: int = 50,
        tool_executor: Optional[Callable] = None,
    ):
        self.queue = queue
        self.blackboard = blackboard
        self.worker_count = worker_count
        self.tool_executor = tool_executor
        self._workers: List[asyncio.Task] = []
        self._running = False
        self._stats = {"executed": 0, "failed": 0, "retried": 0}

    async def start(self):
        """Start all worker coroutines."""
        self._running = True
        for i in range(self.worker_count):
            worker = asyncio.create_task(self._worker_loop(i), name=f"nano_bot_{i}")
            self._workers.append(worker)
        log.info("Started %d nano-bot workers", self.worker_count)

    async def stop(self):
        """Gracefully stop all workers."""
        self._running = False
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        log.info("Stopped all nano-bot workers")

    async def _worker_loop(self, worker_id: int):
        """Main loop for a single worker."""
        while self._running:
            try:
                task = await asyncio.wait_for(self.queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue

            try:
                result = await self._execute_task(task)
                await self.queue.complete(task, result)
                self._stats["executed"] += 1
                self.blackboard.write(f"task:{task.id}:result", result)
            except Exception as exc:
                log.warning("Worker %d task %s failed: %s", worker_id, task.id, exc)
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
        }

    def reprioritize_all(self, prefix: str, new_priority: Priority):
        """Bulk reprioritize tasks matching a tool name prefix."""
        # This would need queue iteration; simplified here
        pass
