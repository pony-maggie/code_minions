"""Tests for DAG Runner."""
from __future__ import annotations

import sys
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


def _stub_skill(tmp: Path, name: str, *, role: str | None = None) -> Skill:
    d = tmp / name
    d.mkdir()
    role_line = f"role: {role}\n" if role else ""
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\n{role_line}inputs: {{}}\noutputs: {{}}\n---\n\n# {name}\n"
    )
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


def test_when_false_skips_step_without_invoking_skill(tmp_path: Path) -> None:
    wf = Workflow(
        name="w",
        steps=[
            WorkflowStep(id="acceptance", skill="acceptance"),
            WorkflowStep(
                id="publish",
                skill="publish",
                depends_on=["acceptance"],
                when="$steps.acceptance.output.accepted",
            ),
        ],
    )
    acceptance = _stub_skill(tmp_path, "acceptance")
    publish = _stub_skill(tmp_path, "publish")
    rt = FakeSkillRuntime({
        "acceptance": {"accepted": False, "blockers": [{"code": "missing"}]},
        "publish": {"pushed": True},
    })

    runner = DAGRunner(
        workflow=wf,
        skills_by_name={"acceptance": acceptance, "publish": publish},
        runtime=rt,
        workdir=tmp_path,
        inputs={},
    )

    outputs = runner.run()

    assert [call[0] for call in rt.calls] == ["acceptance"]
    assert outputs["publish"] == {
        "__code_minions_skipped__": True,
        "reason": "when condition evaluated false",
    }


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


def test_blocker_command_sensor_fails_step_after_skill_success(tmp_path: Path) -> None:
    wf = Workflow(
        name="w",
        sensors={
            "typecheck": {
                "type": "command",
                "command": f"{sys.executable} -c \"import sys; print('bad'); sys.exit(2)\"",
                "severity": "blocker",
            },
        },
        steps=[
            WorkflowStep(id="build", skill="build", sensors=["typecheck"]),
            WorkflowStep(id="publish", skill="publish", depends_on=["build"]),
        ],
    )
    build = _stub_skill(tmp_path, "build")
    publish = _stub_skill(tmp_path, "publish")
    rt = FakeSkillRuntime({"build": {"ok": True}, "publish": {"pushed": True}})
    events: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
    runner = DAGRunner(
        workflow=wf,
        skills_by_name={"build": build, "publish": publish},
        runtime=rt,
        workdir=tmp_path,
        inputs={},
        observer=lambda step_id, status, output, error, _detail=None: events.append(
            (step_id, status, output, error)
        ),
    )

    with pytest.raises(DAGRunnerError, match="sensor typecheck failed"):
        runner.run()

    assert [call[0] for call in rt.calls] == ["build"]
    failed = events[-1]
    assert failed[0] == "build"
    assert failed[1] == "failed"
    assert failed[2] is not None
    assert failed[2]["ok"] is True
    findings = failed[2]["gate_findings"]
    assert findings[0]["code"] == "sensor-typecheck"
    assert findings[0]["severity"] == "blocker"
    assert "bad" in findings[0]["message"]


def test_warning_command_sensor_records_finding_without_failing_step(tmp_path: Path) -> None:
    wf = Workflow(
        name="w",
        sensors={
            "audit": {
                "type": "command",
                "command": f"{sys.executable} -c \"import sys; print('warn'); sys.exit(1)\"",
                "severity": "warning",
            },
        },
        steps=[WorkflowStep(id="build", skill="build", sensors=["audit"])],
    )
    build = _stub_skill(tmp_path, "build")
    rt = FakeSkillRuntime({"build": {"ok": True}})
    events: list[tuple[str, str, dict[str, Any] | None, str | None]] = []

    outputs = DAGRunner(
        workflow=wf,
        skills_by_name={"build": build},
        runtime=rt,
        workdir=tmp_path,
        inputs={},
        observer=lambda step_id, status, output, error, _detail=None: events.append(
            (step_id, status, output, error)
        ),
    ).run()

    assert outputs["build"] == {"ok": True}
    success = events[-1]
    assert success[0] == "build"
    assert success[1] == "success"
    assert success[2] is not None
    assert success[2]["gate_findings"][0]["severity"] == "warning"
    assert "warn" in success[2]["gate_findings"][0]["message"]


def test_runner_uses_role_specific_llm_backend_for_skill(tmp_path: Path) -> None:
    seen: list[Any] = []

    class R(SkillRuntime):
        def invoke(self, skill: Skill, ctx: SkillContext) -> dict[str, Any]:
            seen.append(ctx.llm)
            return {"ok": True}

    default_llm = object()
    reviewer_llm = object()
    wf = Workflow(name="w", steps=[WorkflowStep(id="review", skill="review")])
    skill = _stub_skill(tmp_path, "review", role="reviewer")

    DAGRunner(
        workflow=wf,
        skills_by_name={"review": skill},
        runtime=R(),
        workdir=tmp_path,
        inputs={},
        llm_backend=default_llm,
        role_llm_backends={"reviewer": reviewer_llm},
    ).run()

    assert seen == [reviewer_llm]


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
