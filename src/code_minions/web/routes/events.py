"""SSE endpoint: /runs/<id>/events.

Streams run/step state changes to the browser. Subscribes to the process-wide
EventBus and filters events for the target run_id.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from code_minions.web.deps import get_store
from code_minions.web.events import get_event_bus

router = APIRouter()


@router.get("/runs/{run_id}/events")
async def run_events(request: Request, run_id: str) -> EventSourceResponse:
    store = get_store()
    if store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    loop = asyncio.get_event_loop()
    bus = get_event_bus()

    def _on_event(ev) -> None:
        if ev.run_id != run_id:
            return
        asyncio.run_coroutine_threadsafe(queue.put({
            "event": ev.kind,
            "data": json.dumps(ev.payload, default=str),
        }), loop)

    bus.subscribe(_on_event)

    async def generator():
        # Initial snapshot: current step statuses so late subscribers catch up.
        steps = store.list_steps(run_id)
        for s in steps:
            yield {
                "event": "step.status",
                "data": json.dumps({
                    "step_id": s["step_id"],
                    "status": s["status"],
                    "detail": s.get("detail"),
                    "output": None,
                    "error": s.get("error"),
                }, default=str),
            }
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield msg
                if msg["event"] == "run.finished":
                    break
            except TimeoutError:
                continue

    return EventSourceResponse(generator())
