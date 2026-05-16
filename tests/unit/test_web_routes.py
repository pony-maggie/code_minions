"""Unit tests for web routes (TestClient-based, no uvicorn)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A TestClient rooted at a fresh tmp git repo (so RunStore writes there)."""
    # web.deps caches project_root at cwd; monkeypatch to tmp_git_repo
    monkeypatch.chdir(tmp_git_repo)

    # Clear any cached singletons from a previous test
    from code_minions.web import deps as web_deps
    web_deps._project_root.cache_clear()
    web_deps.get_engine.cache_clear()
    web_deps.get_store.cache_clear()

    from code_minions.web.app import create_app
    app = create_app()
    return TestClient(app)


def test_runs_list_empty(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No runs yet" in resp.text
    assert "code-minions" in resp.text


def test_web_auth_token_rejects_missing_token(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_git_repo)
    monkeypatch.setenv("CODE_MINIONS_WEB_AUTH_TOKEN", "secret")
    from code_minions.web import deps as web_deps

    web_deps._project_root.cache_clear()
    web_deps.get_engine.cache_clear()
    web_deps.get_store.cache_clear()

    from code_minions.web.app import create_app

    authed_client = TestClient(create_app())

    resp = authed_client.get("/")

    assert resp.status_code == 401


def test_web_auth_token_accepts_bearer_token(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_git_repo)
    monkeypatch.setenv("CODE_MINIONS_WEB_AUTH_TOKEN", "secret")
    from code_minions.web import deps as web_deps

    web_deps._project_root.cache_clear()
    web_deps.get_engine.cache_clear()
    web_deps.get_store.cache_clear()

    from code_minions.web.app import create_app

    authed_client = TestClient(create_app())

    resp = authed_client.get("/", headers={"Authorization": "Bearer secret"})

    assert resp.status_code == 200
    assert "code-minions" in resp.text


def test_runs_list_shows_existing_runs(client: TestClient, tmp_git_repo: Path) -> None:
    # Seed a run directly into the store
    from code_minions.web.deps import get_store
    store = get_store()
    run_id = store.create_run(workflow="demo-workflow", inputs={"x": 1})

    resp = client.get("/")
    assert resp.status_code == 200
    assert run_id in resp.text
    assert "demo-workflow" in resp.text
    assert "pending" in resp.text.lower()


def test_run_detail_not_found(client: TestClient) -> None:
    resp = client.get("/runs/r_nonexistent")
    assert resp.status_code == 404


def test_run_detail_shows_steps(client: TestClient) -> None:
    from code_minions.types import StepStatus
    from code_minions.web.deps import get_store
    store = get_store()
    run_id = store.create_run(workflow="demo", inputs={})
    store.upsert_step(run_id, "stepA", StepStatus.SUCCESS, output={"result": "ok"})
    store.upsert_step(run_id, "stepB", StepStatus.FAILED, error="boom")

    resp = client.get(f"/runs/{run_id}")
    assert resp.status_code == 200
    assert "stepA" in resp.text
    assert "stepB" in resp.text
    assert "success" in resp.text.lower()
    assert "failed" in resp.text.lower()
    assert "boom" in resp.text
    assert "Runtime Activity" in resp.text


def test_run_detail_shows_gate_findings(client: TestClient) -> None:
    from code_minions.types import RunStatus, StepStatus
    from code_minions.web.deps import get_store

    store = get_store()
    run_id = store.create_run("react-vite-prd-to-commit", {}, llm="minimax/MiniMax-M2.7")
    store.upsert_step(
        run_id,
        "implement[0]",
        StepStatus.FAILED,
        output={
            "agent_profile": {"profile_id": "react-vite/implementer"},
            "gate_findings": [
                {
                    "code": "missing-postcss-plugin-dependency",
                    "severity": "error",
                    "stage": "preflight",
                    "message": "PostCSS config references tailwindcss.",
                    "repair_hint": "Add tailwindcss or remove PostCSS config.",
                    "source": "react-vite",
                    "paths": ["postcss.config.js"],
                }
            ],
        },
        error="tests never green",
    )
    store.set_run_status(run_id, RunStatus.FAILED)

    resp = client.get(f"/runs/{run_id}")

    assert resp.status_code == 200
    assert "Findings" in resp.text
    assert "react-vite/implementer" in resp.text
    assert "missing-postcss-plugin-dependency" in resp.text
    assert "Add tailwindcss or remove PostCSS config." in resp.text


def test_web_engine_loads_llm_from_project_devflow(
    client: TestClient,
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_git_repo / "devflow.yaml").write_text(
        """
version: 1
llm:
  default: minimax
  providers:
    minimax:
      model: MiniMax-M2.7
      api_key_env: MINIMAX_API_KEY
workflow:
  default: hello-world
  search_paths: []
skills:
  search_paths: []
""".lstrip()
    )
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")

    from code_minions.web import deps

    deps.get_engine.cache_clear()

    assert deps.get_engine().llm_display == "minimax/MiniMax-M2.7"
