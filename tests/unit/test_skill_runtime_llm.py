"""Tests for SkillRuntime LLM path without an entrypoint script."""
from __future__ import annotations

from pathlib import Path

from code_minions.engine.context import ContextAssembler
from code_minions.engine.skill import load_skill
from code_minions.engine.skill_runtime import SkillContext, SkillRuntime
from code_minions.llm.types import Message, Response, ToolCall, Usage


class FakeLLM:
    name = "fake"
    def __init__(self, scripted: list[Response]):
        self._queue = list(scripted)
        self.seen_messages: list[list[Message]] = []
        self.seen_kwargs: list[dict] = []
    def supports_tool_use(self) -> bool: return True
    def chat(self, messages, tools=None, model=None, temperature=0.2, max_tokens=4096):
        self.seen_messages.append(list(messages))
        self.seen_kwargs.append({
            "tools": tools,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        return self._queue.pop(0)


def _make_skill_no_handler(tmp_path: Path):
    d = tmp_path / "summ"
    d.mkdir()
    (d / "SKILL.md").write_text(
        """---
name: summ
description: Summarize a file
allowed-tools:
  - Read
required-mcps: []
inputs:
  path: {type: string, required: true}
outputs:
  summary: {type: string}
llm:
  max_iterations: 5
  max_tokens: 12000
---

# summ

Summarize a file.
"""
    )
    return load_skill(d)


def _make_skill_with_write_tool(tmp_path: Path):
    d = tmp_path / "writer"
    d.mkdir()
    (d / "SKILL.md").write_text(
        """---
name: writer
description: Try to write a file
allowed-tools:
  - Write
required-mcps: []
inputs: {}
outputs:
  done: {type: boolean}
llm:
  max_iterations: 5
---

# writer
"""
    )
    return load_skill(d)


def _make_skill_with_write_tool_capability(tmp_path: Path):
    d = tmp_path / "writer-caps"
    d.mkdir()
    (d / "SKILL.md").write_text(
        """---
name: writer-caps
description: Try to write a file with path restrictions
allowed-tools:
  - Write
required-mcps: []
inputs: {}
outputs:
  done: {type: boolean}
llm:
  max_iterations: 5
tool-capabilities:
  Write:
    path_allowlist:
      - src/**
---

# writer-caps
"""
    )
    return load_skill(d)


def _make_task_planner_skill(tmp_path: Path):
    d = tmp_path / "planner"
    d.mkdir()
    (d / "SKILL.md").write_text(
        """---
name: plan-tasks
description: Plan tasks
allowed-tools: []
required-mcps: []
inputs: {}
outputs:
  tasks: {type: array}
llm:
  max_iterations: 3
policies:
  max_tasks: 2
---

# planner
"""
    )
    return load_skill(d)


def _make_cacheable_parse_skill(tmp_path: Path):
    d = tmp_path / "parse"
    d.mkdir()
    (d / "SKILL.md").write_text(
        """---
name: parse
description: Parse a file
allowed-tools: []
required-mcps: []
inputs:
  prd_file: {type: string, required: true}
outputs:
  goal: {type: string}
llm:
  max_iterations: 2
policies:
  cache: true
---

# parse
"""
    )
    return load_skill(d)


def _make_cacheable_plan_skill(tmp_path: Path):
    d = tmp_path / "plan"
    d.mkdir()
    (d / "SKILL.md").write_text(
        """---
name: plan
description: Plan tasks
allowed-tools: []
required-mcps: []
inputs:
  structured_prd: {type: object, required: true}
outputs:
  tasks: {type: array}
llm:
  max_iterations: 2
policies:
  cache: true
---

# plan
"""
    )
    return load_skill(d)


def _text_resp(content: str):
    return Response(
        message=Message(role="assistant", content=content),
        usage=Usage(input_tokens=1, output_tokens=1),
        model="fake", stop_reason="end_turn",
    )


def _empty_resp():
    return Response(
        message=Message(role="assistant", content=""),
        usage=Usage(input_tokens=123, output_tokens=4096),
        model="gpt-5.5", stop_reason="max_tokens",
    )


def test_llm_path_single_turn(tmp_path: Path):
    sk = _make_skill_no_handler(tmp_path)
    llm = FakeLLM([_text_resp('{"summary": "ok"}')])
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={"path": "x"}, workdir=tmp_path,
        llm=llm, assembler=ContextAssembler(tmp_path),
    )
    out = rt.invoke(sk, ctx)
    assert out == {"summary": "ok"}
    assert llm.seen_kwargs[0]["max_tokens"] == 12000


def test_llm_path_retries_when_final_message_has_no_json(tmp_path: Path):
    sk = _make_skill_no_handler(tmp_path)
    llm = FakeLLM([
        _text_resp("I can do that. The summary is ok."),
        _text_resp('{"summary": "ok"}'),
    ])
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={"path": "x"}, workdir=tmp_path,
        llm=llm, assembler=ContextAssembler(tmp_path),
    )

    out = rt.invoke(sk, ctx)

    assert out == {"summary": "ok"}
    assert len(llm.seen_messages) == 2
    assert "valid JSON object" in llm.seen_messages[-1][-1].content


