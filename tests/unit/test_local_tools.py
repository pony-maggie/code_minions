from __future__ import annotations

from pathlib import Path

import pytest

from code_minions.engine.local_tools import run_local_tool


def test_read_tool_reads_file(tmp_path: Path) -> None:
    f = tmp_path / "note.txt"
    f.write_text("hello")

    out = run_local_tool("Read", {"path": "note.txt"}, workdir=tmp_path)

    assert out == "hello"


def test_read_tool_truncates_large_file(tmp_path: Path) -> None:
    f = tmp_path / "large.txt"
    f.write_text("x" * 13_000)

    out = run_local_tool("Read", {"path": "large.txt"}, workdir=tmp_path)

    assert len(out) == 12_000


def test_read_tool_lists_directory(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("export default function App() {}\n")
    (tmp_path / "src" / "components").mkdir()

    out = run_local_tool("Read", {"path": "src"}, workdir=tmp_path)

    assert "directory src" in out
    assert "App.tsx" in out
    assert "components/" in out


def test_read_tool_accepts_file_path_alias(tmp_path: Path) -> None:
    f = tmp_path / "note.txt"
    f.write_text("hello")

    out = run_local_tool("Read", {"file_path": "note.txt"}, workdir=tmp_path)

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


def test_edit_tool_accepts_common_argument_aliases(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello world")

    out = run_local_tool(
        "Edit",
        {"path": "a.txt", "old_string": "world", "new_string": "team"},
        workdir=tmp_path,
    )

    assert "updated" in out.lower()
    assert f.read_text() == "hello team"


def test_edit_tool_accepts_camel_case_argument_aliases(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello world")

    out = run_local_tool(
        "Edit",
        {"filePath": "a.txt", "oldString": "world", "newString": "team"},
        workdir=tmp_path,
    )

    assert "updated" in out.lower()
    assert f.read_text() == "hello team"


def test_edit_tool_accepts_lowercase_string_argument_aliases(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello world")

    out = run_local_tool(
        "Edit",
        {"file_path": "a.txt", "oldstring": "world", "newstring": "team"},
        workdir=tmp_path,
    )

    assert "updated" in out.lower()
    assert f.read_text() == "hello team"


def test_edit_tool_accepts_content_and_replacement_aliases(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello world")

    out = run_local_tool(
        "Edit",
        {"path": "a.txt", "oldContent": "world", "newContent": "team"},
        workdir=tmp_path,
    )

    assert "updated" in out.lower()
    assert f.read_text() == "hello team"

    out = run_local_tool(
        "Edit",
        {"path": "a.txt", "target": "team", "replacement": "crew"},
        workdir=tmp_path,
    )

    assert "updated" in out.lower()
    assert f.read_text() == "hello crew"


def test_glob_tool_lists_matching_files(tmp_path: Path) -> None:
    (tmp_path / "src" / "components").mkdir(parents=True)
    (tmp_path / "src" / "components" / "App.tsx").write_text("export function App() {}\n")
    (tmp_path / "src" / "types.ts").write_text("export type X = string\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.ts").write_text("ignored\n")

    out = run_local_tool("Glob", {"pattern": "src/**/*.ts*"}, workdir=tmp_path)

    assert "src/components/App.tsx" in out
    assert "src/types.ts" in out
    assert "node_modules" not in out


def test_edit_tool_reports_missing_text_arguments(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello world")

    with pytest.raises(ValueError, match="old_text and new_text are required"):
        run_local_tool("Edit", {"path": "a.txt", "content": "hello team"}, workdir=tmp_path)


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


def test_command_tool_alias_runs_shell_command(tmp_path: Path) -> None:
    out = run_local_tool("Command", {"command": "pwd"}, workdir=tmp_path)

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
