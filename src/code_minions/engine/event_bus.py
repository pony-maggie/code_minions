"""In-memory pub/sub. Phase C swaps in Redis/WebSocket."""
from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any


@dataclass
class Event:
    run_id: str
    kind: str            # "run.started" | "step.status" | "run.finished"
    payload: dict[str, Any]
    ts: datetime


class EventBus:
    def __init__(self) -> None:
        self._subs: list[Callable[[Event], None]] = []
        self._lock = Lock()

    def subscribe(self, fn: Callable[[Event], None]) -> None:
        with self._lock:
            self._subs.append(fn)

    def publish(self, event: Event) -> None:
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            with contextlib.suppress(Exception):
                fn(event)
