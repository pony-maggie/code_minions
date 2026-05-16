"""FastAPI dependency providers for shared Engine / RunStore instances.

v1 uses process-wide singletons (one Engine per web process) — matches the
localhost-only single-user scope. Phase C-B will replace with per-request
user-scoped instances.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from code_minions.config import load_devflow_config
from code_minions.engine.engine import Engine
from code_minions.engine.skill_runtime import SkillRuntime
from code_minions.store.run_store import RunStore

if TYPE_CHECKING:
    from code_minions.llm.base import LLMBackend
    from code_minions.mcp.pool import MCPClientPool


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


def _make_llm_backend(root: Path) -> LLMBackend | None:
    devflow = root / "devflow.yaml"
    if not devflow.exists():
        return None
    try:
        from code_minions.llm.config import load_llm_config
        from code_minions.llm.litellm_backend import LiteLLMBackend

        cfg = load_llm_config(devflow)
        provider = cfg.providers[cfg.default]
        return LiteLLMBackend(
            provider=cfg.default,
            default_model=provider.model,
            api_key=provider.api_key,
            api_base=provider.api_base,
        )
    except Exception:
        return None


def _make_role_llm_backends(root: Path) -> dict[str, LLMBackend]:
    devflow = root / "devflow.yaml"
    if not devflow.exists():
        return {}
    try:
        from code_minions.llm.config import load_llm_config
        from code_minions.llm.litellm_backend import LiteLLMBackend

        cfg = load_llm_config(devflow)
        backends: dict[str, LLMBackend] = {}
        for role, provider_name in cfg.roles.items():
            provider = cfg.providers[provider_name]
            backends[role] = LiteLLMBackend(
                provider=provider_name,
                default_model=provider.model,
                api_key=provider.api_key,
                api_base=provider.api_base,
            )
        return backends
    except Exception:
        return {}


def _make_mcp_pool(root: Path) -> MCPClientPool | None:
    mcp_json = root / ".mcp.json"
    if not mcp_json.exists():
        return None
    try:
        from code_minions.mcp.config import load_mcp_config
        from code_minions.mcp.pool import MCPClientPool

        pool = MCPClientPool(load_mcp_config(mcp_json))
        import atexit

        atexit.register(pool.stop)
        return pool
    except Exception:
        return None


def project_llm_display() -> str:
    devflow = _project_root() / "devflow.yaml"
    if not devflow.exists():
        return "not configured"
    try:
        from code_minions.llm.config import load_llm_config

        cfg = load_llm_config(devflow)
    except Exception as e:
        return f"not configured ({e})"
    provider = cfg.providers[cfg.default]
    if provider.model:
        return f"{cfg.default}/{provider.model}"
    return cfg.default


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
        llm_backend=_make_llm_backend(root),
        role_llm_backends=_make_role_llm_backends(root),
        mcp_pool=_make_mcp_pool(root),
        event_bus=get_event_bus(),
    )


@lru_cache(maxsize=1)
def get_store() -> RunStore:
    """Return the RunStore used by the Engine (same SQLite file)."""
    db_path = _project_root() / ".devflow" / "runs.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return RunStore(db_path)
