"""Context budgeting and compacting helpers for agent loops."""
from __future__ import annotations

import os
from dataclasses import dataclass

from code_minions.engine.observability import estimate_messages_chars
from code_minions.llm.types import Message

DEFAULT_CONTEXT_BUDGET_CHARS = 120_000


def context_budget_chars() -> int:
    raw = os.environ.get("CODE_MINIONS_CONTEXT_BUDGET_CHARS")
    if not raw:
        return DEFAULT_CONTEXT_BUDGET_CHARS
    try:
        return max(1000, int(raw))
    except ValueError:
        return DEFAULT_CONTEXT_BUDGET_CHARS


@dataclass(frozen=True)
class CompactionResult:
    messages: list[Message]
    compacted: bool
    before_chars: int
    after_chars: int


def compact_messages(
    messages: list[Message],
    *,
    budget_chars: int,
    keep_tail: int = 8,
) -> CompactionResult:
    before = estimate_messages_chars(messages)
    if before <= budget_chars or len(messages) <= keep_tail + 2:
        return CompactionResult(messages=list(messages), compacted=False, before_chars=before, after_chars=before)

    prefix: list[Message] = []
    if messages and messages[0].role == "system":
        prefix.append(messages[0])

    tail = _drop_orphan_tool_results(list(messages[-keep_tail:]))
    dropped = messages[len(prefix): max(len(prefix), len(messages) - keep_tail)]
    summary_lines = [
        "Earlier conversation compacted by code_minions.",
        f"Dropped messages: {len(dropped)}.",
        "Preserved: system instructions, current task tail, latest tool/test evidence.",
    ]
    summary = Message(role="user", content="\n".join(summary_lines))
    compacted_messages = [*prefix, summary, *tail]
    after = estimate_messages_chars(compacted_messages)
    return CompactionResult(
        messages=compacted_messages,
        compacted=True,
        before_chars=before,
        after_chars=after,
    )


def _drop_orphan_tool_results(messages: list[Message]) -> list[Message]:
    known_tool_call_ids: set[str] = set()
    filtered: list[Message] = []
    for message in messages:
        if message.role == "assistant":
            known_tool_call_ids.update(call.id for call in message.tool_calls if call.id)
            filtered.append(message)
            continue
        if message.role == "tool" and message.tool_call_id not in known_tool_call_ids:
            continue
        filtered.append(message)
    return filtered
