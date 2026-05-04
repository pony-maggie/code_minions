from __future__ import annotations

from pathlib import Path

from code_minions.engine.skill import load_skill


def test_load_skill_reads_frontmatter_from_skill_md(tmp_path: Path) -> None:
    d = tmp_path / "parse-prd"
    d.mkdir()
    (d / "SKILL.md").write_text(
        """---
name: parse-prd
description: Parse a PRD file
allowed-tools:
  - Read
required-mcps:
  - jira
entrypoint-script: scripts/run.py
inputs:
  prd_file: {type: string, required: true}
outputs:
  goal: {type: string}
invokes-skills:
  - ai-code-review
llm:
  max_iterations: 7
  temperature: 0.1
  max_tokens: 12000
hooks:
  post_run:
    - lint
policies:
  self_heal_max_rounds: 2
model: inherit
effort: medium
---

# parse-prd

Read a PRD and return structured JSON.
"""
    )

    skill = load_skill(d)

    assert skill.name == "parse-prd"
    assert skill.meta.description == "Parse a PRD file"
    assert skill.meta.allowed_tools == ["Read"]
    assert skill.meta.required_mcps == ["jira"]
    assert skill.meta.entrypoint_script == "scripts/run.py"
    assert skill.meta.inputs["prd_file"]["required"] is True
    assert skill.meta.outputs["goal"]["type"] == "string"
    assert skill.meta.invokes_skills == ["ai-code-review"]
    assert skill.meta.llm.max_iterations == 7
    assert skill.meta.llm.temperature == 0.1
    assert skill.meta.llm.max_tokens == 12000
    assert skill.meta.hooks == {"post_run": ["lint"]}
    assert skill.meta.policies == {"self_heal_max_rounds": 2}
    assert skill.meta.model == "inherit"
    assert skill.meta.effort == "medium"
    assert "Read a PRD" in skill.instructions
