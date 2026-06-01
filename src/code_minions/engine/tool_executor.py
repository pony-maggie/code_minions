"""Shared tool execution pipeline for LLM-driven skills."""
from __future__ import annotations

import fnmatch
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_minions.engine.observability import (
    TOOL_CALL_FAILED,
    TOOL_CALL_FINISHED,
    TOOL_CALL_STARTED,
    emit_run_event,
    monotonic_ms,
)
from code_minions.engine.tool_runtime import tool_spec_for

ToolEventRecorder = Callable[[str, dict[str, Any]], None]


LOCAL_TOOL_READ_ONLY = {
    "Read": True,
    "Glob": True,
    "Write": False,
    "Edit": False,
    "Delete": False,
    "Bash": False,
    "Command": False,
}


@dataclass
class ToolExecutionContext:
    workdir: Path
    workspace_mode: str = "git-worktree"
    event_recorder: ToolEventRecorder | None = None
    step_id: str | None = None
    tool_capabilities: dict[str, Any] = field(default_factory=dict)


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
        spec = tool_spec_for(name)
        started = monotonic_ms()
        base_payload = {
            "tool": name,
            "call_id": call_id,
            "read_only": read_only,
            "timeout_seconds": spec.timeout_seconds,
        }
        self._record(TOOL_CALL_STARTED, base_payload)
        try:
            if self._ctx.workspace_mode == "project-readonly" and not read_only:
                raise RuntimeError(
                    f"local tool {name} is not allowed in project-readonly workspace"
                )
            self._enforce_tool_capabilities(name, arguments)
            from code_minions.engine.local_tools import run_local_tool_with_evidence
            result = run_local_tool_with_evidence(name, arguments, self._ctx.workdir)
        except Exception as e:
            duration_ms = monotonic_ms() - started
            evidence = _error_evidence(e)
            self._record(
                TOOL_CALL_FAILED,
                {
                    **base_payload,
                    "duration_ms": duration_ms,
                    "error": str(e),
                    "evidence": evidence,
                },
            )
            self._record(
                "tool_call",
                {
                    "tool": name,
                    "call_id": call_id,
                    "status": "error",
                    "read_only": read_only,
                    "error": str(e),
                    "evidence": evidence,
                },
            )
            return f"[error] {e}"
        duration_ms = monotonic_ms() - started
        self._record(
            TOOL_CALL_FINISHED,
            {
                **base_payload,
                "duration_ms": duration_ms,
                "result_chars": len(result.content),
                "evidence": result.evidence,
            },
        )
        self._record(
            "tool_call",
            {
                "tool": name,
                "call_id": call_id,
                "status": "success",
                "read_only": read_only,
                "evidence": result.evidence,
            },
        )
        return result.content

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
        spec = tool_spec_for(name)
        started = monotonic_ms()
        base_payload = {
            "tool": name,
            "call_id": call_id,
            "read_only": False,
            "timeout_seconds": spec.timeout_seconds,
        }
        self._record(TOOL_CALL_STARTED, base_payload)
        try:
            result = mcp_pool.call_tool(server, tool, arguments)
        except Exception as e:
            duration_ms = monotonic_ms() - started
            self._record(
                TOOL_CALL_FAILED,
                {
                    **base_payload,
                    "duration_ms": duration_ms,
                    "error": str(e),
                },
            )
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
        duration_ms = monotonic_ms() - started
        self._record(
            TOOL_CALL_FINISHED,
            {
                **base_payload,
                "duration_ms": duration_ms,
                "result_chars": len(result),
            },
        )
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
        emit_run_event(self._ctx.event_recorder, event_type, payload)

    def _enforce_tool_capabilities(self, name: str, arguments: dict[str, Any]) -> None:
        caps = self._ctx.tool_capabilities.get(name) or {}
        if not isinstance(caps, dict):
            raise ValueError(f"tool_capabilities for {name} must be a mapping")
        if name in {"Bash", "Command"}:
            allowlist = _string_list(caps.get("command_allowlist"))
            if allowlist and not _command_allowed(arguments, allowlist):
                raise ValueError(
                    f"local tool {name} command is not in command_allowlist"
                )
        if name in {"Write", "Edit", "Delete"}:
            allowlist = _string_list(caps.get("path_allowlist"))
            if allowlist and not _path_allowed(_tool_path_argument(arguments), allowlist):
                raise ValueError(f"local tool {name} path is not in path_allowlist")


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


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("tool capability allowlist values must be lists of strings")
    return [item for item in value if item]


def _first_present(arguments: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in arguments:
            return arguments[name]
    return None


def _tool_path_argument(arguments: dict[str, Any]) -> str:
    path = _first_present(arguments, ("path", "file_path", "filePath", "filepath", "pathname"))
    if not path:
        raise ValueError("path is required for path_allowlist enforcement")
    return str(path)


def _path_allowed(path: str, allowlist: list[str]) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    positive = [pattern for pattern in allowlist if not pattern.startswith("!")]
    negative = [pattern[1:] for pattern in allowlist if pattern.startswith("!")]
    if any(fnmatch.fnmatchcase(normalized, pattern) for pattern in negative):
        return False
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in positive)


def _command_allowed(arguments: dict[str, Any], allowlist: list[str]) -> bool:
    command = str(arguments.get("command") or arguments.get("cmd") or "")
    if not command:
        raise ValueError("command is required for command_allowlist enforcement")
    command_tokens = _split_command(command)
    for allowed in allowlist:
        allowed_tokens = _split_command(allowed)
        if allowed_tokens and command_tokens[: len(allowed_tokens)] == allowed_tokens:
            return True
        if not allowed_tokens and command.strip().startswith(allowed):
            return True
    return False


def _split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _error_evidence(error: Exception) -> dict[str, Any]:
    message = str(error)
    kind = "policy_rejection" if "not allowed" in message or "allowlist" in message else "tool_error"
    return {
        "kind": kind,
        "result_chars": 0,
        "result_truncated": False,
    }
