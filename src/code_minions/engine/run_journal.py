"""Typed run journal event helpers."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from code_minions.engine.observability import emit_run_event, llm_identity

RunEventRecorder = Callable[[str, dict[str, Any]], None]

STEP_ATTEMPT_STARTED = "step_attempt_started"
STEP_ATTEMPT_FINISHED = "step_attempt_finished"
STEP_ATTEMPT_FAILED = "step_attempt_failed"
STEP_ATTEMPT_SKIPPED = "step_attempt_skipped"
STEP_ATTEMPT_REUSED = "step_attempt_reused"


def step_attempt_snapshot(
    *,
    step: Any,
    skill: Any | None,
    resolved_inputs: dict[str, Any] | None,
    workspace_mode: str,
    is_resume: bool,
    llm: Any | None,
) -> dict[str, Any]:
    """Return stable, serializable step attempt configuration."""
    return {
        "step_id": getattr(step, "id", ""),
        "skill": getattr(step, "skill", ""),
        "role": getattr(getattr(skill, "meta", None), "role", None) or "",
        "workspace_mode": workspace_mode,
        "is_resume": is_resume,
        "input_keys": sorted((resolved_inputs or {}).keys()),
        "depends_on": list(getattr(step, "depends_on", []) or []),
        "sensors": _sensor_names(getattr(step, "sensors", []) or []),
        "post_run_hooks": list(getattr(getattr(skill, "meta", None), "hooks", {}).get("post_run", []))
        if skill is not None else [],
        "llm": llm_identity(llm),
    }


def record_step_attempt_started(
    recorder: RunEventRecorder | None,
    *,
    observable_step_id: str,
    step: Any,
    skill: Any | None,
    resolved_inputs: dict[str, Any],
    workspace_mode: str,
    is_resume: bool,
    llm: Any | None,
    detail: str | None = None,
) -> dict[str, Any]:
    snapshot = step_attempt_snapshot(
        step=step,
        skill=skill,
        resolved_inputs=resolved_inputs,
        workspace_mode=workspace_mode,
        is_resume=is_resume,
        llm=llm,
    )
    snapshot["step_id"] = observable_step_id
    payload: dict[str, Any] = {
        "step_id": observable_step_id,
        "attempt": 1,
        "snapshot": snapshot,
    }
    if detail:
        payload["detail"] = detail
    emit_run_event(recorder, STEP_ATTEMPT_STARTED, payload)
    return snapshot


def record_step_attempt_finished(
    recorder: RunEventRecorder | None,
    *,
    observable_step_id: str,
    status: str,
    output: dict[str, Any] | None = None,
    detail: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "step_id": observable_step_id,
        "attempt": 1,
        "status": status,
        "output_keys": sorted((output or {}).keys()),
    }
    if detail:
        payload["detail"] = detail
    emit_run_event(recorder, STEP_ATTEMPT_FINISHED, payload)


def record_step_attempt_failed(
    recorder: RunEventRecorder | None,
    *,
    observable_step_id: str,
    error: str,
    output: dict[str, Any] | None = None,
    detail: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "step_id": observable_step_id,
        "attempt": 1,
        "status": "failed",
        "error": error,
        "output_keys": sorted((output or {}).keys()),
    }
    if detail:
        payload["detail"] = detail
    emit_run_event(recorder, STEP_ATTEMPT_FAILED, payload)


def record_step_attempt_status(
    recorder: RunEventRecorder | None,
    event_type: str,
    *,
    observable_step_id: str,
    reason: str,
    output: dict[str, Any] | None = None,
) -> None:
    emit_run_event(
        recorder,
        event_type,
        {
            "step_id": observable_step_id,
            "attempt": 1,
            "status": event_type.removeprefix("step_attempt_"),
            "reason": reason,
            "output_keys": sorted((output or {}).keys()),
        },
    )


def _sensor_names(sensors: list[Any]) -> list[str]:
    names: list[str] = []
    for idx, sensor in enumerate(sensors, start=1):
        if isinstance(sensor, str):
            names.append(sensor)
        elif isinstance(sensor, dict):
            names.append(str(sensor.get("name") or f"inline-{idx}"))
        else:
            names.append(f"inline-{idx}")
    return names
