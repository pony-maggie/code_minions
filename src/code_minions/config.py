"""Shared devflow.yaml platform configuration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class DevflowConfig:
    workflow_default: str | None
    workflow_search_paths: list[Path]
    skill_search_paths: list[Path]


def _as_path_list(value: Any, default: list[str], key: str) -> list[str]:
    if value is None:
        return default
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise ValueError(f"devflow.yaml: {key} must be a string or list of strings")


def _resolve_paths(project_root: Path, values: list[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        path = Path(value)
        paths.append(path if path.is_absolute() else project_root / path)
    return paths


def load_devflow_config(project_root: Path) -> DevflowConfig:
    """Load workflow/skill platform config with project-local defaults.

    Missing devflow.yaml is allowed so commands can still run with conventional
    `./workflows` and `./skills` directories.
    """
    project_root = project_root.resolve()
    path = project_root / "devflow.yaml"
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}

    workflow = data.get("workflow") or {}
    skills = data.get("skills") or {}
    if not isinstance(workflow, dict):
        raise ValueError("devflow.yaml: workflow must be a mapping")
    if not isinstance(skills, dict):
        raise ValueError("devflow.yaml: skills must be a mapping")

    workflow_default = workflow.get("default")
    if workflow_default is not None and not isinstance(workflow_default, str):
        raise ValueError("devflow.yaml: workflow.default must be a string")

    workflow_paths = _as_path_list(workflow.get("search_paths"), ["./workflows"], "workflow.search_paths")
    skill_paths = _as_path_list(skills.get("search_paths"), ["./skills"], "skills.search_paths")

    return DevflowConfig(
        workflow_default=workflow_default,
        workflow_search_paths=_resolve_paths(project_root, workflow_paths),
        skill_search_paths=_resolve_paths(project_root, skill_paths),
    )
