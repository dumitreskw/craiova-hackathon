"""In-process pub/sub for live updates (docs/05-api.md#live-updates).

Each SSE client subscribes to one investigation and gets its own queue. A slow
client never blocks a scan: when its queue is full, events for it are dropped
(the UI can re-fetch the graph).
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any, AsyncIterator


class EventBus:
    def __init__(self, queue_size: int = 1000):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._size = queue_size

    def publish(self, investigation_id: str, event: str, data: dict[str, Any]) -> None:
        for q in list(self._subs.get(investigation_id, ())):
            try:
                q.put_nowait((event, data))
            except asyncio.QueueFull:
                pass

    async def subscribe(self, investigation_id: str) -> AsyncIterator[tuple[str, dict]]:
        q: asyncio.Queue = asyncio.Queue(self._size)
        self._subs[investigation_id].add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subs[investigation_id].discard(q)


def sse_format(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
