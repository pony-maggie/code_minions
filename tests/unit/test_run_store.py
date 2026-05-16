"""Tests for Run Store (SQLite persistence)."""
from __future__ import annotations

import json
from pathlib import Path

from code_minions.store.run_store import RunStore
from code_minions.types import RunStatus, StepStatus


def test_create_and_get_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    run_id = store.create_run(workflow="hello", inputs={"greeting": "hi"}, llm="openai/gpt-5.5")
    assert run_id.startswith("r_")

    run = store.get_run(run_id)
    assert run is not None
    assert run["workflow"] == "hello"
    assert run["status"] == RunStatus.PENDING.value
    assert run["llm"] == "openai/gpt-5.5"
    assert json.loads(run["input_json"]) == {"greeting": "hi"}


def test_update_run_status(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    run_id = store.create_run(workflow="w", inputs={})

    store.set_run_status(run_id, RunStatus.RUNNING)
    assert store.get_run(run_id)["status"] == RunStatus.RUNNING.value

    store.set_run_status(run_id, RunStatus.SUCCESS)
    run = store.get_run(run_id)
    assert run["status"] == RunStatus.SUCCESS.value
    assert run["ended_at"] is not None

    store.set_run_status(run_id, RunStatus.RUNNING)
    run = store.get_run(run_id)
    assert run["status"] == RunStatus.RUNNING.value
    assert run["ended_at"] is None


def test_create_and_update_step(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    run_id = store.create_run(workflow="w", inputs={})

    store.upsert_step(run_id, step_id="s1", status=StepStatus.RUNNING)
    steps = store.list_steps(run_id)
    assert len(steps) == 1
    assert steps[0]["status"] == StepStatus.RUNNING.value

    store.upsert_step(
        run_id, step_id="s1", status=StepStatus.SUCCESS, output={"ok": True}
    )
    steps = store.list_steps(run_id)
    assert steps[0]["status"] == StepStatus.SUCCESS.value
    assert json.loads(steps[0]["output_json"]) == {"ok": True}


def test_running_step_update_clears_prior_terminal_fields(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    run_id = store.create_run(workflow="w", inputs={})

    store.upsert_step(run_id, "s1", StepStatus.FAILED, error="old failure")
    failed = store.list_steps(run_id)[0]
    assert failed["ended_at"] is not None
    assert failed["error"] == "old failure"

    store.upsert_step(run_id, "s1", StepStatus.RUNNING)

    running = store.list_steps(run_id)[0]
    assert running["status"] == StepStatus.RUNNING.value
    assert running["ended_at"] is None
    assert running["error"] is None


def test_step_detail_is_persisted(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    run_id = store.create_run(workflow="w", inputs={})

    store.upsert_step(
        run_id,
        step_id="implement[0]",
        status=StepStatus.RUNNING,
        detail="T1: Add history search",
    )

    steps = store.list_steps(run_id)
    assert steps[0]["detail"] == "T1: Add history search"


def test_run_events_are_append_only_and_ordered(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    run_id = store.create_run(workflow="w", inputs={})

    store.append_run_event(run_id, "llm_call", {"model": "fake", "stop_reason": "tool_use"})
    store.append_run_event(run_id, "tool_call", {"tool": "Read", "status": "success"})

    events = store.list_run_events(run_id)

    assert [e["event_type"] for e in events] == ["llm_call", "tool_call"]
    assert events[0]["payload"] == {"model": "fake", "stop_reason": "tool_use"}
    assert events[1]["payload"] == {"tool": "Read", "status": "success"}
    assert events[0]["created_at"] <= events[1]["created_at"]


def test_successful_outputs_include_for_each_iteration_steps(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    run_id = store.create_run(workflow="w", inputs={})
    store.upsert_step(run_id, "up", StepStatus.SUCCESS, output={"items": ["a", "b"]})
    store.upsert_step(run_id, "each[0]", StepStatus.SUCCESS, output={"echo": "a"})
    store.upsert_step(run_id, "each[1]", StepStatus.FAILED, output={"echo": "b"})

    outputs = store.get_successful_outputs(run_id)

    assert outputs == {
        "up": {"items": ["a", "b"]},
        "each[0]": {"echo": "a"},
    }


def test_list_runs_ordered_newest_first(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    first = store.create_run(workflow="a", inputs={})
    second = store.create_run(workflow="b", inputs={})
    runs = store.list_runs(limit=10)
    ids = [r["id"] for r in runs]
    assert ids == [second, first]


def test_get_missing_run_returns_none(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.db")
    assert store.get_run("r_nonexistent") is None
