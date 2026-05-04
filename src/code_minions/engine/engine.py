"""Engine: top-level orchestration API.

This module glues Workflow loader, Skill loader, SkillRuntime, DAGRunner,
WorktreeManager, and RunStore. It's the single entry point used by CLI and
(in Phase C) by the HTTP server.
"""
from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from code_minions.engine.context import ContextAssembler
from code_minions.engine.dag_runner import DAGRunner
from code_minions.engine.event_bus import Event, EventBus
from code_minions.engine.hooks import HookRegistry
from code_minions.engine.skill import Skill, SkillLoadError, load_skill
from code_minions.engine.skill_cache import SkillCache
from code_minions.engine.skill_runtime import SkillRuntime
from code_minions.engine.workflow import Workflow, WorkflowLoadError, load_workflow
from code_minions.git.worktree import WorktreeManager
from code_minions.store.run_store import RunStore
from code_minions.types import RunStatus, StepStatus

if TYPE_CHECKING:
    from code_minions.llm.base import LLMBackend
    from code_minions.mcp.pool import MCPClientPool


class EngineError(Exception):
    """Top-level engine failure."""


def _run_workspace_dir(root: Path, run_id: str) -> Path:
    return root / ".devflow" / "runs" / run_id / "workspace"


def _run_worktree_dir(root: Path, run_id: str) -> Path:
    return root / ".devflow" / "runs" / run_id / "worktree"


def _llm_display(llm: LLMBackend | None) -> str:
    if llm is None:
        return "not configured"
    provider = getattr(llm, "_provider", None)
    model = getattr(llm, "_default_model", None)
    if provider and model:
        return f"{provider}/{model}"
    return getattr(llm, "name", type(llm).__name__)


