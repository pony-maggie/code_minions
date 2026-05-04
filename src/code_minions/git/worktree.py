"""Thin wrapper around `git worktree` to create isolated working copies per run."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorktreeError(Exception):
    """Raised for any worktree-related failure."""


@dataclass(frozen=True)
class WorktreeInfo:
    path: Path
    branch: str


class WorktreeManager:
    """Create and remove git worktrees for isolated run workspaces."""

    def __init__(self, repo_path: Path):
        self._repo = Path(repo_path)
        if not (self._repo / ".git").exists():
            # Defer check until an operation is actually called to keep init cheap.
            pass

    def _ensure_git_repo(self) -> None:
        if not (self._repo / ".git").exists():
            raise WorktreeError(f"not a git repo: {self._repo}")

    def create(self, worktree_path: Path, branch: str, base: str = "HEAD") -> WorktreeInfo:
        self._ensure_git_repo()
        if worktree_path.exists():
            raise WorktreeError(f"worktree path already exists: {worktree_path}")
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(worktree_path), base],
            cwd=self._repo,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise WorktreeError(
                f"git worktree add failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        return WorktreeInfo(path=worktree_path, branch=branch)

    def remove(self, worktree_path: Path, delete_branch: bool = False) -> None:
        self._ensure_git_repo()
        branch: str | None = None
        if delete_branch:
            branch = self._branch_of(worktree_path)

        result = subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=self._repo,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)

        if delete_branch and branch:
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=self._repo,
                capture_output=True,
                text=True,
            )

    def _branch_of(self, worktree_path: Path) -> str | None:
        out = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self._repo,
            capture_output=True,
            text=True,
        )
        if out.returncode != 0:
            return None
        current_path: str | None = None
        for line in out.stdout.splitlines():
            if line.startswith("worktree "):
                current_path = line.removeprefix("worktree ").strip()
            elif line.startswith("branch ") and current_path == str(worktree_path.resolve()):
                return line.removeprefix("branch refs/heads/").strip()
        return None
