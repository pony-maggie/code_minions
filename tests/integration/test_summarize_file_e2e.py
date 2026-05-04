"""E2E: summarize-file with fake LLM + deterministic file read."""
from __future__ import annotations

import subprocess
from pathlib import Path

from code_minions.engine.engine import Engine
from code_minions.engine.skill_runtime import SkillRuntime
from code_minions.llm.types import Message, Response, Usage
from tests.unit.test_skill_runtime_llm import FakeLLM


def _builtin_root() -> Path:
    import code_minions
    return Path(code_minions.__file__).resolve().parent / "builtin"


def test_summarize_file_flow(tmp_git_repo: Path) -> None:
    # Resolve to real path to handle macOS /var -> /private/var symlink.
    repo = tmp_git_repo.resolve()

    # Seed a file inside the repo (main branch), then commit.
    (repo / "target.txt").write_text("hello world\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)

    # summarize-file reads the file itself, then asks the LLM for summary JSON.
    turn1 = Response(message=Message(role="assistant", content='{"summary": "hello file"}'),
                     usage=Usage(1, 1), model="fake", stop_reason="end_turn")
    llm = FakeLLM([turn1])

    engine = Engine(
        project_root=repo,
        skill_search_paths=[_builtin_root() / "skills"],
        workflow_search_paths=[_builtin_root() / "workflows"],
        runtime=SkillRuntime(),
        llm_backend=llm,
        mcp_pool=None,
    )
    run_id = engine.start_run(workflow="summarize-file", inputs={"file": "target.txt"})
    state = engine.get_run_state(run_id)
    assert state["status"] == "success"
    assert "hello file" in state["steps"][0]["output_json"]
    assert "12" in state["steps"][0]["output_json"]


def test_summarize_file_reads_project_root_without_git(tmp_path: Path) -> None:
    repo = tmp_path.resolve()
    (repo / "draft.txt").write_text("not committed yet\n")
    turn1 = Response(message=Message(role="assistant", content='{"summary": "draft file"}'),
                     usage=Usage(1, 1), model="fake", stop_reason="end_turn")
    llm = FakeLLM([turn1])

    engine = Engine(
        project_root=repo,
        skill_search_paths=[_builtin_root() / "skills"],
        workflow_search_paths=[_builtin_root() / "workflows"],
        runtime=SkillRuntime(),
        llm_backend=llm,
        mcp_pool=None,
    )

    run_id = engine.start_run(workflow="summarize-file", inputs={"file": "./draft.txt"})
    state = engine.get_run_state(run_id)

    assert state["status"] == "success"
    assert "draft file" in state["steps"][0]["output_json"]
    assert not (repo / ".devflow" / "runs" / run_id / "worktree").exists()