def test_llm_path_parses_json_fence_before_trailing_json_like_text(tmp_path: Path):
    sk = _make_skill_no_handler(tmp_path)
    content = (
        'scratch: {"not_summary": true}\n'
        "```json\n"
        '{"summary": "ok"}\n'
        "```\n"
        'notes: {"extra": "ignored"}'
    )
    llm = FakeLLM([_text_resp(content)])
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={"path": "x"}, workdir=tmp_path,
        llm=llm, assembler=ContextAssembler(tmp_path),
    )

    out = rt.invoke(sk, ctx)

    assert out == {"summary": "ok"}
    assert len(llm.seen_messages) == 1


def test_llm_path_scans_for_object_matching_declared_outputs(tmp_path: Path):
    sk = _make_skill_no_handler(tmp_path)
    llm = FakeLLM([_text_resp('First {"draft": true}\nFinal {"summary": "ok"}')])
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={"path": "x"}, workdir=tmp_path,
        llm=llm, assembler=ContextAssembler(tmp_path),
    )

    out = rt.invoke(sk, ctx)

    assert out == {"summary": "ok"}


def test_llm_path_retries_when_tasks_exceed_max_tasks_policy(tmp_path: Path):
    sk = _make_task_planner_skill(tmp_path)
    llm = FakeLLM([
        _text_resp('{"tasks": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}'),
        _text_resp('{"tasks": [{"id": "1"}, {"id": "2"}]}'),
    ])
    rt = SkillRuntime()
    ctx = SkillContext(inputs={}, workdir=tmp_path, llm=llm, assembler=ContextAssembler(tmp_path))

    out = rt.invoke(sk, ctx)

    assert out == {
        "tasks": [
            {"id": "1", "trace_id": "cm_task_1"},
            {"id": "2", "trace_id": "cm_task_2"},
        ]
    }
    assert len(llm.seen_messages) == 2
    assert "at most 2 tasks" in llm.seen_messages[-1][-1].content


def test_plan_tasks_fails_when_large_prd_hits_max_tasks_limit(tmp_path: Path):
    import pytest

    from code_minions.engine.skill_runtime import SkillExecutionError

    sk = _make_task_planner_skill(tmp_path)
    llm = FakeLLM([_text_resp('{"tasks": [{"id": "1"}, {"id": "2"}]}')])
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={
            "structured_prd": {
                "features": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
            },
        },
        workdir=tmp_path,
        llm=llm,
        assembler=ContextAssembler(tmp_path),
    )

    with pytest.raises(SkillExecutionError) as exc:
        rt.invoke(sk, ctx)

    assert exc.value.run_status == "needs_human"
    assert exc.value.output["max_tasks_hit"] is True
    assert exc.value.output["feature_count"] == 3
    assert exc.value.output["task_count"] == 2


def test_plan_tasks_injects_stable_trace_ids(tmp_path: Path):
    sk = _make_task_planner_skill(tmp_path)
    llm = FakeLLM([
        _text_resp(
            '{"tasks": ['
            '{"id": "T1", "title": "First"}, '
            '{"id": "T2", "title": "Second", "trace_id": "custom-trace"}'
            ']}'
        )
    ])
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={
            "structured_prd": {
                "features": [{"name": "a"}, {"name": "b"}],
            },
        },
        workdir=tmp_path,
        llm=llm,
        assembler=ContextAssembler(tmp_path),
    )

    out = rt.invoke(sk, ctx)

    assert out["tasks"][0]["trace_id"] == "cm_task_1"
    assert out["tasks"][1]["trace_id"] == "custom-trace"


def test_llm_path_exceeds_max_iterations(tmp_path: Path):
    import pytest

    from code_minions.engine.skill_runtime import SkillValidationError
    sk = _make_skill_no_handler(tmp_path)
    # Always returns tool_calls but no tools are registered; the loop still counts iterations
    resp_with_tc = Response(
        message=Message(role="assistant", tool_calls=[ToolCall(id="1", name="ghost", arguments={})]),
        usage=Usage(input_tokens=1, output_tokens=1), model="fake", stop_reason="tool_use",
    )
    llm = FakeLLM([resp_with_tc] * 5)
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={"path": "x"}, workdir=tmp_path,
        llm=llm, assembler=ContextAssembler(tmp_path),
    )
    with pytest.raises(SkillValidationError, match="max_iterations") as exc_info:
        rt.invoke(sk, ctx)
    assert "tool_calls=[ghost]" in str(exc_info.value)


