from __future__ import annotations

from pathlib import Path

import pytest

from code_minions.engine.local_tools import run_local_tool


def test_read_tool_reads_file(tmp_path: Path) -> None:
    f = tmp_path / "note.txt"
    f.write_text("hello")

    out = run_local_tool("Read", {"path": "note.txt"}, workdir=tmp_path)

    assert out == "hello"


def test_write_tool_writes_file(tmp_path: Path) -> None:
    out = run_local_tool("Write", {"path": "a.txt", "content": "x"}, workdir=tmp_path)

    assert "wrote" in out.lower()
    assert (tmp_path / "a.txt").read_text() == "x"


def test_edit_tool_replaces_exact_text(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello world")

    out = run_local_tool(
        "Edit",
        {"path": "a.txt", "old_text": "world", "new_text": "team"},
        workdir=tmp_path,
    )

    assert "updated" in out.lower()
    assert f.read_text() == "hello team"


def test_delete_tool_removes_file(tmp_path: Path) -> None:
    f = tmp_path / "stale.txt"
    f.write_text("remove me")

    out = run_local_tool("Delete", {"path": "stale.txt"}, workdir=tmp_path)

    assert "deleted" in out.lower()
    assert not f.exists()


def test_bash_tool_runs_in_workdir(tmp_path: Path) -> None:
    out = run_local_tool("Bash", {"command": "pwd"}, workdir=tmp_path)

    assert "exit_code=0" in out
    assert str(tmp_path) in out


def test_read_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside workdir|relative to workdir"):
        run_local_tool("Read", {"path": "../secret.txt"}, workdir=tmp_path)


def test_write_rejects_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="relative to workdir"):
        run_local_tool("Write", {"path": str(tmp_path / "x.txt"), "content": "x"}, workdir=tmp_path)


def test_delete_rejects_directories(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()

    with pytest.raises(ValueError, match="path is not a file"):
        run_local_tool("Delete", {"path": "src"}, workdir=tmp_path)


def test_edit_requires_unique_match(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x x")

    with pytest.raises(ValueError, match="exactly one"):
        run_local_tool(
            "Edit",
            {"path": "a.txt", "old_text": "x", "new_text": "y"},
            workdir=tmp_path,
        )
