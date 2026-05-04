from __future__ import annotations

from pathlib import Path

from code_minions.engine.workflow import load_workflow


def test_workflow_defaults_to_git_worktree_workspace(tmp_path: Path) -> None:
    path = tmp_path / "wf.yaml"
    path.write_text(
        """
name: legacy
steps:
  - id: s
    skill: hello
"""
    )

    wf = load_workflow(path)

    assert wf.workspace.mode == "git-worktree"


def test_workflow_loads_explicit_workspace_mode(tmp_path: Path) -> None:
    path = tmp_path / "wf.yaml"
    path.write_text(
        """
name: smoke
workspace:
  mode: none
steps:
  - id: s
    skill: hello
"""
    )

    wf = load_workflow(path)

    assert wf.workspace.mode == "none"
