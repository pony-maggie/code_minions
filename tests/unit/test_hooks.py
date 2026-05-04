from __future__ import annotations

from pathlib import Path

import pytest

from code_minions.engine.hooks import HookContext, HookError, HookRegistry


def test_register_and_run_builtin_lint(tmp_path: Path, monkeypatch):
    calls: list = []
    reg = HookRegistry()
    reg.register("custom", lambda ctx: calls.append(ctx.workdir))
    ctx = HookContext(workdir=tmp_path, skill_name="x", step_id="s", outputs={})
    reg.run("custom", ctx)
    assert calls == [tmp_path]


def test_unknown_hook_raises(tmp_path: Path):
    reg = HookRegistry()
    with pytest.raises(HookError, match="unknown hook"):
        reg.run("ghost", HookContext(workdir=tmp_path, skill_name="x", step_id="s", outputs={}))


def test_custom_hook_loaded_from_path(tmp_path: Path):
    hook_file = tmp_path / "my_hook.py"
    hook_file.write_text(
        "def run(ctx):\n"
        "    (ctx.workdir / 'hook_ran.txt').write_text('yes')\n"
    )
    reg = HookRegistry()
    reg.register_from_file("my-hook", hook_file)
    ctx = HookContext(workdir=tmp_path, skill_name="x", step_id="s", outputs={})
    reg.run("my-hook", ctx)
    assert (tmp_path / "hook_ran.txt").read_text() == "yes"
