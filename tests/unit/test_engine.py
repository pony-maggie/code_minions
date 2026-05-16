"""Tests for Engine (top-level orchestration)."""
from __future__ import annotations

from pathlib import Path

import pytest

from code_minions.engine.engine import Engine, EngineError
from code_minions.engine.skill_runtime import SkillRuntime


def _write_hello_skill(skills_root: Path) -> None:
    d = skills_root / "hello"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        """---
name: hello
description: Say hello
entrypoint-script: scripts/run.py
inputs:
  name: {type: string, required: true}
outputs:
  greeting: {type: string}
---

# hello
"""
    )
    (d / "scripts" / "run.py").write_text(
        "def run(ctx):\n"
        "    return {'greeting': f\"hello {ctx.inputs['name']}\"}\n"
    )


def _write_workflow(workflows_root: Path) -> None:
    workflows_root.mkdir(parents=True, exist_ok=True)
    (workflows_root / "hello.yaml").write_text(
        """
name: hello
inputs:
  who: {type: string, required: true}
steps:
  - id: greet
    skill: hello
    inputs:
      name: $inputs.who
"""
    )


def test_start_run_success(tmp_git_repo: Path) -> None:
    skills_root = tmp_git_repo / "skills"
    workflows_root = tmp_git_repo / "workflows"
    _write_hello_skill(skills_root)
    _write_workflow(workflows_root)

    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[workflows_root],
        runtime=SkillRuntime(),
    )
    run_id = engine.start_run(workflow="hello", inputs={"who": "world"})

    state = engine.get_run_state(run_id)
    assert state["status"] == "success"
    steps = state["steps"]
    assert len(steps) == 1
    assert steps[0]["status"] == "success"

    wt = tmp_git_repo / ".devflow" / "runs" / run_id / "worktree"
    assert wt.exists()


def test_successful_run_updates_project_memory(tmp_git_repo: Path) -> None:
    skills_root = tmp_git_repo / "skills"
    workflows_root = tmp_git_repo / "workflows"
    _write_hello_skill(skills_root)
    _write_workflow(workflows_root)

    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[workflows_root],
        runtime=SkillRuntime(),
    )
    run_id = engine.start_run(workflow="hello", inputs={"who": "world"})

    memory = (tmp_git_repo / ".devflow" / "memory.md").read_text()
    assert "code_minions Project Memory" in memory
    assert f"Run `{run_id}`" in memory
    assert "workflow `hello`" in memory


def test_start_run_unknown_workflow_fails(tmp_git_repo: Path) -> None:
    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[],
        workflow_search_paths=[tmp_git_repo / "workflows"],
        runtime=SkillRuntime(),
    )
    (tmp_git_repo / "workflows").mkdir()
    with pytest.raises(EngineError, match="workflow not found"):
        engine.start_run(workflow="nope", inputs={})


def test_start_run_marks_failed_on_skill_error(tmp_git_repo: Path) -> None:
    skills_root = tmp_git_repo / "skills"
    d = skills_root / "boom"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: boom\nentrypoint-script: scripts/run.py\ninputs: {}\noutputs: {}\n---\n\n# boom\n"
    )
    (d / "scripts" / "run.py").write_text("def run(ctx):\n    raise RuntimeError('boom')\n")

    workflows_root = tmp_git_repo / "workflows"
    workflows_root.mkdir()
    (workflows_root / "b.yaml").write_text(
        "name: b\nsteps:\n  - id: s\n    skill: boom\n"
    )

    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[workflows_root],
        runtime=SkillRuntime(),
    )
    run_id = engine.start_run(workflow="b", inputs={})
    state = engine.get_run_state(run_id)
    assert state["status"] == "failed"
    assert "boom" in state["steps"][0]["error"]
    events = engine._store.list_run_events(run_id)  # noqa: SLF001 - test durable run diagnostics
    classified = [event for event in events if event["event_type"] == "workflow_failure_classified"]
    assert classified
    assert classified[-1]["payload"]["classification"] == "workflow_systemic"


