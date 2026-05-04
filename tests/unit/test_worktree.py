"""Tests for WorktreeManager."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from code_minions.git.worktree import WorktreeError, WorktreeManager


def test_create_worktree(tmp_git_repo: Path) -> None:
    mgr = WorktreeManager(repo_path=tmp_git_repo)
    wt_path = tmp_git_repo / ".devflow" / "runs" / "r_1" / "worktree"

    info = mgr.create(worktree_path=wt_path, branch="code-minions/r_1")

    assert info.path == wt_path
    assert info.branch == "code-minions/r_1"
    assert wt_path.exists()
    assert (wt_path / "README.md").exists()   # base branch content copied

    branches = subprocess.check_output(
        ["git", "branch", "--list"], cwd=tmp_git_repo, text=True
    )
    assert "code-minions/r_1" in branches


def test_remove_worktree(tmp_git_repo: Path) -> None:
    mgr = WorktreeManager(repo_path=tmp_git_repo)
    wt_path = tmp_git_repo / ".devflow" / "runs" / "r_2" / "worktree"
    mgr.create(worktree_path=wt_path, branch="code-minions/r_2")

    mgr.remove(worktree_path=wt_path, delete_branch=True)

    assert not wt_path.exists()
    branches = subprocess.check_output(
        ["git", "branch", "--list"], cwd=tmp_git_repo, text=True
    )
    assert "code-minions/r_2" not in branches


def test_create_fails_when_path_exists(tmp_git_repo: Path) -> None:
    mgr = WorktreeManager(repo_path=tmp_git_repo)
    wt_path = tmp_git_repo / ".devflow" / "runs" / "r_3" / "worktree"
    wt_path.mkdir(parents=True)

    with pytest.raises(WorktreeError, match="already exists"):
        mgr.create(worktree_path=wt_path, branch="code-minions/r_3")


def test_create_fails_outside_git_repo(tmp_path: Path) -> None:
    mgr = WorktreeManager(repo_path=tmp_path)
    with pytest.raises(WorktreeError, match="not a git repo"):
        mgr.create(worktree_path=tmp_path / "wt", branch="code-minions/x")
