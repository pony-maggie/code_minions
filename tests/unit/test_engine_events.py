"""Tests verifying Engine publishes step.status + run.finished events."""
from __future__ import annotations

from pathlib import Path

from code_minions.engine.engine import Engine
from code_minions.engine.event_bus import Event, EventBus
from code_minions.engine.skill_runtime import SkillRuntime


def _seed_skill(root: Path, name: str, entrypoint: str = "def run(ctx): return {}\n") -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\nentrypoint-script: scripts/run.py\ninputs: {{}}\noutputs: {{}}\n---\n\n# {name}\n"
    )
    (d / "scripts").mkdir()
    (d / "scripts" / "run.py").write_text(entrypoint)


def test_engine_publishes_events_with_bus(tmp_git_repo: Path) -> None:
    skills_root = tmp_git_repo / "skills"
    _seed_skill(skills_root, "a")
    wf_root = tmp_git_repo / "workflows"
    wf_root.mkdir()
    (wf_root / "w.yaml").write_text("name: w\nsteps:\n  - id: s\n    skill: a\n")

    bus = EventBus()
    captured: list[Event] = []
    bus.subscribe(captured.append)

    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[wf_root],
        runtime=SkillRuntime(),
        event_bus=bus,
    )
    run_id = engine.start_run(workflow="w", inputs={})

    kinds = [e.kind for e in captured]
    assert "run.started" in kinds
    assert "run.finished" in kinds
    step_events = [e for e in captured if e.kind == "step.status"]
    assert any(e.payload["status"] == "running" for e in step_events)
    assert any(e.payload["status"] == "success" for e in step_events)
    assert all(e.run_id == run_id for e in captured)
    assert all(e.ts.tzinfo is not None for e in captured)


def test_engine_without_bus_unchanged(tmp_git_repo: Path) -> None:
    """Backward compat: Engine created without event_bus still works."""
    skills_root = tmp_git_repo / "skills"
    _seed_skill(skills_root, "a")
    wf_root = tmp_git_repo / "workflows"
    wf_root.mkdir()
    (wf_root / "w.yaml").write_text("name: w\nsteps:\n  - id: s\n    skill: a\n")

    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[wf_root],
        runtime=SkillRuntime(),
    )
    run_id = engine.start_run(workflow="w", inputs={})
    state = engine.get_run_state(run_id)
    assert state["status"] == "success"
