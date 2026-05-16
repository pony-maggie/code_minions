from __future__ import annotations

from code_minions.engine.observability import (
    LLM_CALL_FAILED,
    LLM_CALL_FINISHED,
    LLM_CALL_STARTED,
    emit_run_event,
    estimate_messages_chars,
    llm_call_payload,
    redacted_event_payload,
)
from code_minions.llm.types import Message, ToolCall


class FakeLLM:
    name = "fake"
    _provider = "minimax"
    _default_model = "abab6.5s"


def test_runtime_observability_defines_llm_event_names() -> None:
    assert LLM_CALL_STARTED == "llm_call_started"
    assert LLM_CALL_FINISHED == "llm_call_finished"
    assert LLM_CALL_FAILED == "llm_call_failed"


def test_estimate_messages_chars_uses_sizes_not_raw_prompt_contract() -> None:
    messages = [
        Message(role="system", content="abc"),
        Message(role="assistant", tool_calls=[ToolCall(id="1", name="Write", arguments={"path": "x.txt"})]),
    ]

    assert estimate_messages_chars(messages) >= len("abc")


def test_redacted_event_payload_removes_secret_values() -> None:
    payload = redacted_event_payload({"header": "Authorization: Bearer abcdefghijklmnop"})

    assert payload["header"] == "Authorization: Bearer [REDACTED]"


def test_emit_run_event_redacts_before_recording() -> None:
    events: list[tuple[str, dict]] = []

    emit_run_event(
        lambda event_type, payload: events.append((event_type, payload)),
        "sample",
        {"api_key": "sk-abcdefg1234567890hijklm"},
    )

    assert events == [("sample", {"api_key": "[REDACTED]"})]


def test_llm_call_payload_records_metadata_without_prompt_text() -> None:
    payload = llm_call_payload(
        llm=FakeLLM(),
        messages=[Message(role="user", content="secret product plan")],
        tools=[],
        skill="summ",
        role="planner",
        step_id="parse",
        attempt=2,
    )

    assert payload["provider"] == "minimax"
    assert payload["model"] == "abab6.5s"
    assert payload["role"] == "planner"
    assert payload["messages_count"] == 1
    assert payload["tools_count"] == 0
    assert payload["prompt_chars"] == len("secret product plan")
    assert "secret product plan" not in str(payload)
