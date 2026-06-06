"""
Jarvis v3 Nano-Bot Swarm — Priority Task Queue
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set


class Priority(Enum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    IDLE = 4


@dataclass
class Task:
    id: str
    tool_name: str
    params: dict = field(default_factory=dict)
    priority: Priority = Priority.NORMAL
    deps: Set[str] = field(default_factory=set)  # task IDs this task depends on
    timeout_sec: float = 60.0
    max_retries: int = 2
    retry_count: int = 0
    result: Optional[dict] = None
    error: Optional[str] = None
    done: bool = False


class PriorityTaskQueue:
    """
    asyncio PriorityQueue with dependency resolution.
    Tasks with unmet dependencies are held in a waiting set.
    """

    def __init__(self):
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._waiting: Dict[str, Task] = {}
        self._completed: Dict[str, Task] = {}
        self._lock = asyncio.Lock()

    async def submit(self, task: Task) -> str:
        """Add a task to the queue. Returns task ID."""
        if not task.id:
            task.id = str(uuid.uuid4())[:8]
        async with self._lock:
            if task.deps and not all(d in self._completed for d in task.deps):
                self._waiting[task.id] = task
            else:
                await self._queue.put((task.priority.value, task.id, task))
        return task.id

    async def get(self) -> Task:
        """Get the next ready task. Blocks until available."""
        while True:
            priority, tid, task = await self._queue.get()
            async with self._lock:
                # Check if deps are now satisfied
                if task.deps and not all(d in self._completed for d in task.deps):
                    self._waiting[tid] = task
                    continue
                return task

    async def complete(self, task: Task, result: dict, error: Optional[str] = None):
        """Mark a task as completed and unblock waiting tasks."""
        async with self._lock:
            task.result = result
            task.error = error
            task.done = True
            self._completed[task.id] = task

            # Promote waiting tasks whose deps are now satisfied
            promoted = []
            for tid, wt in list(self._waiting.items()):
                if all(d in self._completed for d in wt.deps):
                    promoted.append(wt)
                    del self._waiting[tid]
            for wt in promoted:
                await self._queue.put((wt.priority.value, wt.id, wt))

    def task_done(self):
        self._queue.task_done()

    async def reprioritize(self, task_id: str, new_priority: Priority):
        """Change priority of a waiting or queued task."""
        async with self._lock:
            if task_id in self._waiting:
                self._waiting[task_id].priority = new_priority

    @property
    def stats(self) -> dict:
        return {
            "queued": self._queue.qsize(),
            "waiting": len(self._waiting),
            "completed": len(self._completed),
        }