def test_llm_path_exceeded_iterations_reports_empty_response_diagnostics(tmp_path: Path):
    import pytest

    from code_minions.engine.skill_runtime import SkillValidationError
    sk = _make_skill_no_handler(tmp_path)
    llm = FakeLLM([_empty_resp()] * 5)
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={"path": "x"}, workdir=tmp_path,
        llm=llm, assembler=ContextAssembler(tmp_path),
    )

    with pytest.raises(SkillValidationError, match="max_iterations") as exc_info:
        rt.invoke(sk, ctx)

    error = str(exc_info.value)
    assert "content=''" in error
    assert "stop_reason=max_tokens" in error
    assert "model=gpt-5.5" in error
    assert "usage=input:123,output:4096" in error



def test_llm_path_dispatches_allowed_local_tool(tmp_path: Path):
    (tmp_path / "x.txt").write_text("hello")
    sk = _make_skill_no_handler(tmp_path)
    turn1 = Response(
        message=Message(
            role="assistant",
            tool_calls=[ToolCall(id="1", name="Read", arguments={"path": "x.txt"})],
        ),
        usage=Usage(input_tokens=1, output_tokens=1),
        model="fake",
        stop_reason="tool_use",
    )
    llm = FakeLLM([turn1, _text_resp('{"summary": "ok"}')])
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={"path": "x.txt"}, workdir=tmp_path,
        llm=llm, assembler=ContextAssembler(tmp_path),
    )

    out = rt.invoke(sk, ctx)

    assert out == {"summary": "ok"}
    tool_messages = [m for m in llm.seen_messages[-1] if m.role == "tool"]
    assert tool_messages[0].content == "hello"


def test_llm_path_records_allowed_local_tool_call(tmp_path: Path):
    (tmp_path / "x.txt").write_text("hello")
    sk = _make_skill_no_handler(tmp_path)
    turn1 = Response(
        message=Message(
            role="assistant",
            tool_calls=[ToolCall(id="1", name="Read", arguments={"path": "x.txt"})],
        ),
        usage=Usage(input_tokens=1, output_tokens=1),
        model="fake",
        stop_reason="tool_use",
    )
    llm = FakeLLM([turn1, _text_resp('{"summary": "ok"}')])
    events: list[dict] = []
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={"path": "x.txt"},
        workdir=tmp_path,
        extras={
            "current_step_id": "summarize",
            "run_event_recorder": lambda event_type, payload: events.append({
                "event_type": event_type,
                "payload": payload,
            }),
        },
        llm=llm,
        assembler=ContextAssembler(tmp_path),
    )

    rt.invoke(sk, ctx)

    tool_events = [e for e in events if e["event_type"] == "tool_call"]
    assert tool_events == [{
        "event_type": "tool_call",
        "payload": {
            "step_id": "summarize",
            "tool": "Read",
            "call_id": "1",
            "status": "success",
            "read_only": True,
            "evidence": {
                "kind": "file_read",
                "path": "x.txt",
                "result_chars": 5,
                "result_truncated": False,
            },
        },
    }]


def test_llm_path_records_llm_call_diagnostics(tmp_path: Path):
    sk = _make_skill_no_handler(tmp_path)
    llm = FakeLLM([_text_resp('{"summary": "ok"}')])
    events: list[dict] = []
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={"path": "x"},
        workdir=tmp_path,
        extras={
            "current_step_id": "summarize",
            "run_event_recorder": lambda event_type, payload: events.append({
                "event_type": event_type,
                "payload": payload,
            }),
        },
        llm=llm,
        assembler=ContextAssembler(tmp_path),
    )

    rt.invoke(sk, ctx)

    assert [event["event_type"] for event in events] == [
        "llm_call_started",
        "llm_call_finished",
    ]
    started = events[0]["payload"]
    assert started["step_id"] == "summarize"
    assert started["skill"] == "summ"
    assert started["provider"] == "fake"
    assert started["attempt"] == 1
    assert started["messages_count"] == 2
    assert started["tools_count"] == 1
    assert started["timeout_seconds"] == 180
    assert "prompt_chars" in started
    assert "summary" not in str(started)

    finished = events[1]["payload"]
    assert finished["step_id"] == "summarize"
    assert finished["skill"] == "summ"
    assert finished["model"] == "fake"
    assert finished["stop_reason"] == "end_turn"
    assert finished["usage"] == {"input_tokens": 1, "output_tokens": 1}
    assert finished["tool_calls"] == []
    assert "duration_ms" in finished


