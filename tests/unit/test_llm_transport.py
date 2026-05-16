from __future__ import annotations

import time

import pytest

from code_minions.engine.llm_transport import (
    LLMCallController,
    LLMCallError,
    classify_llm_exception,
)


class FakeLLM:
    name = "fake"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def chat(self, **_kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_classifies_timeout_as_provider_timeout() -> None:
    failure = classify_llm_exception(RuntimeError("request timed out after 180s"))

    assert failure.classification == "provider_timeout"
    assert failure.retryable is True


def test_classifies_503_as_provider_unavailable() -> None:
    failure = classify_llm_exception(RuntimeError("HTTP 503: high demand"))

    assert failure.classification == "provider_unavailable"
    assert failure.retryable is True


def test_classifies_gemini_schema_400_as_schema_incompatible() -> None:
    failure = classify_llm_exception(RuntimeError("BadRequestError: 400 invalid function schema anyOf"))

    assert failure.classification == "schema_incompatible"
    assert failure.retryable is False


def test_classifies_auth_error() -> None:
    failure = classify_llm_exception(RuntimeError("AuthenticationError: invalid api key"))

    assert failure.classification == "auth_error"


def test_controller_retries_retryable_errors() -> None:
    llm = FakeLLM([RuntimeError("HTTP 503"), "ok"])
    controller = LLMCallController(max_attempts=2, sleep=lambda _seconds: None)

    assert controller.call(llm, messages=[], tools=None) == "ok"
    assert llm.calls == 2


def test_controller_enforces_wall_clock_timeout() -> None:
    class SlowLLM:
        def chat(self, **_kwargs):
            time.sleep(1)
            return "late"

    controller = LLMCallController(max_attempts=1, timeout_seconds=0.01, sleep=lambda _seconds: None)

    with pytest.raises(LLMCallError) as exc:
        controller.call(SlowLLM(), messages=[])

    assert exc.value.failure.classification == "provider_timeout"


def test_controller_raises_typed_error_for_non_retryable_errors() -> None:
    controller = LLMCallController(max_attempts=3, sleep=lambda _seconds: None)

    with pytest.raises(LLMCallError) as exc:
        controller.call(FakeLLM([RuntimeError("BadRequestError: 400 bad request")]), messages=[])

    assert exc.value.failure.classification == "bad_request"
