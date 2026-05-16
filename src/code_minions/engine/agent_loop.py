"""Shared agent loop for LLM-driven skills."""
from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from code_minions.engine.context_compaction import compact_messages
from code_minions.engine.llm_transport import LLMCallController, LLMCallError
from code_minions.engine.observability import (
    CONTEXT_COMPACTED,
    LLM_CALL_FAILED,
    LLM_CALL_FINISHED,
    LLM_CALL_STARTED,
    emit_run_event,
    llm_call_payload,
    monotonic_ms,
)
from code_minions.engine.tool_runtime import run_tool_calls
from code_minions.llm.types import Message, Tool, ToolCall


@dataclass(frozen=True)
class AgentLoopConfig:
    max_iterations: int
    max_tool_rounds: int = 24
    role: str | None = None
    skill_name: str = ""
    temperature: float = 0.2
    max_tokens: int = 4096
    context_budget_chars: int | None = None


@dataclass
class AgentLoopResult:
    content: str = ""
    parsed: Any = None
    messages: list[Message] = field(default_factory=list)
    tool_results: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    failure: dict[str, Any] | None = None


FinalParser = Callable[[str], Any]
ToolHandler = Callable[[ToolCall], str]
ParserRetryPrompt = Callable[[Exception], str]


class AgentLoop:
    def __init__(
        self,
        *,
        llm: Any,
        config: AgentLoopConfig,
        event_recorder=None,
        step_id: str | None = None,
        controller: LLMCallController | None = None,
    ):
        self._llm = llm
        self._config = config
        self._event_recorder = event_recorder
        self._step_id = step_id
        self._controller = controller or LLMCallController()

    def run(
        self,
        *,
        messages: list[Message],
        tools: list[Tool] | Callable[[], list[Tool] | None] | None = None,
        final_parser: FinalParser | None = None,
        tool_handler: ToolHandler | None = None,
        parser_retry_prompt: ParserRetryPrompt | None = None,
        after_tool_round: Callable[[list[Any]], str | None] | None = None,
    ) -> AgentLoopResult:
        conversation = list(messages)
        tool_results: list[str] = []
        tool_rounds = 0
        last_summary = ""
        for attempt in range(1, self._config.max_iterations + 1):
            active_tools = tools() if callable(tools) else tools
            if self._config.context_budget_chars:
                compacted = compact_messages(conversation, budget_chars=self._config.context_budget_chars)
                if compacted.compacted:
                    conversation = compacted.messages
                    emit_run_event(
                        self._event_recorder,
                        CONTEXT_COMPACTED,
                        {
                            "step_id": self._step_id,
                            "skill": self._config.skill_name,
                            "before_chars": compacted.before_chars,
                            "after_chars": compacted.after_chars,
                        },
                    )
            started = monotonic_ms()
            emit_run_event(
                self._event_recorder,
                LLM_CALL_STARTED,
                llm_call_payload(
                    llm=self._llm,
                    messages=conversation,
                    tools=active_tools,
                    skill=self._config.skill_name,
                    role=self._config.role,
                    step_id=self._step_id,
                    attempt=attempt,
                    timeout_seconds=self._controller.effective_timeout_seconds,
                    started_ms=started,
                ),
            )
            try:
                resp = self._controller.call(
                    self._llm,
                    messages=conversation,
                    tools=active_tools,
                    temperature=self._config.temperature,
                    max_tokens=self._config.max_tokens,
                )
            except LLMCallError as exc:
                duration = monotonic_ms() - started
                payload = llm_call_payload(
                    llm=self._llm,
                    messages=conversation,
                    tools=active_tools,
                    skill=self._config.skill_name,
                    role=self._config.role,
                    step_id=self._step_id,
                    attempt=attempt,
                    timeout_seconds=self._controller.effective_timeout_seconds,
                    duration_ms=duration,
                    extra={
                        "classification": exc.failure.classification,
                        "retryable": exc.failure.retryable,
                        "error": exc.failure.message,
                    },
                )
                emit_run_event(self._event_recorder, LLM_CALL_FAILED, payload)
                return AgentLoopResult(messages=conversation, tool_results=tool_results, failure=payload)
            duration = monotonic_ms() - started
            message = resp.message
            conversation.append(message)
            emit_run_event(
                self._event_recorder,
                LLM_CALL_FINISHED,
                llm_call_payload(
                    llm=self._llm,
                    messages=conversation,
                    tools=active_tools,
                    skill=self._config.skill_name,
                    role=self._config.role,
                    step_id=self._step_id,
                    attempt=attempt,
                    model=getattr(resp, "model", ""),
                    timeout_seconds=self._controller.effective_timeout_seconds,
                    duration_ms=duration,
                    extra={
                        "stop_reason": getattr(resp, "stop_reason", ""),
                        "usage": {
                            "input_tokens": getattr(getattr(resp, "usage", None), "input_tokens", 0),
                            "output_tokens": getattr(getattr(resp, "usage", None), "output_tokens", 0),
                        },
                        "tool_calls": [getattr(tc, "name", "") for tc in message.tool_calls],
                    },
                ),
            )
            if message.tool_calls:
                tool_rounds += 1
                if tool_rounds > self._config.max_tool_rounds:
                    failure = {
                        "classification": "max_tool_rounds",
                        "message": (
                            f"agent loop exceeded max_tool_rounds={self._config.max_tool_rounds}; "
                            f"LLM exceeded tool_call round limit={self._config.max_tool_rounds}; "
                            f"last assistant response: {last_summary}"
                        ),
                    }
                    return AgentLoopResult(messages=conversation, tool_results=tool_results, failure=failure)
                if tool_handler is None:
                    def handler(tc):
                        return f"[error] no tool handler for {tc.name}"
                else:
                    handler = tool_handler
                round_results = run_tool_calls(message.tool_calls, run_one=handler)
                for result in round_results:
                    tool_results.append(result.content)
                    conversation.append(Message(
                        role="tool",
                        tool_call_id=result.call_id,
                        content=result.content,
                        name=result.name,
                    ))
                prompt = after_tool_round(round_results) if after_tool_round is not None else None
                if prompt:
                    conversation.append(Message(role="user", content=prompt))
                last_summary = (
                    "tool_calls=["
                    + ", ".join(tc.name for tc in message.tool_calls)
                    + f"]; {_assistant_summary(resp)}"
                )
                continue
            last_summary = _assistant_summary(resp)
            if final_parser is None:
                return AgentLoopResult(content=message.content, parsed=message.content, messages=conversation, tool_results=tool_results)
            try:
                parsed = final_parser(message.content)
            except Exception as exc:
                if getattr(exc, "run_status", None) is not None or getattr(exc, "output", None) is not None:
                    raise
                if parser_retry_prompt is None:
                    return AgentLoopResult(
                        content=message.content,
                        messages=conversation,
                        tool_results=tool_results,
                        failure={
                            "classification": "final_parser_error",
                            "message": f"{exc}; last assistant response: {last_summary}",
                        },
                    )
                conversation.append(Message(role="user", content=_call_parser_retry(parser_retry_prompt, exc, last_summary)))
                continue
            if parsed is None:
                return AgentLoopResult(
                    content=message.content,
                    messages=conversation,
                    tool_results=tool_results,
                    failure={
                        "classification": "final_parser_error",
                        "message": (
                            "LLM did not return JSON; final parser returned None; "
                            f"last assistant response: {last_summary}"
                        ),
                    },
                )
            return AgentLoopResult(content=message.content, parsed=parsed, messages=conversation, tool_results=tool_results)
        return AgentLoopResult(
            messages=conversation,
            tool_results=tool_results,
            failure={
                "classification": "max_iterations",
                "message": (
                    f"agent loop exceeded max_iterations={self._config.max_iterations}; "
                    f"last assistant response: {last_summary}"
                ),
            },
        )


def _assistant_summary(resp: Any) -> str:
    usage = getattr(resp, "usage", None)
    message = getattr(resp, "message", None)
    content = getattr(message, "content", "") if message is not None else ""
    return (
        f"content={content[:500]!r}; stop_reason={getattr(resp, 'stop_reason', '')}; "
        f"model={getattr(resp, 'model', '')}; "
        f"usage=input:{getattr(usage, 'input_tokens', 0)},output:{getattr(usage, 'output_tokens', 0)}"
    )


def _call_parser_retry(parser_retry_prompt: ParserRetryPrompt, exc: Exception, summary: str) -> str:
    signature = inspect.signature(parser_retry_prompt)
    accepts_varargs = any(
        param.kind == inspect.Parameter.VAR_POSITIONAL
        for param in signature.parameters.values()
    )
    if accepts_varargs or len(signature.parameters) >= 2:
        return parser_retry_prompt(exc, summary)  # type: ignore[misc]
    return parser_retry_prompt(exc)
