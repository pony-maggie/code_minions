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
    assert events == [{
        "event_type": "tool_call",
        "payload": {
            "tool": "Read",
            "call_id": "call-1",
            "status": "success",
            "read_only": True,
        },
    }]


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
    assert events == [{
        "event_type": "tool_call",
        "payload": {
            "tool": "Write",
            "call_id": "call-2",
            "status": "error",
            "read_only": False,
            "error": "local tool Write is not allowed in project-readonly workspace",
        },
    }]
