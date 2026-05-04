"""Tests for /new (start run) routes."""
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


def test_new_page_lists_workflows(client: TestClient) -> None:
    resp = client.get("/new")
    assert resp.status_code == 200
    # Built-in workflows should always be listed
    assert "hello-world" in resp.text
    assert "prd-to-commit" in resp.text
    assert "prd-to-pr" in resp.text


def test_new_inputs_fragment_for_hello_world(client: TestClient) -> None:
    resp = client.get("/new/inputs", params={"workflow": "hello-world"})
    assert resp.status_code == 200
    # hello-world requires `name`
    assert "name" in resp.text.lower()
    assert 'required' in resp.text.lower()


def test_new_inputs_fragment_offers_project_files_for_prd_input(
    client: TestClient,
    tmp_git_repo: Path,
) -> None:
    (tmp_git_repo / "docs").mkdir()
    (tmp_git_repo / "docs" / "calc-prd.md").write_text("# Calc PRD\n")

    resp = client.get("/new/inputs", params={"workflow": "prd-to-commit"})

    assert resp.status_code == 200
    assert 'name="prd"' in resp.text
    assert 'list="file-options-prd"' in resp.text
    assert '<datalist id="file-options-prd">' in resp.text
    assert 'value="docs/calc-prd.md"' in resp.text


def test_local_file_options_avoids_unbounded_recursive_glob(
    client: TestClient,
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_git_repo / "prd2.md").write_text("# PRD\n")

    def fail_rglob(_path: Path, _pattern: str):
        raise AssertionError("file option discovery must prune ignored directories before descending")

    monkeypatch.setattr(Path, "rglob", fail_rglob)

    from code_minions.web.routes import start

    assert "prd2.md" in start._local_file_options()


def test_new_inputs_unknown_workflow(client: TestClient) -> None:
    resp = client.get("/new/inputs", params={"workflow": "nope"})
    assert resp.status_code == 404


def test_post_new_starts_run(client: TestClient) -> None:
    # POST /new with workflow=hello-world, name=foo
    resp = client.post(
        "/new",
        data={"workflow": "hello-world", "name": "foo"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("/runs/r_")
    run_id = location.split("/")[-1]
    from code_minions.web.deps import get_store
    assert get_store().get_run(run_id) is not None


def test_post_new_records_project_llm_display(
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

    resp = client.post(
        "/new",
        data={"workflow": "hello-world", "name": "foo"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    run_id = resp.headers["location"].split("/")[-1]

    from code_minions.web.deps import get_store

    run = get_store().get_run(run_id)
    assert run is not None
    assert run["llm"] == "minimax/MiniMax-M2.7"
