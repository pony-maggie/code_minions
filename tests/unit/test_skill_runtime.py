"""Tests for SkillRuntime deterministic entrypoint mode."""
from __future__ import annotations

from pathlib import Path

import pytest

from code_minions.engine.skill import load_skill
from code_minions.engine.skill_runtime import (
    NoHandlerError,
    SkillContext,
    SkillRuntime,
    SkillValidationError,
)


def _write_skill(
    root: Path, name: str, frontmatter: str, entrypoint_code: str | None = None
) -> Path:
    d = root / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\n{frontmatter.strip()}\n---\n\n# test skill\n")
    if entrypoint_code is not None:
        (d / "scripts").mkdir()
        (d / "scripts" / "run.py").write_text(entrypoint_code)
    return d


def test_invoke_skill_with_entrypoint(tmp_path: Path) -> None:
    d = _write_skill(
        tmp_path,
        "echo",
        """
name: echo
entrypoint-script: scripts/run.py
inputs:
  msg: {type: string, required: true}
outputs:
  echoed: {type: string}
""",
        entrypoint_code=(
            "def run(ctx):\n"
            "    return {'echoed': ctx.inputs['msg']}\n"
        ),
    )
    sk = load_skill(d)
    rt = SkillRuntime()
    ctx = SkillContext(inputs={"msg": "hi"}, workdir=tmp_path)
    out = rt.invoke(sk, ctx)
    assert out == {"echoed": "hi"}


def test_skill_without_entrypoint_or_llm_raises(tmp_path: Path) -> None:
    d = _write_skill(
        tmp_path,
        "no-entrypoint",
        "name: no-entrypoint\ninputs: {}\noutputs: {}\n",
        entrypoint_code=None,
    )
    sk = load_skill(d)
    rt = SkillRuntime()
    with pytest.raises(NoHandlerError, match="no entrypoint-script"):
        rt.invoke(sk, SkillContext(inputs={}, workdir=tmp_path))


def test_missing_required_input_raises(tmp_path: Path) -> None:
    d = _write_skill(
        tmp_path,
        "req",
        """
name: req
entrypoint-script: scripts/run.py
inputs:
  x: {type: string, required: true}
outputs: {}
""",
        entrypoint_code="def run(ctx):\n    return {}\n",
    )
    sk = load_skill(d)
    rt = SkillRuntime()
    with pytest.raises(SkillValidationError, match="missing required input"):
        rt.invoke(sk, SkillContext(inputs={}, workdir=tmp_path))


def test_entrypoint_sees_skill_metadata_and_policies(tmp_path: Path) -> None:
    d = _write_skill(
        tmp_path,
        "policy-reader",
        """
name: policy-reader
entrypoint-script: scripts/run.py
inputs: {}
outputs: {}
policies:
  self_heal_max_rounds: 7
  reviewer_max_rounds: 2
""",
        entrypoint_code=(
            "def run(ctx):\n"
            "    return {\n"
            "        'skill_name': ctx.skill.name,\n"
            "        'policies': ctx.skill.meta.policies,\n"
            "    }\n"
        ),
    )
    sk = load_skill(d)
    rt = SkillRuntime()
    out = rt.invoke(sk, SkillContext(inputs={}, workdir=tmp_path))
    assert out["skill_name"] == "policy-reader"
    assert out["policies"] == {"self_heal_max_rounds": 7, "reviewer_max_rounds": 2}


def test_entrypoint_exception_bubbles(tmp_path: Path) -> None:
    d = _write_skill(
        tmp_path,
        "boom",
        "name: boom\nentrypoint-script: scripts/run.py\ninputs: {}\noutputs: {}\n",
        entrypoint_code=(
            "def run(ctx):\n"
            "    raise RuntimeError('boom')\n"
        ),
    )
    sk = load_skill(d)
    rt = SkillRuntime()
    with pytest.raises(RuntimeError, match="boom"):
        rt.invoke(sk, SkillContext(inputs={}, workdir=tmp_path))


def test_parse_prd_output_applies_delivery_stack_preset(tmp_path: Path) -> None:
    d = _write_skill(
        tmp_path,
        "parse-prd",
        """
name: parse-prd
entrypoint-script: scripts/run.py
inputs:
  prd_file: {type: string, required: true}
outputs:
  goal: {type: string}
  delivery_profile: {type: object}
""",
        entrypoint_code=(
            "def run(ctx):\n"
            "    return {\n"
            "        'goal': 'Build a web game',\n"
            "        'delivery_profile': {},\n"
            "        'features': [],\n"
            "    }\n"
        ),
    )
    sk = load_skill(d)
    rt = SkillRuntime()

    out = rt.invoke(sk, SkillContext(
        inputs={"prd_file": "prd.md", "delivery_stack_id": "react-vite"},
        workdir=tmp_path,
    ))

    assert out["delivery_profile"]["stack_id"] == "react-vite"
    assert out["delivery_profile"]["test_command"] == "npm test"


def test_plan_tasks_output_gets_authoritative_delivery_profile(tmp_path: Path) -> None:
    d = _write_skill(
        tmp_path,
        "plan-tasks",
        """
name: plan-tasks
entrypoint-script: scripts/run.py
inputs:
  structured_prd: {type: object, required: true}
outputs:
  tasks: {type: array}
""",
        entrypoint_code=(
            "def run(ctx):\n"
            "    return {'tasks': [{\n"
            "        'id': 'T1',\n"
            "        'title': 'Task',\n"
            "        'delivery_profile': {'kind': 'web-app'},\n"
            "    }]}\n"
        ),
    )
    sk = load_skill(d)
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
        "test_command": "npm test",
        "gate_strictness": "relaxed",
    }

    out = SkillRuntime().invoke(
        sk,
        SkillContext(inputs={"structured_prd": {"delivery_profile": profile}}, workdir=tmp_path),
    )

    assert out["tasks"][0]["delivery_profile"] == profile
