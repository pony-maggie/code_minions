"""Tests for Engine.resume_run."""
from __future__ import annotations

from pathlib import Path

from code_minions.engine.engine import Engine


class Flakey:
    """Fail first time on step 'b', succeed on resume."""
    def __init__(self):
        self.fail_once = True
    def invoke(self, skill, ctx):
        if skill.name == "b" and self.fail_once:
            self.fail_once = False
            raise RuntimeError("boom")
        return {"result": skill.name}


def _seed_skills(root: Path):
    for name in ("a", "b", "c"):
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\nentrypoint-script: scripts/run.py\ninputs: {{}}\noutputs: {{}}\n---\n\n# {name}\n"
        )
        (d / "scripts").mkdir()
        (d / "scripts" / "run.py").write_text("def run(ctx): return {}\n")


def test_resume_picks_up_after_failure(tmp_git_repo: Path):
    skills_root = tmp_git_repo / "skills"
    _seed_skills(skills_root)
    wf_root = tmp_git_repo / "workflows"
    wf_root.mkdir()
    (wf_root / "s.yaml").write_text("""
name: s
steps:
  - id: a
    skill: a
  - id: b
    skill: b
    depends_on: [a]
  - id: c
    skill: c
    depends_on: [b]
""")
    runtime = Flakey()
    engine = Engine(
        project_root=tmp_git_repo,
        skill_search_paths=[skills_root],
        workflow_search_paths=[wf_root],
        runtime=runtime,  # type: ignore
    )
    run_id = engine.start_run(workflow="s", inputs={})
    state = engine.get_run_state(run_id)
    assert state["status"] == "failed"
    statuses = {s["step_id"]: s["status"] for s in state["steps"]}
    assert statuses["a"] == "success"
    assert statuses["b"] == "failed"

    engine.resume_run(run_id)
    state = engine.get_run_state(run_id)
    assert state["status"] == "success"
    statuses = {s["step_id"]: s["status"] for s in state["steps"]}
    assert statuses == {"a": "success", "b": "success", "c": "success"}
