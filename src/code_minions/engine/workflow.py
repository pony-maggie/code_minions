"""Workflow model and YAML loader."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError


class WorkflowLoadError(Exception):
    """Raised when a workflow YAML fails to load or validate."""


class InputSpec(BaseModel):
    type: str
    required: bool = False
    default: Any = None


class WorkflowStep(BaseModel):
    id: str
    skill: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    for_each: str | None = None   # M1 accepted but not executed; enforced in DAG Runner
    as_: str | None = Field(default=None, alias="as")
    max_parallel: int = 1

    model_config = {"populate_by_name": True}


class WorkspaceSpec(BaseModel):
    mode: Literal["none", "project-readonly", "git-worktree"] = "git-worktree"


class Workflow(BaseModel):
    name: str
    description: str = ""
    extends: str | None = None
    workspace: WorkspaceSpec = Field(default_factory=WorkspaceSpec)
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    preset_inputs: dict[str, Any] = Field(default_factory=dict)
    steps: list[WorkflowStep]


def load_workflow(path: Path) -> Workflow:
    """Load and validate a workflow YAML file."""
    p = Path(path)
    if not p.exists():
        raise WorkflowLoadError(f"workflow file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise WorkflowLoadError(f"invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise WorkflowLoadError("workflow schema error: top-level YAML must be an object")

    extends = data.get("extends")
    if extends:
        parent = p.parent / str(extends)
        parent_data = _load_workflow_data(parent)
        data = _merge_workflow_data(parent_data, data)

    try:
        wf = Workflow.model_validate(data)
    except ValidationError as e:
        raise WorkflowLoadError(f"workflow schema error: {e}") from e

    _validate_graph(wf)
    return wf


def _load_workflow_data(path: Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise WorkflowLoadError(f"workflow file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise WorkflowLoadError(f"invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise WorkflowLoadError("workflow schema error: top-level YAML must be an object")
    extends = data.get("extends")
    if extends:
        parent = p.parent / str(extends)
        data = _merge_workflow_data(_load_workflow_data(parent), data)
    return data


def _merge_workflow_data(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    merged = dict(parent)
    for key, value in child.items():
        if key == "extends":
            continue
        if key in {"inputs", "preset_inputs"} and isinstance(value, dict):
            base = merged.get(key)
            merged[key] = {**base, **value} if isinstance(base, dict) else dict(value)
            continue
        merged[key] = value
    return merged


def _validate_graph(wf: Workflow) -> None:
    seen: set[str] = set()
    for step in wf.steps:
        if step.id in seen:
            raise WorkflowLoadError(f"duplicate step id: {step.id}")
        seen.add(step.id)
    for step in wf.steps:
        for dep in step.depends_on:
            if dep not in seen:
                raise WorkflowLoadError(f"step {step.id} depends on unknown step {dep}")
        if step.for_each is not None and not step.as_:
            raise WorkflowLoadError(f"step {step.id}: for_each requires 'as' field")
