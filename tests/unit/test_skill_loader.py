"""Tests for Skill loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from code_minions.engine.skill import Skill, SkillLoadError, load_skill


def _make_skill_dir(
    root: Path,
    name: str,
    frontmatter: str,
    entrypoint_code: str | None = None,
) -> Path:
    d = root / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\n{frontmatter.strip()}\n---\n\n# instructions\n")
    if entrypoint_code is not None:
        (d / "scripts").mkdir()
        (d / "scripts" / "run.py").write_text(entrypoint_code)
    return d


def test_load_minimal_skill(tmp_path: Path) -> None:
    d = _make_skill_dir(
        tmp_path,
        "s1",
        """
name: s1
description: test
inputs:
  x: {type: string, required: true}
outputs:
  y: {type: object}
""",
    )
    sk = load_skill(d)
    assert isinstance(sk, Skill)
    assert sk.name == "s1"
    assert sk.meta.entrypoint_script is None
    assert "instructions" in sk.instructions


def test_load_skill_with_entrypoint(tmp_path: Path) -> None:
    d = _make_skill_dir(
        tmp_path,
        "s2",
        "name: s2\nentrypoint-script: scripts/run.py\ninputs: {}\noutputs: {}\n",
        entrypoint_code="def run(ctx):\n    return {'ok': True}\n",
    )
    sk = load_skill(d)
    assert sk.meta.entrypoint_script == "scripts/run.py"


def test_load_skill_missing_skill_md_fails(tmp_path: Path) -> None:
    d = tmp_path / "bad"
    d.mkdir()
    with pytest.raises(SkillLoadError, match="SKILL.md"):
        load_skill(d)


def test_load_skill_missing_frontmatter_fails(tmp_path: Path) -> None:
    d = tmp_path / "bad"
    d.mkdir()
    (d / "SKILL.md").write_text("x")
    with pytest.raises(SkillLoadError, match="frontmatter"):
        load_skill(d)


def test_load_skill_bad_frontmatter_fails(tmp_path: Path) -> None:
    d = _make_skill_dir(tmp_path, "bad", "nope: :\n  this is: not valid:\n: - - -\n")
    with pytest.raises(SkillLoadError):
        load_skill(d)
