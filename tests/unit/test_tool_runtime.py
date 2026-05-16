from __future__ import annotations

import time

from code_minions.engine.tool_runtime import (
    ToolSpec,
    budget_tool_result,
    run_tool_calls,
    tool_spec_for,
)


class Call:
    def __init__(self, name: str, call_id: str):
        self.name = name
        self.id = call_id


def test_known_local_tool_specs_mark_reads_concurrency_safe() -> None:
    assert tool_spec_for("Read").read_only is True
    assert tool_spec_for("Read").concurrency_safe is True
    assert tool_spec_for("Write").mutates_paths is True


def test_read_only_concurrency_safe_calls_can_run_together() -> None:
    calls = [Call("Read", "1"), Call("Read", "2")]
    started: list[str] = []

    def run_one(call):
        started.append(call.id)
        time.sleep(0.05)
        return f"done-{call.id}"

    start = time.monotonic()
    results = run_tool_calls(calls, run_one=run_one)
    elapsed = time.monotonic() - start

    assert [result.content for result in results] == ["done-1", "done-2"]
    assert elapsed < 0.09
    assert sorted(started) == ["1", "2"]


def test_mutating_calls_are_serialized() -> None:
    calls = [Call("Write", "1"), Call("Write", "2")]
    active = 0
    max_active = 0

    def run_one(_call):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        time.sleep(0.01)
        active -= 1
        return "ok"

    run_tool_calls(calls, run_one=run_one)

    assert max_active == 1


def test_large_tool_output_is_budgeted() -> None:
    content, truncated = budget_tool_result("a" * 100, budget_chars=30)

    assert truncated is True
    assert "truncated" in content


def test_custom_spec_result_budget_is_applied() -> None:
    result = run_tool_calls(
        [Call("Custom", "1")],
        run_one=lambda _call: "x" * 100,
        specs={"Custom": ToolSpec("Custom", read_only=True, concurrency_safe=True, result_budget_chars=25)},
    )[0]

    assert result.truncated is True
