"""Process-wide EventBus used by the web app to stream step / run state changes.

The Engine publishes to it inside start_run/resume_run; SSE route handlers
subscribe and forward events to connected browsers.
"""
from __future__ import annotations

from functools import lru_cache

from code_minions.engine.event_bus import EventBus


@lru_cache(maxsize=1)
def get_event_bus() -> EventBus:
    return EventBus()
