"""
Jarvis v3 Nano-Bot Swarm — Gossip Protocol
Lightweight pub/sub for bot status updates.
"""

import asyncio
import json
import logging
from typing import Callable, Dict, List

log = logging.getLogger("jarvis.nano_swarm.gossip")


class GossipBus:
    """
    In-memory pub/sub bus for nano-bot communication.
    Channels: task_complete, alert, heartbeat, status_update
    """

    def __init__(self):
        self._channels: Dict[str, List[Callable]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, channel: str, callback: Callable):
        async with self._lock:
            self._channels.setdefault(channel, []).append(callback)

    async def unsubscribe(self, channel: str, callback: Callable):
        async with self._lock:
            if channel in self._channels:
                self._channels[channel] = [c for c in self._channels[channel] if c != callback]

    async def publish(self, channel: str, message: dict):
        async with self._lock:
            callbacks = self._channels.get(channel, []).copy()
        for cb in callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    asyncio.create_task(cb(message))
                else:
                    cb(message)
            except Exception as exc:
                log.warning("Gossip callback error: %s", exc)

    async def heartbeat(self, bot_id: str, status: str):
        await self.publish("heartbeat", {"bot_id": bot_id, "status": status, "ts": asyncio.get_event_loop().time()})
