"""Tests for /runs/<id>/events SSE endpoint."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_git_repo)
    from code_minions.web import deps, events
    deps._project_root.cache_clear()
    deps.get_engine.cache_clear()
    deps.get_store.cache_clear()
    events.get_event_bus.cache_clear()

    from code_minions.web.app import create_app
    return TestClient(create_app())


def test_sse_endpoint_exists_and_streams(client: TestClient) -> None:
    from code_minions.web.deps import get_store
    store = get_store()
    run_id = store.create_run(workflow="demo", inputs={})

    # httpx TestClient/ASGITransport buffer the full response body before
    # returning, so infinite SSE generators deadlock them.  Drive the ASGI app
    # directly via raw anyio streams so we can cancel after seeing the headers.
    result: dict[str, Any] = {}

    async def _check() -> None:
        import anyio

        app = client.app

        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "path": f"/runs/{run_id}/events",
            "raw_path": f"/runs/{run_id}/events".encode(),
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
            "state": {},
        }

        response_started = anyio.Event()

        async def receive() -> dict[str, Any]:
            # Block until the response is started (headers received), then
            # signal disconnect.  The SSE generator detects disconnect and stops.
            await response_started.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                result["status_code"] = message["status"]
                result["headers"] = dict(message.get("headers", []))
                response_started.set()

        async with anyio.create_task_group() as tg:
            async def _run_app() -> None:
                await app(scope, receive, send)
                tg.cancel_scope.cancel()

            tg.start_soon(_run_app)
            # Give the app up to 3 s to start the response.
            await asyncio.sleep(0.0)  # yield control so app can start
            with anyio.move_on_after(3):
                await response_started.wait()
            tg.cancel_scope.cancel()

    asyncio.run(_check())

    assert result.get("status_code") == 200
    headers = result.get("headers", {})
    content_type = headers.get(b"content-type", b"").decode()
    assert content_type.startswith("text/event-stream")


def test_sse_404_on_unknown_run(client: TestClient) -> None:
    with client.stream("GET", "/runs/r_nope/events") as resp:
        assert resp.status_code == 404
