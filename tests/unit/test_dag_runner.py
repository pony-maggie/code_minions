"""Tests for DAG Runner."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from code_minions.engine.dag_runner import DAGRunner, DAGRunnerError
from code_minions.engine.skill import Skill, load_skill
from code_minions.engine.skill_runtime import SkillContext, SkillRuntime
from code_minions.engine.workflow import InputSpec, Workflow, WorkflowStep


class FakeSkillRuntime(SkillRuntime):
    """Capture invocations, return fixed outputs per skill name."""

    def __init__(self, outputs: dict[str, dict[str, Any]]) -> None:
        self._outputs = outputs
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def invoke(self, skill: Skill, ctx: SkillContext) -> dict[str, Any]:
        self.calls.append((skill.name, dict(ctx.inputs)))
        return self._outputs[skill.name]


def _stub_skill(tmp: Path, name: str) -> Skill:
    d = tmp / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\nname: {name}\ninputs: {{}}\noutputs: {{}}\n---\n\n# {name}\n")
    return load_skill(d)


def test_sequential_execution_and_variable_resolution(tmp_path: Path) -> None:
    wf = Workflow(
        name="w",
        inputs={},
        steps=[
            WorkflowStep(id="a", skill="a", inputs={"init": "hello"}),
            WorkflowStep(
                id="b", skill="b",
                inputs={"prev": "$steps.a.output.greeting"},
                depends_on=["a"],
            ),
        ],
    )
    sk_a = _stub_skill(tmp_path, "a")
    sk_b = _stub_skill(tmp_path, "b")
    rt = FakeSkillRuntime({"a": {"greeting": "hi"}, "b": {"done": True}})

    runner = DAGRunner(
        workflow=wf,
        skills_by_name={"a": sk_a, "b": sk_b},
        runtime=rt,
        workdir=tmp_path,
        inputs={},
    )
    outputs = runner.run()

    assert [c[0] for c in rt.calls] == ["a", "b"]
    assert rt.calls[1][1] == {"prev": "hi"}
    assert outputs["a"] == {"greeting": "hi"}
    assert outputs["b"] == {"done": True}


def test_runner_passes_event_recorder_and_step_id_to_skill_context(tmp_path: Path) -> None:
    seen: list[dict] = []
    events: list[dict] = []

    class R(SkillRuntime):
        def invoke(self, skill: Skill, ctx: SkillContext) -> dict[str, Any]:
            seen.append(dict(ctx.extras))
            ctx.extras["run_event_recorder"](
                "custom",
                {"step": ctx.extras["current_step_id"]},
            )
            return {"ok": True}

    wf = Workflow(name="w", steps=[WorkflowStep(id="s1", skill="s1", inputs={})])
    skill = _stub_skill(tmp_path, "s1")
    runner = DAGRunner(
        workflow=wf,
        skills_by_name={"s1": skill},
        runtime=R(),
        workdir=tmp_path,
        inputs={},
        run_event_recorder=lambda event_type, payload: events.append({
            "event_type": event_type,
            "payload": payload,
        }),
    )

    runner.run()

    assert seen[0]["current_step_id"] == "s1"
    assert events == [{"event_type": "custom", "payload": {"step": "s1"}}]


def test_top_level_input_reference(tmp_path: Path) -> None:
    wf = Workflow(
        name="w",
        steps=[WorkflowStep(id="a", skill="a", inputs={"x": "$inputs.greeting"})],
    )
    sk_a = _stub_skill(tmp_path, "a")
    rt = FakeSkillRuntime({"a": {}})
    runner = DAGRunner(
        workflow=wf, skills_by_name={"a": sk_a}, runtime=rt,
        workdir=tmp_path, inputs={"greeting": "yo"},
    )
    runner.run()
    assert rt.calls[0][1] == {"x": "yo"}


def test_optional_input_reference_resolves_to_none_when_omitted(tmp_path: Path) -> None:
    wf = Workflow(
        name="w",
        inputs={"delivery_stack_id": InputSpec(type="string", required=False)},
        steps=[WorkflowStep(id="a", skill="a", inputs={"stack": "$inputs.delivery_stack_id"})],
    )
    sk_a = _stub_skill(tmp_path, "a")
    rt = FakeSkillRuntime({"a": {}})

    runner = DAGRunner(
        workflow=wf,
        skills_by_name={"a": sk_a},
        runtime=rt,
        workdir=tmp_path,
        inputs={},
    )

    runner.run()

    assert rt.calls[0][1] == {"stack": None}


def test_missing_skill_fails(tmp_path: Path) -> None:
    wf = Workflow(name="w", steps=[WorkflowStep(id="a", skill="ghost")])
    rt = FakeSkillRuntime({})
    with pytest.raises(DAGRunnerError, match="unknown skill"):
        DAGRunner(
            workflow=wf, skills_by_name={}, runtime=rt, workdir=tmp_path, inputs={}
        ).run()
