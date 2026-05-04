"""Verify a skill's entrypoint can invoke another skill via ctx.invoke_skill."""
from __future__ import annotations

from pathlib import Path

from code_minions.engine.engine import Engine
from code_minions.engine.skill_runtime import SkillRuntime


def _seed_skill(root: Path, name: str, entrypoint: str, frontmatter: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\n{frontmatter.strip()}\nentrypoint-script: scripts/run.py\n---\n\n# {name}\n"
    )
    (d / "scripts").mkdir()
    (d / "scripts" / "run.py").write_text(entrypoint)


def test_entrypoint_invokes_nested_skill(tmp_git_repo: Path) -> None:
    skills_root = tmp_git_repo / "skills"

    _seed_skill(
        skills_root, "inner",
        "def run(ctx): return {'doubled': ctx.inputs['n'] * 2}\n",
        "name: inner\ninputs: {n: {type: integer, required: true}}\noutputs: {doubled: {type: integer}}\n",
    )

    _seed_skill(
        skills_root, "outer",
        (
            "def run(ctx):\n"
            "    result = ctx.invoke_skill('inner', {'n': 21})\n"
            "    return {'final': result['doubled']}\n"
        ),
        "name: outer\ninputs: {}\noutputs: {final: {type: integer}}\ninvokes-skills: [inner]\n",
    )

    wf_root = tmp_git_repo / "workflows"
    wf_root.mkdir()
    (wf_root / "w.yaml").write_text("name: w\nsteps:\n  - id: o\n    skill: outer\n")

    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[wf_root],
        runtime=SkillRuntime(),
    )
    run_id = engine.start_run(workflow="w", inputs={})
    state = engine.get_run_state(run_id)
    assert state["status"] == "success"
    import json
    out = json.loads(state["steps"][0]["output_json"])
    assert out == {"final": 42}
