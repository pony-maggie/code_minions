"""LLM call controller: retry, timeout metadata, and failure classification."""
from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from code_minions.engine.observability import request_timeout_seconds


@dataclass(frozen=True)
class LLMFailure:
    classification: str
    message: str
    retryable: bool
    attempt: int


class LLMCallError(RuntimeError):
    def __init__(self, failure: LLMFailure):
        super().__init__(failure.message)
        self.failure = failure


@dataclass(frozen=True)
class LLMCallController:
    max_attempts: int = 1
    timeout_seconds: float | None = None
    backoff_seconds: float = 0.5
    sleep: Callable[[float], None] = time.sleep

    def call(self, llm: Any, *, messages: list[Any], tools: list[Any] | None = None, **kwargs: Any) -> Any:
        attempts = max(1, self.max_attempts)
        last_failure: LLMFailure | None = None
        for attempt in range(1, attempts + 1):
            try:
                return _run_with_wall_clock_timeout(
                    lambda: llm.chat(messages=messages, tools=tools, **kwargs),
                    seconds=self.effective_timeout_seconds,
                    label="LLM request",
                )
            except Exception as exc:
                failure = classify_llm_exception(exc, attempt=attempt)
                last_failure = failure
                if attempt >= attempts or not failure.retryable:
                    raise LLMCallError(failure) from exc
                self.sleep(self.backoff_seconds * attempt)
        if last_failure is not None:
            raise LLMCallError(last_failure)
        raise LLMCallError(LLMFailure(
            classification="unknown",
            message="LLM call failed before any attempt",
            retryable=False,
            attempt=0,
        ))

    @property
    def effective_timeout_seconds(self) -> float:
        return self.timeout_seconds or request_timeout_seconds()


def classify_llm_exception(exc: Exception, *, attempt: int = 1) -> LLMFailure:
    name = type(exc).__name__
    text = f"{name}: {exc}"
    lowered = text.lower()
    classification = "provider_error"
    retryable = False
    if "timed out" in lowered or "timeout" in lowered:
        classification = "provider_timeout"
        retryable = True
    elif "503" in lowered or "serviceunavailable" in lowered or "overload" in lowered or "high demand" in lowered:
        classification = "provider_unavailable"
        retryable = True
    elif "429" in lowered or "rate limit" in lowered:
        classification = "provider_rate_limited"
        retryable = True
    elif "authentication" in lowered or "permission" in lowered or "invalid api key" in lowered or "unauthorized" in lowered:
        classification = "auth_error"
    elif "badrequest" in lowered or "bad request" in lowered or "400" in lowered:
        if "schema" in lowered or "function" in lowered or "tool" in lowered or "anyof" in lowered:
            classification = "schema_incompatible"
        else:
            classification = "bad_request"
    elif "schema" in lowered and "incompatible" in lowered:
        classification = "schema_incompatible"
    elif any(marker in lowered for marker in ("connection reset", "remotedisconnected", "eof", "ssl")):
        classification = "provider_connection_error"
        retryable = True
    return LLMFailure(
        classification=classification,
        message=text,
        retryable=retryable,
        attempt=attempt,
    )


def _run_with_wall_clock_timeout(func: Callable[[], Any], *, seconds: float, label: str) -> Any:
    results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            results.put((True, func()))
        except BaseException as exc:
            results.put((False, exc))

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeoutError(f"{label} timed out after {seconds}s")

    ok, value = results.get_nowait()
    if ok:
        return value
    raise value