def test_start_run_marks_completed_with_issues_when_step_is_skipped_by_gate(
    tmp_git_repo: Path,
) -> None:
    skills_root = tmp_git_repo / "skills"
    for name, body in {
        "accept": "def run(ctx):\n    return {'accepted': False}\n",
        "publish": "def run(ctx):\n    raise AssertionError('must not publish')\n",
    }.items():
        d = skills_root / name
        (d / "scripts").mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\nentrypoint-script: scripts/run.py\ninputs: {{}}\noutputs: {{}}\n---\n\n# {name}\n"
        )
        (d / "scripts" / "run.py").write_text(body)

    workflows_root = tmp_git_repo / "workflows"
    workflows_root.mkdir()
    (workflows_root / "gated.yaml").write_text(
        """
name: gated
steps:
  - id: accept
    skill: accept
  - id: publish
    skill: publish
    depends_on: [accept]
    when: $steps.accept.output.accepted
"""
    )
    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[workflows_root],
        runtime=SkillRuntime(),
    )

    run_id = engine.start_run(workflow="gated", inputs={})
    state = engine.get_run_state(run_id)

    assert state["status"] == "completed_with_issues"
    statuses = {step["step_id"]: step["status"] for step in state["steps"]}
    assert statuses == {"accept": "success", "publish": "skipped"}


def test_start_run_marks_completed_with_issues_when_acceptance_rejects_without_skips(
    tmp_git_repo: Path,
) -> None:
    skills_root = tmp_git_repo / "skills"
    for name, body in {
        "accept": "def run(ctx):\n    return {'accepted': False, 'blockers': [{'code': 'browser'}]}\n",
        "report": "def run(ctx):\n    return {'report_path': 'report.md'}\n",
    }.items():
        d = skills_root / name
        (d / "scripts").mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\nentrypoint-script: scripts/run.py\ninputs: {{}}\noutputs: {{}}\n---\n\n# {name}\n"
        )
        (d / "scripts" / "run.py").write_text(body)

    workflows_root = tmp_git_repo / "workflows"
    workflows_root.mkdir()
    (workflows_root / "reported.yaml").write_text(
        """
name: reported
steps:
  - id: accept
    skill: accept
  - id: report
    skill: report
    depends_on: [accept]
"""
    )
    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[workflows_root],
        runtime=SkillRuntime(),
    )

    run_id = engine.start_run(workflow="reported", inputs={})
    state = engine.get_run_state(run_id)

    assert state["status"] == "completed_with_issues"
    statuses = {step["step_id"]: step["status"] for step in state["steps"]}
    assert statuses == {"accept": "success", "report": "success"}


def test_start_run_marks_needs_clarification_when_prd_has_questions(
    tmp_git_repo: Path,
) -> None:
    skills_root = tmp_git_repo / "skills"
    parse_dir = skills_root / "parse"
    (parse_dir / "scripts").mkdir(parents=True)
    (parse_dir / "SKILL.md").write_text(
        "---\nname: parse\nentrypoint-script: scripts/run.py\ninputs: {}\noutputs: {}\n---\n\n# parse\n"
    )
    (parse_dir / "scripts" / "run.py").write_text(
        "def run(ctx):\n"
        "    return {'questions': ['Which platform?'], 'delivery_profile': {}}\n"
    )
    plan_dir = skills_root / "plan-tasks"
    plan_dir.mkdir()
    (plan_dir / "SKILL.md").write_text(
        "---\nname: plan-tasks\ninputs: {structured_prd: {type: object, required: true}}\noutputs: {tasks: {type: array}}\n---\n\n# plan\n"
    )

    workflows_root = tmp_git_repo / "workflows"
    workflows_root.mkdir()
    (workflows_root / "clarify.yaml").write_text(
        """
name: clarify
steps:
  - id: parse
    skill: parse
  - id: plan
    skill: plan-tasks
    inputs:
      structured_prd: $steps.parse.output
    depends_on: [parse]
"""
    )
    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[workflows_root],
        runtime=SkillRuntime(),
    )

    run_id = engine.start_run(workflow="clarify", inputs={})
    state = engine.get_run_state(run_id)

    assert state["status"] == "needs_clarification"
    plan_step = next(step for step in state["steps"] if step["step_id"] == "plan")
    assert plan_step["status"] == "failed"
    assert "Which platform?" in plan_step["output_json"]


