from __future__ import annotations

from code_minions.engine.failure_classification import classify_failure


def test_provider_timeout_event_classifies_provider_unavailable() -> None:
    result = classify_failure(run_events=[{
        "event_type": "llm_call_failed",
        "payload": {"classification": "provider_timeout", "error": "timed out"},
    }])

    assert result.classification == "provider_unavailable"


def test_schema_error_event_classifies_workflow_systemic() -> None:
    result = classify_failure(run_events=[{
        "event_type": "llm_call_failed",
        "payload": {"classification": "schema_incompatible", "error": "schema rejected"},
    }])

    assert result.classification == "workflow_systemic"


def test_test_failure_text_classifies_implementation_fixable() -> None:
    result = classify_failure(error="npm test failed")

    assert result.classification == "implementation_fixable"


def test_review_unresolved_classifies_implementation_fixable() -> None:
    result = classify_failure(error="SkillExecutionError('review unresolved')")

    assert result.classification == "implementation_fixable"
    assert "review" in result.next_action.lower()


def test_acceptance_failure_text_classifies_acceptance_failed() -> None:
    result = classify_failure(step_output={"accepted": False, "acceptance_items": [{"status": "fail"}]})

    assert result.classification == "acceptance_failed"
