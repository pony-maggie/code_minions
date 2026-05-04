"""Post-skill hooks: deterministic checks/effects run after a skill returns."""
from __future__ import annotations

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class HookContext:
    workdir: Path
    skill_name: str
    step_id: str
    outputs: dict[str, Any]


class HookError(Exception):
    pass


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: dict[str, Callable[[HookContext], None]] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        self.register("lint", _builtin_lint)

    def register(self, name: str, fn: Callable[[HookContext], None]) -> None:
        self._hooks[name] = fn

    def register_from_file(self, name: str, path: Path) -> None:
        spec = importlib.util.spec_from_file_location(f"cm_hook_{name}", path)
        if spec is None or spec.loader is None:
            raise HookError(f"could not load hook {name} from {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, "run"):
            raise HookError(f"hook {path} has no run(ctx)")
        self.register(name, mod.run)

    def run(self, name: str, ctx: HookContext) -> None:
        fn = self._hooks.get(name)
        if fn is None:
            raise HookError(f"unknown hook: {name}")
        fn(ctx)


def _builtin_lint(ctx: HookContext) -> None:
    """Best-effort lint: if `ruff` is on PATH, run it against workdir."""
    import shutil
    import subprocess
    if not shutil.which("ruff"):
        return
    subprocess.run(["ruff", "check", str(ctx.workdir)], capture_output=True, timeout=60)