def test_start_run_failed_step_preserves_partial_output(tmp_git_repo: Path) -> None:
    skills_root = tmp_git_repo / "skills"
    d = skills_root / "publish"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: publish\nentrypoint-script: scripts/run.py\ninputs: {}\noutputs: {}\n---\n\n# publish\n"
    )
    (d / "scripts" / "run.py").write_text(
        "from code_minions.engine.skill_runtime import SkillExecutionError\n"
        "def run(ctx):\n"
        "    raise SkillExecutionError('publish failed', {'pushed': True, 'pr_url': ''})\n"
    )

    workflows_root = tmp_git_repo / "workflows"
    workflows_root.mkdir()
    (workflows_root / "p.yaml").write_text(
        "name: p\nsteps:\n  - id: publish\n    skill: publish\n"
    )

    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[workflows_root],
        runtime=SkillRuntime(),
    )
    run_id = engine.start_run(workflow="p", inputs={})
    state = engine.get_run_state(run_id)
    assert state["status"] == "failed"
    assert state["steps"][0]["status"] == "failed"
    assert state["steps"][0]["output_json"] == '{"pushed": true, "pr_url": ""}'
    assert "publish failed" in state["steps"][0]["error"]


def test_start_run_marks_needs_human_from_skill_execution_error(tmp_git_repo: Path) -> None:
    skills_root = tmp_git_repo / "skills"
    d = skills_root / "implement"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: implement\nentrypoint-script: scripts/run.py\ninputs: {}\noutputs: {}\n---\n\n# implement\n"
    )
    (d / "scripts" / "run.py").write_text(
        "from code_minions.engine.skill_runtime import SkillExecutionError\n"
        "def run(ctx):\n"
        "    raise SkillExecutionError('needs human', {'commit_sha': ''}, run_status='needs_human')\n"
    )
    workflows_root = tmp_git_repo / "workflows"
    workflows_root.mkdir()
    (workflows_root / "w.yaml").write_text("name: w\nsteps:\n  - id: implement\n    skill: implement\n")
    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[workflows_root],
        runtime=SkillRuntime(),
    )

    run_id = engine.start_run(workflow="w", inputs={})
    state = engine.get_run_state(run_id)

    assert state["status"] == "needs_human"
    assert state["steps"][0]["status"] == "failed"
    assert state["steps"][0]["output_json"] == '{"commit_sha": ""}'


def test_start_run_marks_failed_on_worktree_error(tmp_git_repo: Path) -> None:
    """If worktree creation fails (e.g. path already exists), run is marked FAILED."""
    skills_root = tmp_git_repo / "skills"
    _write_hello_skill(skills_root)
    workflows_root = tmp_git_repo / "workflows"
    _write_workflow(workflows_root)

    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[workflows_root],
        runtime=SkillRuntime(),
    )

    # Pre-create a collision so WorktreeManager.create raises:
    # we need to predict the run_id; easier: monkey-patch the manager to always fail.
    from code_minions.git.worktree import WorktreeError
    engine._wt_mgr.create = lambda **kw: (_ for _ in ()).throw(  # type: ignore[method-assign]
        WorktreeError("simulated failure")
    )

    run_id = engine.start_run(workflow="hello", inputs={"who": "x"})
    state = engine.get_run_state(run_id)
    assert state["status"] == "failed"
    assert len(state["steps"]) == 1
    assert state["steps"][0]["step_id"] == "__setup__"
    assert "simulated failure" in state["steps"][0]["error"]


