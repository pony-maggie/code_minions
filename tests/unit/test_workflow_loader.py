"""Tests for Workflow YAML loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from code_minions.engine.workflow import Workflow, WorkflowLoadError, load_workflow


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def test_load_minimal_workflow(tmp_path: Path) -> None:
    yaml_file = _write(
        tmp_path / "wf.yaml",
        """
name: hello
description: test
inputs:
  greeting: {type: string, required: true}
steps:
  - id: say
    skill: hello-world
    inputs:
      msg: $inputs.greeting
""",
    )
    wf = load_workflow(yaml_file)
    assert isinstance(wf, Workflow)
    assert wf.name == "hello"
    assert len(wf.steps) == 1
    assert wf.steps[0].id == "say"
    assert wf.steps[0].skill == "hello-world"
    assert wf.steps[0].inputs == {"msg": "$inputs.greeting"}


def test_load_workflow_with_depends_on(tmp_path: Path) -> None:
    yaml_file = _write(
        tmp_path / "wf.yaml",
        """
name: two-steps
steps:
  - id: a
    skill: s1
  - id: b
    skill: s2
    depends_on: [a]
""",
    )
    wf = load_workflow(yaml_file)
    assert wf.steps[1].depends_on == ["a"]


def test_load_workflow_extends_parent_with_preset_inputs(tmp_path: Path) -> None:
    _write(
        tmp_path / "base.yaml",
        """
name: base
description: base workflow
workspace:
  mode: git-worktree
inputs:
  prd: {type: string, required: true}
steps:
  - id: parse
    skill: parse-prd
    inputs:
      prd_file: $inputs.prd
      delivery_stack_id: $inputs.delivery_stack_id
""",
    )
    yaml_file = _write(
        tmp_path / "react.yaml",
        """
name: react-vite-prd-to-commit
description: React/Vite preset
extends: base.yaml
preset_inputs:
  delivery_stack_id: react-vite
""",
    )

    wf = load_workflow(yaml_file)

    assert wf.name == "react-vite-prd-to-commit"
    assert wf.description == "React/Vite preset"
    assert wf.workspace.mode == "git-worktree"
    assert wf.inputs["prd"].required is True
    assert wf.preset_inputs == {"delivery_stack_id": "react-vite"}
    assert len(wf.steps) == 1
    assert wf.steps[0].inputs["delivery_stack_id"] == "$inputs.delivery_stack_id"


@pytest.mark.parametrize(
    ("workflow_name", "stack_id"),
    [
        ("react-vite-prd-to-commit", "react-vite"),
        ("swift-xcodegen-prd-to-commit", "swift-xcodegen"),
        ("go-service-prd-to-commit", "go-service"),
        ("python-cli-prd-to-commit", "python-cli"),
    ],
)
def test_builtin_stack_prd_to_commit_aliases_extend_generic_workflow(workflow_name: str, stack_id: str) -> None:
    path = Path(f"src/code_minions/builtin/workflows/{workflow_name}.yaml")

    wf = load_workflow(path)

    assert wf.name == workflow_name
    assert wf.preset_inputs == {"delivery_stack_id": stack_id}
    assert [step.id for step in wf.steps] == ["parse", "plan", "implement", "acceptance", "report"]
    assert wf.steps[0].inputs["delivery_stack_id"] == "$inputs.delivery_stack_id"


def test_load_workflow_duplicate_step_ids_fails(tmp_path: Path) -> None:
    yaml_file = _write(
        tmp_path / "wf.yaml",
        """
name: bad
steps:
  - id: a
    skill: s
  - id: a
    skill: s
""",
    )
    with pytest.raises(WorkflowLoadError, match="duplicate step id"):
        load_workflow(yaml_file)


def test_load_workflow_unknown_depends_on_fails(tmp_path: Path) -> None:
    yaml_file = _write(
        tmp_path / "wf.yaml",
        """
name: bad
steps:
  - id: a
    skill: s
    depends_on: [nowhere]
""",
    )
    with pytest.raises(WorkflowLoadError, match="unknown step"):
        load_workflow(yaml_file)


def test_load_workflow_missing_file_fails(tmp_path: Path) -> None:
    with pytest.raises(WorkflowLoadError, match="not found"):
        load_workflow(tmp_path / "nope.yaml")