class Engine:
    def __init__(
        self,
        project_root: Path,
        skill_search_paths: list[Path],
        workflow_search_paths: list[Path],
        runtime: SkillRuntime,
        run_store: RunStore | None = None,
        llm_backend: LLMBackend | None = None,
        mcp_pool: MCPClientPool | None = None,
        event_bus: EventBus | None = None,
    ):
        self._root = Path(project_root)
        self._skill_paths = [Path(p) for p in skill_search_paths]
        self._wf_paths = [Path(p) for p in workflow_search_paths]
        self._runtime = runtime

        devflow_dir = self._root / ".devflow"
        devflow_dir.mkdir(parents=True, exist_ok=True)
        self._store = run_store or RunStore(devflow_dir / "runs.db")
        self._skill_cache = SkillCache(devflow_dir / "skill_cache.db")
        self._wt_mgr = WorktreeManager(self._root)
        self._llm = llm_backend
        self._mcp = mcp_pool
        self._bus = event_bus
        self._assembler = ContextAssembler(self._root)
        self._hook_registry = HookRegistry()
        hooks_dir = self._root / "hooks"
        if hooks_dir.exists() and hooks_dir.is_dir():
            for f in hooks_dir.glob("*.py"):
                hook_name = f.stem.replace("_", "-")
                with contextlib.suppress(Exception):  # bad user hook; skip silently
                    self._hook_registry.register_from_file(hook_name, f)

    def _publish(self, run_id: str, kind: str, payload: dict) -> None:
        if self._bus is None:
            return
        self._bus.publish(Event(run_id=run_id, kind=kind, payload=payload, ts=datetime.now(UTC)))

    def start_run(self, workflow: str, inputs: dict[str, Any]) -> str:
        wf = self._load_workflow(workflow)  # validate eagerly
        run_id = self._store.create_run(workflow=wf.name, inputs=inputs, llm=self.llm_display)
        return self.execute_run(run_id, workflow, inputs)

    @property
    def llm_display(self) -> str:
        return _llm_display(self._llm)

    def execute_run(self, run_id: str, workflow: str, inputs: dict[str, Any]) -> str:
        """Execute a run whose row was created by the caller.

        Used by web BackgroundTasks, which needs the run_id synchronously
        before scheduling background work.
        """
        wf = self._load_workflow(workflow)
        skills = self._load_skills_for(wf)

        try:
            workdir = self._create_workspace(run_id, wf)
        except Exception as e:
            if not self._store.list_steps(run_id):
                self._store.upsert_step(
                    run_id=run_id,
                    step_id="__setup__",
                    status=StepStatus.FAILED,
                    error=f"workspace creation failed: {e}",
                )
                self._store.set_run_status(run_id, RunStatus.FAILED)
                self._publish(run_id, "run.finished", {"status": "failed"})
            return run_id

        def observe(
            step_id: str,
            status: str,
            output: dict[str, Any] | None,
            error: str | None,
            detail: str | None = None,
        ) -> None:
            self._store.upsert_step(
                run_id=run_id,
                step_id=step_id,
                status=StepStatus(status),
                output=output,
                error=error,
                detail=detail,
            )
            self._publish(run_id, "step.status", {
                "step_id": step_id, "status": status, "output": output, "error": error, "detail": detail,
            })

        def record_run_event(event_type: str, payload: dict[str, Any]) -> None:
            self._store.append_run_event(run_id, event_type, payload)

        runner = DAGRunner(
            workflow=wf,
            skills_by_name=skills,
            runtime=self._runtime,
            workdir=workdir,
            inputs=inputs,
            observer=observe,
            llm_backend=self._llm,
            mcp_pool=self._mcp,
            assembler=self._assembler,
            hook_registry=self._hook_registry,
            skill_search_paths=self._skill_paths,
            project_root=self._root,
            skill_cache=self._skill_cache,
            run_event_recorder=record_run_event,
        )

        self._store.set_run_status(run_id, RunStatus.RUNNING)
        self._publish(run_id, "run.started", {"workflow": wf.name})
        try:
            runner.run()
        except Exception:
            self._store.set_run_status(run_id, RunStatus.FAILED)
            self._publish(run_id, "run.finished", {"status": "failed"})
            return run_id
        self._store.set_run_status(run_id, RunStatus.SUCCESS)
        self._publish(run_id, "run.finished", {"status": "success"})
        return run_id

    def _create_workspace(self, run_id: str, wf: Workflow) -> Path:
        mode = wf.workspace.mode
        if mode == "git-worktree":
            wt_path = _run_worktree_dir(self._root, run_id)
            branch = f"code-minions/{run_id}"
            try:
                self._wt_mgr.create(worktree_path=wt_path, branch=branch)
            except Exception as e:
                self._store.upsert_step(
                    run_id=run_id,
                    step_id="__setup__",
                    status=StepStatus.FAILED,
                    error=f"worktree creation failed: {e}",
                )
                self._store.set_run_status(run_id, RunStatus.FAILED)
                self._publish(run_id, "run.finished", {"status": "failed"})
                raise
            return wt_path
        if mode == "project-readonly":
            return self._root
        workspace = _run_workspace_dir(self._root, run_id)
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def get_run_state(self, run_id: str) -> dict[str, Any]:
        run = self._store.get_run(run_id)
        if run is None:
            raise EngineError(f"run not found: {run_id}")
        steps = self._store.list_steps(run_id)
        return {
            "id": run["id"],
            "workflow": run["workflow"],
            "status": run["status"],
            "llm": run.get("llm"),
            "started_at": run["started_at"],
            "ended_at": run["ended_at"],
            "steps": steps,
        }

    def resume_run(self, run_id: str) -> str:
        run = self._store.get_run(run_id)
        if run is None:
            raise EngineError(f"run not found: {run_id}")
        if run["status"] in {RunStatus.SUCCESS.value, RunStatus.CANCELLED.value}:
            raise EngineError(f"run already {run['status']}; nothing to resume")

        wf = self._load_workflow(run["workflow"])
        skills = self._load_skills_for(wf)
        preloaded = self._store.get_successful_outputs(run_id)

        workdir = self._workspace_for_existing_run(run_id, wf)

        def observe(
            step_id: str,
            status: str,
            output: dict[str, Any] | None,
            error: str | None,
            detail: str | None = None,
        ) -> None:
            self._store.upsert_step(run_id, step_id, StepStatus(status), output, error, detail=detail)
            self._publish(run_id, "step.status", {
                "step_id": step_id, "status": status, "output": output, "error": error, "detail": detail,
            })

        def record_run_event(event_type: str, payload: dict[str, Any]) -> None:
            self._store.append_run_event(run_id, event_type, payload)

        import json as _json
        inputs = _json.loads(run["input_json"])
        runner = DAGRunner(
            workflow=wf, skills_by_name=skills, runtime=self._runtime,
            workdir=workdir, inputs=inputs, observer=observe,
            llm_backend=self._llm, mcp_pool=self._mcp, assembler=self._assembler,
            preloaded_outputs=preloaded, hook_registry=self._hook_registry,
            skill_search_paths=self._skill_paths,
            project_root=self._root,
            skill_cache=self._skill_cache,
            run_event_recorder=record_run_event,
        )

        self._store.set_run_status(run_id, RunStatus.RUNNING)
        self._publish(run_id, "run.started", {"workflow": wf.name})
        try:
            runner.run()
        except Exception:
            self._store.set_run_status(run_id, RunStatus.FAILED)
            self._publish(run_id, "run.finished", {"status": "failed"})
            return run_id
        self._store.set_run_status(run_id, RunStatus.SUCCESS)
        self._publish(run_id, "run.finished", {"status": "success"})
        return run_id

    def _workspace_for_existing_run(self, run_id: str, wf: Workflow) -> Path:
        mode = wf.workspace.mode
        if mode == "git-worktree":
            wt_path = _run_worktree_dir(self._root, run_id)
            if not wt_path.exists():
                raise EngineError(f"worktree missing for {run_id}; cannot resume")
            return wt_path
        if mode == "project-readonly":
            return self._root
        workspace = _run_workspace_dir(self._root, run_id)
        if not workspace.exists():
            raise EngineError(f"workspace missing for {run_id}; cannot resume")
        return workspace

    def get_run_workspace_path(self, run_id: str) -> Path:
        run = self._store.get_run(run_id)
        if run is None:
            raise EngineError(f"run not found: {run_id}")
        wf = self._load_workflow(run["workflow"])
        mode = wf.workspace.mode
        if mode == "git-worktree":
            return _run_worktree_dir(self._root, run_id)
        if mode == "project-readonly":
            return self._root
        return _run_workspace_dir(self._root, run_id)

    def cancel_run(self, run_id: str) -> None:
        run = self._store.get_run(run_id)
        if run is None or run["status"] not in {"pending", "running"}:
            return
        self._store.set_run_status(run_id, RunStatus.CANCELLED)

    def _load_workflow(self, name: str) -> Workflow:
        for base in self._wf_paths:
            candidate = base / f"{name}.yaml"
            if candidate.exists():
                try:
                    return load_workflow(candidate)
                except WorkflowLoadError as e:
                    raise EngineError(str(e)) from e
        raise EngineError(f"workflow not found: {name}")

    def _load_skills_for(self, wf: Workflow) -> dict[str, Skill]:
        needed = {s.skill for s in wf.steps}
        found: dict[str, Skill] = {}
        for base in self._skill_paths:
            if not base.exists():
                continue
            for child in base.iterdir():
                if not child.is_dir() or child.name not in needed:
                    continue
                if child.name in found:
                    continue
                try:
                    found[child.name] = load_skill(child)
                except SkillLoadError as e:
                    raise EngineError(f"skill {child.name!r} failed to load: {e}") from e
        missing = needed - set(found)
        if missing:
            raise EngineError(f"skills not found: {sorted(missing)}")
        return found
