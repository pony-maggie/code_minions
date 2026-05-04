"""Shared types and enums used across modules."""
from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    COMPLETED_WITH_ISSUES = "completed_with_issues"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


TERMINAL_RUN_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.COMPLETED_WITH_ISSUES}
)
TERMINAL_STEP_STATUSES: frozenset[StepStatus] = frozenset(
    {StepStatus.SUCCESS, StepStatus.FAILED, StepStatus.SKIPPED}
)
