from __future__ import annotations

from code_minions.engine.context_compaction import compact_messages
from code_minions.llm.types import Message, ToolCall


def test_compaction_preserves_system_prefix_and_recent_tail() -> None:
    messages = [Message(role="system", content="AGENTS instructions must stay")]
    messages.extend(Message(role="user", content=f"old-{idx}" * 100) for idx in range(20))
    messages.append(Message(role="user", content="latest failure"))

    result = compact_messages(messages, budget_chars=200, keep_tail=3)

    assert result.compacted is True
    assert result.messages[0].role == "system"
    assert result.messages[0].content == "AGENTS instructions must stay"
    assert result.messages[-1].content == "latest failure"
    assert result.before_chars > result.after_chars
    assert "Earlier conversation compacted" in result.messages[1].content


def test_compaction_noops_when_under_budget() -> None:
    messages = [Message(role="system", content="small"), Message(role="user", content="task")]

    result = compact_messages(messages, budget_chars=1000)

    assert result.compacted is False
    assert result.messages == messages


def test_compaction_drops_tool_results_when_matching_tool_call_was_dropped() -> None:
    messages = [
        Message(role="system", content="AGENTS instructions must stay"),
        Message(role="user", content="old context" * 100),
        Message(role="assistant", tool_calls=[ToolCall(id="call_1", name="Read", arguments={})]),
        Message(role="tool", tool_call_id="call_1", content="tool result"),
        Message(role="user", content="latest failure summary"),
    ]

    result = compact_messages(messages, budget_chars=200, keep_tail=2)

    assert result.compacted is True
    assert [message.role for message in result.messages] == ["system", "user", "user"]
    assert all(message.tool_call_id != "call_1" for message in result.messages)
