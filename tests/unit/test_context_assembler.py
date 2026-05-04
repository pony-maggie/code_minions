from __future__ import annotations

from pathlib import Path

from code_minions.engine.context import ContextAssembler


def test_assemble_with_agents_md(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("# project\nrun tests with pytest\n")
    asm = ContextAssembler(project_root=tmp_path)
    system = asm.build_system_prompt(
        skill_instructions="# skill\ndo X",
        step_summary="inputs: {'a': 1}",
    )
    assert "AGENTS.md" in system
    assert "run tests with pytest" in system
    assert "# skill" in system
    assert "do X" in system
    assert "inputs: {'a': 1}" in system


def test_assemble_without_agents_md(tmp_path: Path):
    asm = ContextAssembler(project_root=tmp_path)
    system = asm.build_system_prompt(skill_instructions="# s", step_summary="")
    assert "no AGENTS.md found" in system
    assert "# s" in system
