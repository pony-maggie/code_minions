from __future__ import annotations

from pathlib import Path

from code_minions.engine.tool_executor import ToolExecutionContext, ToolExecutor


def test_tool_executor_records_local_tool_success(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello")
    events: list[dict] = []
    executor = ToolExecutor(
        ToolExecutionContext(
            workdir=tmp_path,
            workspace_mode="git-worktree",
            event_recorder=lambda event_type, payload: events.append({
                "event_type": event_type,
                "payload": payload,
            }),
        )
    )

    result = executor.run_local("Read", {"path": "note.txt"}, call_id="call-1")

    assert result == "hello"
    assert [event["event_type"] for event in events] == [
        "tool_call_started",
        "tool_call_finished",
        "tool_call",
    ]
    assert events[0]["payload"] == {
        "tool": "Read",
        "call_id": "call-1",
        "read_only": True,
        "timeout_seconds": 60,
    }
    assert events[1]["payload"]["result_chars"] == 5
    assert events[1]["payload"]["evidence"] == {
        "kind": "file_read",
        "path": "note.txt",
        "result_chars": 5,
        "result_truncated": False,
    }
    assert "duration_ms" in events[1]["payload"]
    assert events[2] == {
        "event_type": "tool_call",
        "payload": {
            "tool": "Read",
            "call_id": "call-1",
            "status": "success",
            "read_only": True,
            "evidence": {
                "kind": "file_read",
                "path": "note.txt",
                "result_chars": 5,
                "result_truncated": False,
            },
        },
    }


def test_tool_executor_rejects_mutation_in_project_readonly(tmp_path: Path) -> None:
    events: list[dict] = []
    executor = ToolExecutor(
        ToolExecutionContext(
            workdir=tmp_path,
            workspace_mode="project-readonly",
            event_recorder=lambda event_type, payload: events.append({
                "event_type": event_type,
                "payload": payload,
            }),
        )
    )

    result = executor.run_local("Write", {"path": "x.txt", "content": "no"}, call_id="call-2")

    assert result.startswith("[error]")
    assert "not allowed in project-readonly workspace" in result
    assert not (tmp_path / "x.txt").exists()
    assert [event["event_type"] for event in events] == [
        "tool_call_started",
        "tool_call_failed",
        "tool_call",
    ]
    assert events[0]["payload"] == {
        "tool": "Write",
        "call_id": "call-2",
        "read_only": False,
        "timeout_seconds": 60,
    }
    assert events[1]["payload"]["error"] == "local tool Write is not allowed in project-readonly workspace"
    assert "duration_ms" in events[1]["payload"]
    assert events[2] == {
        "event_type": "tool_call",
        "payload": {
            "tool": "Write",
            "call_id": "call-2",
            "status": "error",
            "read_only": False,
            "error": "local tool Write is not allowed in project-readonly workspace",
            "evidence": {
                "kind": "policy_rejection",
                "result_chars": 0,
                "result_truncated": False,
            },
        },
    }


def test_tool_executor_rejects_bash_outside_command_allowlist(tmp_path: Path) -> None:
    executor = ToolExecutor(
        ToolExecutionContext(
            workdir=tmp_path,
            tool_capabilities={"Bash": {"command_allowlist": ["pytest", "npm test"]}},
        )
    )

    result = executor.run_local("Bash", {"command": "curl https://example.com"})

    assert result.startswith("[error]")
    assert "not in command_allowlist" in result


def test_tool_executor_rejects_write_outside_path_allowlist(tmp_path: Path) -> None:
    executor = ToolExecutor(
        ToolExecutionContext(
            workdir=tmp_path,
            tool_capabilities={"Write": {"path_allowlist": ["src/**", "tests/**"]}},
        )
    )

    result = executor.run_local("Write", {"path": "README.md", "content": "no"})

    assert result.startswith("[error]")
    assert "not in path_allowlist" in result
    assert not (tmp_path / "README.md").exists()