def test_engine_passes_llm_to_context(tmp_git_repo: Path) -> None:
    """With LLM wired, a handler-less skill can be invoked via LLM path."""
    from code_minions.llm.types import Message, Response, Usage

    class FakeLLM:
        name = "fake"
        def supports_tool_use(self) -> bool: return True
        def chat(self, messages, tools=None, model=None, temperature=0.2, max_tokens=4096):
            return Response(
                message=Message(role="assistant", content='{"done": true}'),
                usage=Usage(input_tokens=1, output_tokens=1),
                model="fake", stop_reason="end_turn",
            )

    # Create a handler-less skill + workflow
    skills_root = tmp_git_repo / "skills"
    d = skills_root / "no-handler"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: no-handler\ninputs: {}\noutputs: {done: {type: boolean}}\n---\n\n# no-handler\n"
    )
    workflows_root = tmp_git_repo / "workflows"
    workflows_root.mkdir()
    (workflows_root / "t.yaml").write_text(
        "name: t\nsteps:\n  - id: a\n    skill: no-handler\n"
    )

    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[workflows_root],
        runtime=SkillRuntime(),
        llm_backend=FakeLLM(),  # type: ignore[arg-type]
        mcp_pool=None,
    )
    run_id = engine.start_run(workflow="t", inputs={})
    state = engine.get_run_state(run_id)
    assert state["status"] == "success"


def test_engine_shares_skill_cache_across_runs(tmp_git_repo: Path) -> None:
    """Cacheable LLM skills reuse output across separate runs in one project."""
    from code_minions.llm.types import Message, Response, Usage

    class FakeLLM:
        name = "fake"

        def __init__(self) -> None:
            self.calls = 0

        def supports_tool_use(self) -> bool:
            return True

        def chat(self, messages, tools=None, model=None, temperature=0.2, max_tokens=4096):
            self.calls += 1
            return Response(
                message=Message(role="assistant", content='{"done": true}'),
                usage=Usage(input_tokens=1, output_tokens=1),
                model="fake",
                stop_reason="end_turn",
            )

    skills_root = tmp_git_repo / "skills"
    d = skills_root / "cacheable"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        """---
name: cacheable
inputs:
  value: {type: string, required: true}
outputs:
  done: {type: boolean}
policies:
  cache: true
---

# cacheable
"""
    )
    workflows_root = tmp_git_repo / "workflows"
    workflows_root.mkdir()
    (workflows_root / "c.yaml").write_text(
        "name: c\nsteps:\n  - id: s\n    skill: cacheable\n    inputs:\n      value: $inputs.value\n"
    )

    llm = FakeLLM()
    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[workflows_root],
        runtime=SkillRuntime(),
        llm_backend=llm,  # type: ignore[arg-type]
    )

    first = engine.start_run(workflow="c", inputs={"value": "same"})
    second = engine.start_run(workflow="c", inputs={"value": "same"})

    assert engine.get_run_state(first)["status"] == "success"
    assert engine.get_run_state(second)["status"] == "success"
    assert llm.calls == 1


def test_start_run_records_llm_identity(tmp_git_repo: Path) -> None:
    class FakeLLM:
        name = "fake"
        _provider = "minimax"
        _default_model = "MiniMax-M2.7"

        def supports_tool_use(self) -> bool:
            return True

    skills_root = tmp_git_repo / "skills"
    _write_hello_skill(skills_root)
    workflows_root = tmp_git_repo / "workflows"
    _write_workflow(workflows_root)

    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[workflows_root],
        runtime=SkillRuntime(),
        llm_backend=FakeLLM(),  # type: ignore[arg-type]
    )

    run_id = engine.start_run(workflow="hello", inputs={"who": "world"})
    state = engine.get_run_state(run_id)

    assert state["llm"] == "minimax/MiniMax-M2.7"


def test_execute_run_skips_create(tmp_git_repo: Path) -> None:
    """Engine.execute_run drives an already-created run row to completion."""
    skills_root = tmp_git_repo / "skills"
    _write_hello_skill(skills_root)
    workflows_root = tmp_git_repo / "workflows"
    _write_workflow(workflows_root)

    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[workflows_root],
        runtime=SkillRuntime(),
    )
    # Create row manually, then drive
    run_id = engine._store.create_run(workflow="hello", inputs={"who": "x"})
    result_id = engine.execute_run(run_id, "hello", {"who": "x"})
    assert result_id == run_id
    state = engine.get_run_state(run_id)
    assert state["status"] == "success"

    # Verify no duplicate row was created
    all_runs = engine._store.list_runs(limit=10)
    assert len([r for r in all_runs if r["id"] == run_id]) == 1
