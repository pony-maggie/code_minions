"""Shared LLM types: unified across Anthropic/OpenAI backends."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None    # only for role="tool"
    name: str | None = None            # only for role="tool"


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]       # JSON Schema


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class Response:
    message: Message                    # assistant's reply
    usage: Usage
    model: str
    stop_reason: str                    # "end_turn" | "tool_use" | "max_tokens" | ...
