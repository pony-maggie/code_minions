"""Tests for for_each dynamic fan-out in DAGRunner."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from code_minions.engine.dag_runner import DAGRunner, _for_each_item_hash
from code_minions.engine.skill import Skill, load_skill
from code_minions.engine.skill_runtime import SkillRuntime
from code_minions.engine.workflow import Workflow, WorkflowStep


class CaptureRuntime(SkillRuntime):
    def __init__(self):
        self.seen: list[dict[str, Any]] = []
    def invoke(self, skill, ctx):
        self.seen.append(dict(ctx.inputs))
        return {"echo": ctx.inputs.get("item")}


def _stub(tmp: Path, name: str) -> Skill:
    d = tmp / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\nname: {name}\ninputs: {{}}\noutputs: {{}}\n---\n\n# {name}\n")
    return load_skill(d)


def test_for_each_fan_out(tmp_path: Path):
    wf = Workflow(name="w", steps=[
        WorkflowStep(id="upstream", skill="up", inputs={}),
        WorkflowStep(
            id="each", skill="each",
            for_each="$steps.upstream.output.items",
            **{"as": "item"},
            inputs={"item": "$item"},
            depends_on=["upstream"],
        ),
    ])
    up = _stub(tmp_path, "up")
    each = _stub(tmp_path, "each")
    class UpstreamRuntime(CaptureRuntime):
        def invoke(self, skill, ctx):
            if skill.name == "up":
                return {"items": ["a", "b", "c"]}
            return super().invoke(skill, ctx)
    rt = UpstreamRuntime()
    runner = DAGRunner(
        workflow=wf, skills_by_name={"up": up, "each": each},
        runtime=rt, workdir=tmp_path, inputs={},
    )
    outputs = runner.run()
    assert outputs["each"]["items"] == [{"echo": "a"}, {"echo": "b"}, {"echo": "c"}]
    assert [c["item"] for c in rt.seen if "item" in c] == ["a", "b", "c"]


def test_for_each_empty_list_produces_empty_output(tmp_path: Path):
    wf = Workflow(name="w", steps=[
        WorkflowStep(id="up", skill="up"),
        WorkflowStep(
            id="each", skill="each",
            for_each="$steps.up.output.items",
            **{"as": "item"}, inputs={"item": "$item"},
            depends_on=["up"],
        ),
    ])
    up = _stub(tmp_path, "up")
    each = _stub(tmp_path, "each")
    class R(SkillRuntime):
        def invoke(self, s, c): return {"items": []} if s.name == "up" else {}
    runner = DAGRunner(workflow=wf, skills_by_name={"up": up, "each": each},
                      runtime=R(), workdir=tmp_path, inputs={})
    out = runner.run()
    assert out["each"]["items"] == []


def test_for_each_observer_emits_iter_step_ids(tmp_path: Path):
    emitted: list[tuple[str, str]] = []
    wf = Workflow(name="w", steps=[
        WorkflowStep(id="up", skill="up"),
        WorkflowStep(id="each", skill="each",
                     for_each="$steps.up.output.items",
                     **{"as": "x"}, inputs={"x": "$x"}, depends_on=["up"]),
    ])
    up = _stub(tmp_path, "up")
    each = _stub(tmp_path, "each")
    class R(SkillRuntime):
        def invoke(self, s, c): return {"items": [1, 2]} if s.name == "up" else {"ok": True}
    runner = DAGRunner(
        workflow=wf, skills_by_name={"up": up, "each": each},
        runtime=R(), workdir=tmp_path, inputs={},
        observer=lambda sid, st, o, e: emitted.append((sid, st)),
    )
    runner.run()
    ids = [e[0] for e in emitted]
    assert "each" in ids
    assert "each[0]" in ids
    assert "each[1]" in ids


def test_for_each_observer_emits_item_detail(tmp_path: Path):
    emitted: list[tuple[str, str, str | None]] = []
    wf = Workflow(name="w", steps=[
        WorkflowStep(id="up", skill="up"),
        WorkflowStep(id="each", skill="each",
                     for_each="$steps.up.output.items",
                     **{"as": "ticket"}, inputs={"ticket": "$ticket"}, depends_on=["up"]),
    ])
    up = _stub(tmp_path, "up")
    each = _stub(tmp_path, "each")

    class R(SkillRuntime):
        def invoke(self, s, c):
            if s.name == "up":
                return {"items": [{"id": "T17", "title": "Add history search"}]}
            return {"ok": True}

    runner = DAGRunner(
        workflow=wf, skills_by_name={"up": up, "each": each},
        runtime=R(), workdir=tmp_path, inputs={},
        observer=lambda sid, st, o, e, detail=None: emitted.append((sid, st, detail)),
    )

    runner.run()

    assert ("each[0]", "running", "T17: Add history search") in emitted
    assert ("each[0]", "success", "T17: Add history search") in emitted


def test_for_each_parent_running_emits_item_summary(tmp_path: Path):
    emitted: list[tuple[str, str, str | None]] = []
    wf = Workflow(name="w", steps=[
        WorkflowStep(id="up", skill="up"),
        WorkflowStep(id="each", skill="each",
                     for_each="$steps.up.output.items",
                     **{"as": "ticket"}, inputs={"ticket": "$ticket"}, depends_on=["up"]),
    ])
    up = _stub(tmp_path, "up")
    each = _stub(tmp_path, "each")

    class R(SkillRuntime):
        def invoke(self, s, c):
            if s.name == "up":
                return {"items": [
                    {"id": "T1", "title": "Add parser"},
                    {"id": "T2", "title": "Add history"},
                ]}
            return {"ok": True}

    runner = DAGRunner(
        workflow=wf, skills_by_name={"up": up, "each": each},
        runtime=R(), workdir=tmp_path, inputs={},
        observer=lambda sid, st, o, e, detail=None: emitted.append((sid, st, detail)),
    )

    runner.run()

    detail = next(d for sid, status, d in emitted if sid == "each" and status == "running")
    assert detail is not None
    assert "2 items" in detail
    assert "estimated LLM calls" in detail
    assert "[0] T1: Add parser" in detail
    assert "[1] T2: Add history" in detail


def test_for_each_resume_skips_successful_iteration_outputs(tmp_path: Path):
    wf = Workflow(name="w", steps=[
        WorkflowStep(id="up", skill="up"),
        WorkflowStep(
            id="each", skill="each",
            for_each="$steps.up.output.items",
            **{"as": "item"},
            inputs={"item": "$item"},
            depends_on=["up"],
        ),
    ])
    up = _stub(tmp_path, "up")
    each = _stub(tmp_path, "each")

    class R(CaptureRuntime):
        def invoke(self, skill, ctx):
            if skill.name == "up":
                raise AssertionError("upstream step should be preloaded")
            return super().invoke(skill, ctx)

    rt = R()
    runner = DAGRunner(
        workflow=wf,
        skills_by_name={"up": up, "each": each},
        runtime=rt,
        workdir=tmp_path,
        inputs={},
        preloaded_outputs={
            "up": {"items": ["a", "b"]},
            "each[0]": {"echo": "a"},
        },
    )

    outputs = runner.run()

    assert outputs["each"]["items"] == [{"echo": "a"}, {"echo": "b"}]
    assert [c["item"] for c in rt.seen] == ["b"]


def test_for_each_resume_reruns_iteration_when_item_hash_changed(tmp_path: Path):
    wf = Workflow(name="w", steps=[
        WorkflowStep(id="up", skill="up"),
        WorkflowStep(
            id="each", skill="each",
            for_each="$steps.up.output.items",
            **{"as": "item"},
            inputs={"item": "$item"},
            depends_on=["up"],
        ),
    ])
    up = _stub(tmp_path, "up")
    each = _stub(tmp_path, "each")

    class R(CaptureRuntime):
        def invoke(self, skill, ctx):
            if skill.name == "up":
                raise AssertionError("upstream step should be preloaded")
            return super().invoke(skill, ctx)

    rt = R()
    runner = DAGRunner(
        workflow=wf,
        skills_by_name={"up": up, "each": each},
        runtime=rt,
        workdir=tmp_path,
        inputs={},
        preloaded_outputs={
            "up": {"items": ["changed", "b"]},
            "each[0]": {
                "echo": "a",
                "__code_minions_for_each_item_hash": _for_each_item_hash("a"),
            },
        },
    )

    outputs = runner.run()

    assert outputs["each"]["items"] == [{"echo": "changed"}, {"echo": "b"}]
    assert [c["item"] for c in rt.seen] == ["changed", "b"]
