"""Background work with progress events.

Downloads and installs take minutes; the voice loop cannot block on them or
Jarvis becomes deaf while Steam updates. A task is a function that receives
``report(message)`` and ``should_stop()`` and returns a final summary string
(or ``None`` on cancellation). Progress and completion are surfaced as
:class:`TaskEvent` objects drained by the wake loop, which decides what to say
out loud.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass(frozen=True)
class TaskEvent:
    kind: str  # "started" | "progress" | "done" | "error" | "cancelled"
    name: str
    message: str = ""

    @property
    def finished(self) -> bool:
        return self.kind in {"done", "error", "cancelled"}


class BackgroundTasks:
    """Run tasks on daemon threads and publish their lifecycle."""

    def __init__(self) -> None:
        self._events: "queue.Queue[TaskEvent]" = queue.Queue()
        self._cancel = threading.Event()
        self._threads: List[threading.Thread] = []
        self._lock = threading.Lock()

    # -- public API --------------------------------------------------------
    def start(
        self,
        name: str,
        target: Callable[[Callable[[str], None], Callable[[], bool]], Optional[str]],
    ) -> bool:
        """Start *target*. Returns False when the same task is already running."""
        with self._lock:
            if any(t.name == f"pc-task:{name}" and t.is_alive() for t in self._threads):
                return False
            self._cancel.clear()
            thread = threading.Thread(
                target=self._run,
                args=(name, target),
                name=f"pc-task:{name}",
                daemon=True,
            )
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(thread)
            thread.start()
        return True

    def drain(self, limit: int = 20) -> List[TaskEvent]:
        """Pop up to *limit* pending events without blocking."""
        events: List[TaskEvent] = []
        for _ in range(limit):
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return events

    def cancel_all(self) -> None:
        self._cancel.set()

    def shutdown(self) -> None:
        self._cancel.set()

    # -- internals ---------------------------------------------------------
    def _run(self, name: str, target: Callable[..., Optional[str]]) -> None:
        self._events.put(TaskEvent("started", name))
        try:
            summary = target(
                lambda message: self._events.put(TaskEvent("progress", name, message)),
                self._cancel.is_set,
            )
        except Exception as exc:  # reported, never raised into the task thread
            self._events.put(TaskEvent("error", name, f"{type(exc).__name__}: {exc}"))
            return
        if summary is None or self._cancel.is_set():
            self._events.put(TaskEvent("cancelled", name, "Stopped."))
            return
        self._events.put(TaskEvent("done", name, summary))


__all__ = ["BackgroundTasks", "TaskEvent"]
