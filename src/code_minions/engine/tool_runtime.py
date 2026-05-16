"""Metadata-aware tool runtime primitives."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    read_only: bool = False
    concurrency_safe: bool = False
    mutates_paths: bool = False
    timeout_seconds: int = 60
    result_budget_chars: int = 8000


@dataclass(frozen=True)
class ToolRuntimeResult:
    call_id: str | None
    name: str
    content: str
    truncated: bool = False


LOCAL_TOOL_SPECS: dict[str, ToolSpec] = {
    "Read": ToolSpec("Read", read_only=True, concurrency_safe=True, result_budget_chars=12000),
    "Glob": ToolSpec("Glob", read_only=True, concurrency_safe=True, result_budget_chars=12000),
    "Write": ToolSpec("Write", mutates_paths=True),
    "Edit": ToolSpec("Edit", mutates_paths=True),
    "Delete": ToolSpec("Delete", mutates_paths=True),
    "Bash": ToolSpec("Bash", mutates_paths=True, timeout_seconds=300),
    "Command": ToolSpec("Command", mutates_paths=True, timeout_seconds=300),
}


def tool_spec_for(name: str) -> ToolSpec:
    return LOCAL_TOOL_SPECS.get(name, ToolSpec(name=name))


def budget_tool_result(content: str, *, budget_chars: int) -> tuple[str, bool]:
    if len(content) <= budget_chars:
        return content, False
    head = max(0, budget_chars // 2)
    tail = max(0, budget_chars - head - 120)
    compacted = (
        content[:head]
        + f"\n\n[tool output truncated: {len(content) - head - tail} chars omitted]\n\n"
        + content[-tail:]
    )
    return compacted, True


def run_tool_calls(
    calls: list[Any],
    *,
    run_one,
    specs: dict[str, ToolSpec] | None = None,
) -> list[ToolRuntimeResult]:
    """Run calls concurrently only when every call is explicitly safe."""
    spec_map = specs or LOCAL_TOOL_SPECS

    def _execute(call: Any) -> ToolRuntimeResult:
        name = getattr(call, "name", "")
        spec = spec_map.get(name, tool_spec_for(name))
        raw = run_one(call)
        content, truncated = budget_tool_result(str(raw), budget_chars=spec.result_budget_chars)
        return ToolRuntimeResult(
            call_id=getattr(call, "id", None),
            name=name,
            content=content,
            truncated=truncated,
        )

    if len(calls) > 1 and all(spec_map.get(getattr(call, "name", ""), tool_spec_for(getattr(call, "name", ""))).concurrency_safe for call in calls):
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return list(pool.map(_execute, calls))
    return [_execute(call) for call in calls]
