"""LLMBackend protocol shared across providers."""
from __future__ import annotations

from typing import Protocol

from code_minions.llm.types import Message, Response, Tool


class LLMBackend(Protocol):
    """All provider adapters implement this."""

    name: str                           # "anthropic" | "openai" | ...

    def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> Response:
        ...

    def supports_tool_use(self) -> bool:
        ...
