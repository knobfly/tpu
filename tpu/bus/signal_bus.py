# nyx/bus/signal_bus.py
# ------------------------------------------------------------------
# Central async pub/sub bus for upward-only data flow.
# Collectors publish -> Cortexes consume -> AI brain PULLS from cortexes.
# No imports of ai_brain or cortexes here. Pure infra.
# ------------------------------------------------------------------

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any, AsyncIterator, Deque, Dict, Optional


class _Topic:
    __slots__ = ("name", "queues", "history", "max_history", "lock", "published")

    def __init__(self, name: str, max_history: int = 2000):
        self.name = name
        self.queues: Dict[int, asyncio.Queue] = {}
        self.history: Deque[dict] = deque(maxlen=max_history)
        self.max_history = max_history
        self.lock = asyncio.Lock()
        self.published: int = 0


class SignalBus:
    """
    Async, topic-based signal bus.

    - publish(topic, payload) -> fan-out to all subscribers
    - subscribe(topic) -> get an async iterator or a Queue to consume
    - get_recent(topic, n) -> quick history peek (RAM)
    - get_stats() -> metrics
    """

    def __init__(self):
        self._topics: Dict[str, _Topic] = {}
        self._topic_lock = asyncio.Lock()
        self._sub_id_counter = 0

    async def ensure_topic(self, topic: str) -> _Topic:
        if topic in self._topics:
            return self._topics[topic]
        async with self._topic_lock:
            # double-check to avoid race
            if topic not in self._topics:
                self._topics[topic] = _Topic(topic)
            return self._topics[topic]

    async def publish(self, topic: str, payload: dict) -> None:
        """
        Publish an event (dict) to a topic.
        """
        t = await self.ensure_topic(topic)
        event = {
            "topic": topic,
            "ts": time.time(),
            "payload": payload,
        }
        async with t.lock:
            t.history.append(event)
            t.published += 1
            # fan out
            for q in t.queues.values():
                # don't await put, use put_nowait with best-effort (no blocking)
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    # drop if a consumer is too slow (prevent global stall)
                    pass

    async def subscribe(self, topic: str, max_queue_size: int = 1000) -> "BusSubscription":
        """
        Subscribe to a topic. Returns a BusSubscription which can be:
          - iterated asynchronously (async for event in sub)
          - consumed via sub.queue.get()
        """
        t = await self.ensure_topic(topic)
        async with t.lock:
            self._sub_id_counter += 1
            sub_id = self._sub_id_counter
            q: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
            t.queues[sub_id] = q
            return BusSubscription(bus=self, topic=t, sub_id=sub_id, queue=q)

    async def unsubscribe(self, topic: _Topic, sub_id: int) -> None:
        async with topic.lock:
            topic.queues.pop(sub_id, None)

    async def get_recent(self, topic: str, n: int = 100) -> list[dict]:
        t = await self.ensure_topic(topic)
        async with t.lock:
            if n <= 0:
                return []
            return list(list(t.history)[-n:])

    async def get_stats(self) -> dict:
        out = {}
        # no need to lock for a snapshot; races aren't critical for metrics
        for name, t in self._topics.items():
            out[name] = {
                "published": t.published,
                "subscribers": len(t.queues),
                "history_len": len(t.history),
                "max_history": t.max_history,
            }
        return out


class BusSubscription:
    """
    Handle returned by SignalBus.subscribe().

    Usage:
      sub = await bus.subscribe("wallet_event")
      async for event in sub:
          ...

    Or:
      event = await sub.queue.get()
    """

    def __init__(self, bus: SignalBus, topic: _Topic, sub_id: int, queue: asyncio.Queue):
        self.bus = bus
        self.topic = topic
        self.sub_id = sub_id
        self.queue = queue
        self._closed = False

    async def close(self):
        if not self._closed:
            await self.bus.unsubscribe(self.topic, self.sub_id)
            self._closed = True

    def __aiter__(self) -> AsyncIterator[dict]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[dict]:
        try:
            while not self._closed:
                ev = await self.queue.get()
                yield ev
        finally:
            await self.close()


# Export a singleton so modules can just do:
#   from modules.signal_bus import bus
bus = SignalBus()
