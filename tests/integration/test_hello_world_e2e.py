"""End-to-end: run the built-in hello-world workflow."""
from __future__ import annotations

from pathlib import Path

from code_minions.engine.engine import Engine
from code_minions.engine.skill_runtime import SkillRuntime


def _builtin_root() -> Path:
    import code_minions
    return Path(code_minions.__file__).resolve().parent / "builtin"


def test_hello_world_full_flow(tmp_git_repo: Path) -> None:
    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[_builtin_root() / "skills"],
        workflow_search_paths=[_builtin_root() / "workflows"],
        runtime=SkillRuntime(),
    )

    run_id = engine.start_run(workflow="hello-world", inputs={"name": "world"})

    state = engine.get_run_state(run_id)
    assert state["status"] == "success"
    assert len(state["steps"]) == 1
    assert state["steps"][0]["step_id"] == "greet"
    assert state["steps"][0]["status"] == "success"

    workspace = tmp_git_repo / ".devflow" / "runs" / run_id / "workspace"
    greeting_file = workspace / "greeting.txt"
    assert greeting_file.exists()
    assert greeting_file.read_text().strip() == "hello, world!"


def test_hello_world_does_not_require_git_repo(tmp_path: Path) -> None:
    engine = Engine(
        project_root=tmp_path,
        skill_search_paths=[_builtin_root() / "skills"],
        workflow_search_paths=[_builtin_root() / "workflows"],
        runtime=SkillRuntime(),
    )

    run_id = engine.start_run(workflow="hello-world", inputs={"name": "world"})

    state = engine.get_run_state(run_id)
    assert state["status"] == "success"
    workspace = tmp_path / ".devflow" / "runs" / run_id / "workspace"
    greeting_file = workspace / "greeting.txt"
    assert greeting_file.exists()
    assert greeting_file.read_text().strip() == "hello, world!"
    assert not (tmp_path / ".devflow" / "runs" / run_id / "worktree").exists()
