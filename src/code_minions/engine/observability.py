"""Structured runtime observability helpers for workflow runs."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from typing import Any

from code_minions.logging import redact_secrets

RunEventRecorder = Callable[[str, dict[str, Any]], None]

LLM_CALL_STARTED = "llm_call_started"
LLM_CALL_FINISHED = "llm_call_finished"
LLM_CALL_FAILED = "llm_call_failed"
TOOL_CALL_STARTED = "tool_call_started"
TOOL_CALL_FINISHED = "tool_call_finished"
TOOL_CALL_FAILED = "tool_call_failed"
COMMAND_STARTED = "command_started"
COMMAND_FINISHED = "command_finished"
COMMAND_FAILED = "command_failed"
CONTEXT_COMPACTED = "context_compacted"

DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS = 180


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def request_timeout_seconds() -> int:
    raw = os.environ.get("CODE_MINIONS_LLM_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS


def estimate_messages_chars(messages: list[Any]) -> int:
    total = 0
    for message in messages:
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        total += len(str(content or ""))
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls is None and isinstance(message, dict):
            tool_calls = message.get("tool_calls")
        for call in tool_calls or []:
            args = getattr(call, "arguments", None)
            if args is None and isinstance(call, dict):
                args = call.get("arguments")
            total += len(json.dumps(args or {}, sort_keys=True, default=str))
    return total


def redacted_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    redacted = redact_secrets(encoded)
    try:
        decoded = json.loads(redacted)
    except json.JSONDecodeError:
        return {"message": redacted}
    return decoded if isinstance(decoded, dict) else {"value": decoded}


def emit_run_event(
    event_recorder: RunEventRecorder | None,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    if event_recorder is None:
        return
    event_recorder(event_type, redacted_event_payload(payload))


def llm_identity(llm: Any, *, model: str | None = None) -> dict[str, Any]:
    provider = getattr(llm, "_provider", None) or getattr(llm, "provider", None) or getattr(llm, "name", "")
    default_model = getattr(llm, "_default_model", None) or getattr(llm, "default_model", None) or ""
    return {
        "provider": str(provider or ""),
        "model": str(model or default_model or ""),
    }


def llm_call_payload(
    *,
    llm: Any,
    messages: list[Any],
    tools: list[Any] | None,
    skill: str,
    role: str | None,
    step_id: str | None,
    attempt: int,
    model: str | None = None,
    timeout_seconds: int | None = None,
    started_ms: int | None = None,
    duration_ms: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "step_id": step_id,
        "skill": skill,
        "role": role or "",
        "attempt": attempt,
        "timeout_seconds": timeout_seconds or request_timeout_seconds(),
        "messages_count": len(messages),
        "tools_count": len(tools or []),
        "prompt_chars": estimate_messages_chars(messages),
        **llm_identity(llm, model=model),
    }
    if started_ms is not None:
        payload["started_ms"] = started_ms
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if extra:
        payload.update(extra)
    return payload
