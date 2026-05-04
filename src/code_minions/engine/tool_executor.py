"""Shared tool execution pipeline for LLM-driven skills."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ToolEventRecorder = Callable[[str, dict[str, Any]], None]


LOCAL_TOOL_READ_ONLY = {
    "Read": True,
    "Write": False,
    "Edit": False,
    "Delete": False,
    "Bash": False,
}


@dataclass
class ToolExecutionContext:
    workdir: Path
    workspace_mode: str = "git-worktree"
    event_recorder: ToolEventRecorder | None = None
    step_id: str | None = None


class ToolExecutor:
    """Execute local tools behind one recording and policy boundary."""

    def __init__(self, ctx: ToolExecutionContext):
        self._ctx = ctx

    def run_local(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str | None = None,
    ) -> str:
        read_only = LOCAL_TOOL_READ_ONLY.get(name, False)
        try:
            if self._ctx.workspace_mode == "project-readonly" and not read_only:
                raise RuntimeError(
                    f"local tool {name} is not allowed in project-readonly workspace"
                )
            from code_minions.engine.local_tools import run_local_tool
            result = run_local_tool(name, arguments, self._ctx.workdir)
        except Exception as e:
            self._record(
                "tool_call",
                {
                    "tool": name,
                    "call_id": call_id,
                    "status": "error",
                    "read_only": read_only,
                    "error": str(e),
                },
            )
            return f"[error] {e}"
        self._record(
            "tool_call",
            {
                "tool": name,
                "call_id": call_id,
                "status": "success",
                "read_only": read_only,
            },
        )
        return result

    def run_mcp(
        self,
        mcp_pool: Any,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        call_id: str | None = None,
        wire_name: str | None = None,
    ) -> str:
        name = wire_name or f"mcp__{server}__{tool}"
        try:
            result = mcp_pool.call_tool(server, tool, arguments)
        except Exception as e:
            self._record(
                "tool_call",
                {
                    "tool": name,
                    "call_id": call_id,
                    "status": "error",
                    "read_only": False,
                    "error": str(e),
                },
            )
            return f"[error] {e}"
        self._record(
            "tool_call",
            {
                "tool": name,
                "call_id": call_id,
                "status": "success",
                "read_only": False,
            },
        )
        return result

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._ctx.step_id is not None:
            payload = {"step_id": self._ctx.step_id, **payload}
        if self._ctx.event_recorder is None:
            return
        self._ctx.event_recorder(event_type, payload)


def record_llm_call(
    event_recorder: ToolEventRecorder | None,
    *,
    step_id: str | None,
    skill: str,
    response: Any,
) -> None:
    if event_recorder is None:
        return
    message = getattr(response, "message", None)
    tool_calls = getattr(message, "tool_calls", []) if message is not None else []
    usage = getattr(response, "usage", None)
    payload: dict[str, Any] = {
        "skill": skill,
        "model": getattr(response, "model", ""),
        "stop_reason": getattr(response, "stop_reason", ""),
        "usage": {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
        },
        "tool_calls": [getattr(tc, "name", "") for tc in tool_calls],
    }
    if step_id is not None:
        payload = {"step_id": step_id, **payload}
    event_recorder("llm_call", payload)