def test_project_readonly_workspace_rejects_mutating_local_tools(tmp_path: Path):
    sk = _make_skill_with_write_tool(tmp_path)
    turn1 = Response(
        message=Message(
            role="assistant",
            tool_calls=[ToolCall(id="1", name="Write", arguments={"path": "x.txt", "content": "nope"})],
        ),
        usage=Usage(input_tokens=1, output_tokens=1),
        model="fake",
        stop_reason="tool_use",
    )
    llm = FakeLLM([turn1, _text_resp('{"done": true}')])
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={},
        workdir=tmp_path,
        extras={"workspace_mode": "project-readonly"},
        llm=llm,
        assembler=ContextAssembler(tmp_path),
    )

    out = rt.invoke(sk, ctx)

    assert out == {"done": True}
    assert not (tmp_path / "x.txt").exists()
    tool_messages = [m for m in llm.seen_messages[-1] if m.role == "tool"]
    assert "not allowed in project-readonly workspace" in tool_messages[0].content


def test_llm_path_enforces_declared_tool_capabilities(tmp_path: Path):
    sk = _make_skill_with_write_tool_capability(tmp_path)
    turn1 = Response(
        message=Message(
            role="assistant",
            tool_calls=[ToolCall(id="1", name="Write", arguments={"path": "README.md", "content": "nope"})],
        ),
        usage=Usage(input_tokens=1, output_tokens=1),
        model="fake",
        stop_reason="tool_use",
    )
    llm = FakeLLM([turn1, _text_resp('{"done": true}')])
    rt = SkillRuntime()
    ctx = SkillContext(inputs={}, workdir=tmp_path, llm=llm, assembler=ContextAssembler(tmp_path))

    out = rt.invoke(sk, ctx)

    assert out == {"done": True}
    assert not (tmp_path / "README.md").exists()
    tool_messages = [m for m in llm.seen_messages[-1] if m.role == "tool"]
    assert "not in path_allowlist" in tool_messages[0].content


def test_cacheable_llm_skill_reuses_cached_output(tmp_path: Path):
    from code_minions.engine.skill_cache import SkillCache

    sk = _make_cacheable_plan_skill(tmp_path)
    cache = SkillCache(tmp_path / "skill-cache.db")
    llm = FakeLLM([_text_resp('{"tasks": [{"id": "T1"}]}')])
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={"structured_prd": {"goal": "ship"}},
        workdir=tmp_path,
        extras={"skill_cache": cache},
        llm=llm,
        assembler=ContextAssembler(tmp_path),
    )

    assert rt.invoke(sk, ctx) == {"tasks": [{"id": "T1"}]}
    assert rt.invoke(sk, ctx) == {"tasks": [{"id": "T1"}]}
    assert len(llm.seen_messages) == 1


def test_cacheable_llm_skill_invalidates_when_input_file_changes(tmp_path: Path):
    from code_minions.engine.skill_cache import SkillCache

    (tmp_path / "prd.md").write_text("first")
    sk = _make_cacheable_parse_skill(tmp_path)
    cache = SkillCache(tmp_path / "skill-cache.db")
    llm = FakeLLM([
        _text_resp('{"goal": "first"}'),
        _text_resp('{"goal": "second"}'),
    ])
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={"prd_file": "prd.md"},
        workdir=tmp_path,
        extras={"skill_cache": cache},
        llm=llm,
        assembler=ContextAssembler(tmp_path),
    )

    assert rt.invoke(sk, ctx) == {"goal": "first"}
    (tmp_path / "prd.md").write_text("second")
    assert rt.invoke(sk, ctx) == {"goal": "second"}
    assert len(llm.seen_messages) == 2


def test_cache_failure_falls_back_to_llm(tmp_path: Path):
    class BrokenCache:
        def get(self, key):
            raise RuntimeError("cache unavailable")

        def put(self, key, output):
            raise RuntimeError("cache unavailable")

    sk = _make_cacheable_plan_skill(tmp_path)
    llm = FakeLLM([_text_resp('{"tasks": [{"id": "T1"}]}')])
    rt = SkillRuntime()
    ctx = SkillContext(
        inputs={"structured_prd": {"goal": "ship"}},
        workdir=tmp_path,
        extras={"skill_cache": BrokenCache()},
        llm=llm,
        assembler=ContextAssembler(tmp_path),
    )

    assert rt.invoke(sk, ctx) == {"tasks": [{"id": "T1"}]}
    assert len(llm.seen_messages) == 1
