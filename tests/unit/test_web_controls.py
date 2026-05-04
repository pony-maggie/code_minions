"""Tests for POST /runs/<id>/cancel and /resume."""
from __future__ import annotations

from pathlib import Path

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


def test_cancel_marks_run_cancelled(client: TestClient) -> None:
    from code_minions.types import RunStatus
    from code_minions.web.deps import get_store
    store = get_store()
    run_id = store.create_run(workflow="demo", inputs={})
    store.set_run_status(run_id, RunStatus.RUNNING)

    resp = client.post(f"/runs/{run_id}/cancel", follow_redirects=False)
    assert resp.status_code in (200, 303)
    assert store.get_run(run_id)["status"] == "cancelled"


def test_cancel_404_on_unknown(client: TestClient) -> None:
    resp = client.post("/runs/r_nope/cancel", follow_redirects=False)
    assert resp.status_code == 404


def test_resume_calls_engine_resume_run(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from code_minions.types import RunStatus
    from code_minions.web.deps import get_engine, get_store
    store = get_store()
    run_id = store.create_run(workflow="demo", inputs={})
    store.set_run_status(run_id, RunStatus.FAILED)

    engine = get_engine()
    called = {"run_id": None}
    monkeypatch.setattr(engine, "resume_run", lambda rid: called.update({"run_id": rid}) or rid)

    resp = client.post(f"/runs/{run_id}/resume", follow_redirects=False)
    assert resp.status_code in (200, 303)
    assert called["run_id"] == run_id
