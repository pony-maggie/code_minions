"""Workflow failure classification for reports and run diagnostics."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FailureClassification:
    classification: str
    message: str
    next_action: str


def classify_failure(
    *,
    error: str | None = None,
    run_events: list[dict[str, Any]] | None = None,
    step_output: dict[str, Any] | None = None,
) -> FailureClassification:
    events = run_events or []
    output = step_output or {}

    for event in reversed(events):
        event_type = str(event.get("event_type") or event.get("type") or "")
        payload = event.get("payload") or {}
        classification = str(payload.get("classification") or "")
        if event_type == "llm_call_failed" and classification in {
            "provider_timeout",
            "provider_unavailable",
            "provider_rate_limited",
            "provider_connection_error",
        }:
            return FailureClassification(
                "provider_unavailable",
                payload.get("error") or payload.get("message") or classification,
                "Retry later or switch/fallback the affected LLM role provider.",
            )
        if event_type == "llm_call_failed" and classification in {"schema_incompatible", "bad_request"}:
            return FailureClassification(
                "workflow_systemic",
                payload.get("error") or payload.get("message") or classification,
                "Fix code_minions provider payload/schema handling before retrying.",
            )
        if event_type.startswith("browser") or event_type in {"browser_acceptance", "acceptance_failed"}:
            return FailureClassification(
                "acceptance_failed",
                payload.get("message") or "Browser/product acceptance failed.",
                "Inspect browser acceptance artifacts and fix the delivered product.",
            )

    text = f"{error or ''}\n{output}".lower()
    if "provider_timeout" in text or "timed out" in text or "503" in text or "high demand" in text:
        return FailureClassification(
            "provider_unavailable",
            error or "Provider call failed or timed out.",
            "Retry later or switch/fallback the affected LLM role provider.",
        )
    if "schema_incompatible" in text or "badrequest" in text or "bad request" in text:
        return FailureClassification(
            "workflow_systemic",
            error or "Provider rejected the workflow payload.",
            "Fix code_minions provider payload/schema handling before retrying.",
        )
    if "acceptance" in text and ("fail" in text or "accepted': false" in text or '"accepted": false' in text):
        return FailureClassification(
            "acceptance_failed",
            error or "Product acceptance failed.",
            "Inspect acceptance evidence and fix the product behavior or UI.",
        )
    if "review unresolved" in text or "review blocker" in text or "review blockers" in text:
        return FailureClassification(
            "implementation_fixable",
            error or "Implementation review blockers remain.",
            "Continue implementation/self-heal using the review findings.",
        )
    if "test" in text or "build" in text or "pytest" in text or "npm" in text:
        return FailureClassification(
            "implementation_fixable",
            error or "Implementation verification failed.",
            "Continue implementation/self-heal using the verification evidence.",
        )
    return FailureClassification(
        "workflow_systemic",
        error or "Workflow failed without a more specific classification.",
        "Inspect run events and engine logs.",
    )
