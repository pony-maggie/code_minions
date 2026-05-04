"""FastAPI dependency providers for shared Engine / RunStore instances.

v1 uses process-wide singletons (one Engine per web process) — matches the
localhost-only single-user scope. Phase C-B will replace with per-request
user-scoped instances.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from code_minions.config import load_devflow_config
from code_minions.engine.engine import Engine
from code_minions.engine.skill_runtime import SkillRuntime
from code_minions.store.run_store import RunStore


@lru_cache(maxsize=1)
def _project_root() -> Path:
    # The web server is started from the user's project cwd (see cli web command).
    return Path.cwd().resolve()


@lru_cache(maxsize=1)
def _builtin_root() -> Path:
    import code_minions
    return Path(code_minions.__file__).resolve().parent / "builtin"


def workflow_search_paths() -> list[Path]:
    cfg = load_devflow_config(_project_root())
    return [*cfg.workflow_search_paths, _builtin_root() / "workflows"]


def skill_search_paths() -> list[Path]:
    cfg = load_devflow_config(_project_root())
    return [*cfg.skill_search_paths, _builtin_root() / "skills"]


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return a process-wide Engine tied to the cwd's project layout."""
    from code_minions.web.events import get_event_bus
    root = _project_root()
    return Engine(
        project_root=root,
        skill_search_paths=skill_search_paths(),
        workflow_search_paths=workflow_search_paths(),
        runtime=SkillRuntime(),
        event_bus=get_event_bus(),
    )


@lru_cache(maxsize=1)
def get_store() -> RunStore:
    """Return the RunStore used by the Engine (same SQLite file)."""
    db_path = _project_root() / ".devflow" / "runs.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return RunStore(db_path)
