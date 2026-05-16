from __future__ import annotations

from code_minions.engine.agent_loop import AgentLoop, AgentLoopConfig
from code_minions.engine.llm_transport import LLMCallController
from code_minions.llm.types import Message, Response, ToolCall, Usage


class FakeLLM:
    name = "fake"

    def __init__(self, responses):
        self.responses = list(responses)
        self.seen_messages: list[list[Message]] = []

    def chat(self, messages, tools=None, **_kwargs):
        self.seen_messages.append(list(messages))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _resp(content: str = "", tool_calls=None) -> Response:
    return Response(
        message=Message(role="assistant", content=content, tool_calls=tool_calls or []),
        usage=Usage(input_tokens=1, output_tokens=1),
        model="fake-model",
        stop_reason="tool_use" if tool_calls else "end_turn",
    )


def test_agent_loop_returns_final_content_without_tools() -> None:
    loop = AgentLoop(llm=FakeLLM([_resp("done")]), config=AgentLoopConfig(max_iterations=2, skill_name="s"))

    result = loop.run(messages=[Message(role="user", content="go")])

    assert result.parsed == "done"
    assert result.content == "done"


def test_agent_loop_executes_tool_calls_and_sends_results_back() -> None:
    tool_call = ToolCall(id="call-1", name="Read", arguments={"path": "x"})
    llm = FakeLLM([_resp(tool_calls=[tool_call]), _resp('{"ok": true}')])
    loop = AgentLoop(llm=llm, config=AgentLoopConfig(max_iterations=3, skill_name="s"))

    result = loop.run(
        messages=[Message(role="user", content="go")],
        final_parser=lambda content: {"content": content},
        tool_handler=lambda tc: f"result for {tc.name}",
    )

    assert result.parsed == {"content": '{"ok": true}'}
    assert any(message.role == "tool" and message.content == "result for Read" for message in llm.seen_messages[-1])


def test_agent_loop_reports_max_iterations_failure() -> None:
    loop = AgentLoop(
        llm=FakeLLM([_resp("not json"), _resp("still not json")]),
        config=AgentLoopConfig(max_iterations=2, skill_name="s"),
    )

    result = loop.run(
        messages=[Message(role="user", content="go")],
        final_parser=lambda _content: (_ for _ in ()).throw(ValueError("bad json")),
        parser_retry_prompt=lambda exc: str(exc),
    )

    assert result.failure is not None
    assert result.failure["classification"] == "max_iterations"


def test_agent_loop_records_llm_failure_classification() -> None:
    events: list[dict] = []
    loop = AgentLoop(
        llm=FakeLLM([RuntimeError("HTTP 503")]),
        config=AgentLoopConfig(max_iterations=1, skill_name="s"),
        event_recorder=lambda event_type, payload: events.append({"event_type": event_type, "payload": payload}),
        controller=LLMCallController(max_attempts=1),
    )

    result = loop.run(messages=[Message(role="user", content="go")])

    assert result.failure is not None
    assert result.failure["classification"] == "provider_unavailable"
    assert [event["event_type"] for event in events] == ["llm_call_started", "llm_call_failed"]


def test_agent_loop_compacts_context_before_model_call() -> None:
    events: list[dict] = []
    messages = [Message(role="system", content="AGENTS")]
    messages.extend(Message(role="user", content="x" * 100) for _ in range(20))
    loop = AgentLoop(
        llm=FakeLLM([_resp("done")]),
        config=AgentLoopConfig(max_iterations=1, skill_name="s", context_budget_chars=200),
        event_recorder=lambda event_type, payload: events.append({"event_type": event_type, "payload": payload}),
    )

    result = loop.run(messages=messages)

    assert result.content == "done"
    assert any(event["event_type"] == "context_compacted" for event in events)
