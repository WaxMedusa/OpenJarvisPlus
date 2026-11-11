"""Thread-safe event fan-out from the audio thread to API/WebSocket clients."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque


class Hub:
    """Keeps a short history and pushes each event to subscribed asyncio queues."""

    def __init__(self, history: int = 400) -> None:
        self._events: deque[dict] = deque(maxlen=history)
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._seq = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, event: dict) -> None:
        """Safe from any thread once :meth:`bind_loop` has run."""
        with self._lock:
            self._seq += 1
            payload = {"seq": self._seq, "ts": time.time(), **event}
            self._events.append(payload)
            subs = list(self._subs)
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        for queue in subs:
            try:
                loop.call_soon_threadsafe(self._offer, queue, payload)
            except RuntimeError:  # loop shut down mid-publish
                return

    @staticmethod
    def _offer(queue: asyncio.Queue, payload: dict) -> None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._subs.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(queue)

    def history(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._events)[-limit:]
