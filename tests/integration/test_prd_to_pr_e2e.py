"""M4 smoke tests for the prd-to-pr workflow and its skills.

Full scripted-LLM E2E of prd-to-pr isn't done here: it would require pre-computing
LLM responses for every turn across 6 skills (including implement-with-tdd's inner
Coder↔Reviewer double loop + shell pytest subprocesses + jira MCP). Instead:

1) structural validation of the workflow YAML
2) skill availability check (Engine can load every skill the workflow references)
3) first-step smoke: parse-prd runs end-to-end with FakeLLM + built-in Read
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from code_minions.engine.engine import Engine
from code_minions.engine.skill_runtime import SkillRuntime
from code_minions.engine.workflow import load_workflow
from code_minions.llm.types import Message, Response, ToolCall, Usage


def _builtin_root() -> Path:
    import code_minions
    return Path(code_minions.__file__).resolve().parent / "builtin"


def _fixture_prd() -> Path:
    return Path(__file__).parent.parent / "fixtures" / "sample-prd.md"


def test_prd_to_pr_yaml_loads() -> None:
    """Structural: the default workflow parses without errors and lists expected steps."""
    wf = load_workflow(_builtin_root() / "workflows" / "prd-to-pr.yaml")
    assert wf.name == "prd-to-pr"
    assert set(wf.inputs) == {"prd", "delivery_stack_id", "project_key", "epic_title"}
    assert wf.inputs["delivery_stack_id"].required is False
    step_ids = [s.id for s in wf.steps]
    assert step_ids == ["parse", "plan", "tickets", "implement", "browser_acceptance", "acceptance", "report", "open_pr"]
    assert wf.steps[0].inputs["delivery_stack_id"] == "$inputs.delivery_stack_id"


def test_prd_to_commit_yaml_loads() -> None:
    """Structural: built-in PRD-to-commit stops before Jira/GitHub integration steps."""
    wf = load_workflow(_builtin_root() / "workflows" / "prd-to-commit.yaml")

    assert wf.name == "prd-to-commit"
    assert set(wf.inputs) == {"prd", "delivery_stack_id"}
    assert wf.inputs["prd"].required is True
    assert wf.inputs["delivery_stack_id"].required is False
    step_ids = [s.id for s in wf.steps]
    assert step_ids == ["parse", "plan", "implement", "browser_acceptance", "acceptance", "report"]

    implement = wf.steps[2]
    assert implement.skill == "implement-with-tdd"
    assert implement.for_each == "$steps.plan.output.tasks"
    assert implement.as_ == "ticket"

    browser_acceptance = wf.steps[3]
    assert browser_acceptance.skill == "web-ui-acceptance-review"
    assert browser_acceptance.depends_on == ["implement"]

    acceptance = wf.steps[4]
    assert acceptance.skill == "product-acceptance-review"
    assert acceptance.depends_on == ["browser_acceptance"]
    assert acceptance.inputs["browser_acceptance_output"] == "$steps.browser_acceptance.output"


def test_all_referenced_skills_are_findable() -> None:
    """Every skill named in the workflow can be loaded by Engine._load_skills_for."""
    import subprocess
    import tempfile
    # Construct a bare Engine; it only needs to find skills.
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "--allow-empty", "-q", "-m", "init"], cwd=tmp, check=True)
        engine = Engine(
            project_root=tmp,
            skill_search_paths=[_builtin_root() / "skills"],
            workflow_search_paths=[_builtin_root() / "workflows"],
            runtime=SkillRuntime(),
        )
        wf = load_workflow(_builtin_root() / "workflows" / "prd-to-pr.yaml")
        # Access the private loader — this is intentional for validation tests.
        skills = engine._load_skills_for(wf)  # noqa: SLF001
        assert set(skills.keys()) == {
            "parse-prd", "plan-tasks", "create-jira-tickets",
            "implement-with-tdd", "web-ui-acceptance-review", "product-acceptance-review",
            "compile-report", "open-github-pr",
        }

        commit_wf = load_workflow(_builtin_root() / "workflows" / "prd-to-commit.yaml")
        commit_skills = engine._load_skills_for(commit_wf)  # noqa: SLF001
        assert set(commit_skills.keys()) == {
            "parse-prd",
            "plan-tasks",
            "implement-with-tdd",
            "web-ui-acceptance-review",
            "product-acceptance-review",
            "compile-report",
        }


def test_parse_prd_first_step_smoke(tmp_git_repo: Path) -> None:
    """Run parse-prd end-to-end with scripted FakeLLM + built-in Read.

    We invoke Engine.start_run on a tiny one-step workflow that only calls parse-prd.
    This validates the LLM + built-in local tool wiring with a real skill, without attempting to
    script the entire prd-to-pr flow.
    """
    # Seed the PRD inside the repo so Engine's worktree has access to it.
    repo = tmp_git_repo.resolve()
    prd_text = _fixture_prd().read_text()
    (repo / "sample-prd.md").write_text(prd_text)
    import subprocess
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)

    # Reuse FakeLLM from existing unit tests
    from tests.unit.test_skill_runtime_llm import FakeLLM

    # Script: (1) read the file via built-in Read, (2) reply with structured JSON.
    tc = ToolCall(
        id="t1",
        name="Read",
        arguments={"path": "sample-prd.md"},
    )
    turn1 = Response(
        message=Message(role="assistant", tool_calls=[tc]),
        usage=Usage(1, 1), model="fake", stop_reason="tool_use",
    )
    parsed_json = (
        '{"goal":"Add add(a,b) function","users":["Library consumers"],'
        '"features":[{"name":"add","description":"a+b","acceptance_criteria":["add(2,3)==5"]}],'
        '"non_functional":{},"constraints":[],"questions":[]}'
    )
    turn2 = Response(
        message=Message(role="assistant", content=parsed_json),
        usage=Usage(1, 1), model="fake", stop_reason="end_turn",
    )
    llm = FakeLLM([turn1, turn2])

    # Ad-hoc single-step workflow + skills root
    skills_root = repo / "_skills"
    skills_root.mkdir()
    # symlink parse-prd in from builtins
    parse_src = _builtin_root() / "skills" / "parse-prd"
    parse_dst = skills_root / "parse-prd"
    for item in parse_src.iterdir():
        shutil.copy(item, parse_dst / item.name) if parse_dst.exists() else None
    if not parse_dst.exists():
        shutil.copytree(parse_src, parse_dst)

    wf_root = repo / "_workflows"
    wf_root.mkdir()
    (wf_root / "only-parse.yaml").write_text(
        "name: only-parse\n"
        "inputs:\n  prd: {type: string, required: true}\n"
        "steps:\n"
        "  - id: parse\n"
        "    skill: parse-prd\n"
        "    inputs: {prd_file: $inputs.prd}\n"
    )

    engine = Engine(
        project_root=repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[wf_root],
        runtime=SkillRuntime(),
        llm_backend=llm,
        mcp_pool=None,
    )
    run_id = engine.start_run(workflow="only-parse", inputs={"prd": "sample-prd.md"})

    state = engine.get_run_state(run_id)
    assert state["status"] == "success", f"unexpected state: {state}"
    out = json.loads(state["steps"][0]["output_json"])
    assert out["goal"].startswith("Add")
    assert out["features"][0]["name"] == "add"
