"""DAG Runner: execute a Workflow's steps in dependency order.

M1 constraints:
 - Sequential only (no parallel execution even if topology allows).
 - Variable references: $inputs.<name>, $steps.<id>.output[.<path>]

M3 additions:
 - for_each fan-out with per-iteration step ids (e.g. step[0], step[1]).
 - preloaded_outputs for resume support (Task 2).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from code_minions.engine.hooks import HookContext, HookRegistry
from code_minions.engine.run_journal import (
    STEP_ATTEMPT_REUSED,
    STEP_ATTEMPT_SKIPPED,
    record_step_attempt_failed,
    record_step_attempt_finished,
    record_step_attempt_started,
    record_step_attempt_status,
)
from code_minions.engine.sensors import BLOCKING_SEVERITIES, run_sensor
from code_minions.engine.skill import Skill
from code_minions.engine.skill_runtime import SkillContext, SkillRuntime
from code_minions.engine.workflow import SensorSpec, Workflow, WorkflowStep
from code_minions.gates import findings_to_dicts

if TYPE_CHECKING:
    from code_minions.engine.context import ContextAssembler
    from code_minions.engine.skill_cache import SkillCache
    from code_minions.llm.base import LLMBackend
    from code_minions.mcp.pool import MCPClientPool


class DAGRunnerError(Exception):
    """Raised for DAG-level execution failures."""


StepObserver = Callable[[str, str, dict[str, Any] | None, str | None, str | None], None]
"""(step_id, status, output_or_none, error_or_none, detail_or_none) -> None"""

_FOR_EACH_ITEM_HASH_KEY = "__code_minions_for_each_item_hash"
_SKIPPED_KEY = "__code_minions_skipped__"


def _for_each_item_hash(item: Any) -> str:
    payload = json.dumps(item, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _strip_for_each_metadata(output: dict[str, Any]) -> dict[str, Any]:
    if _FOR_EACH_ITEM_HASH_KEY not in output:
        return output
    return {k: v for k, v in output.items() if k != _FOR_EACH_ITEM_HASH_KEY}


def _with_for_each_metadata(output: dict[str, Any], item_hash: str) -> dict[str, Any]:
    result = dict(output)
    result[_FOR_EACH_ITEM_HASH_KEY] = item_hash
    return result


def _item_detail(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    identifier = item.get("id")
    title = item.get("title") or item.get("name")
    if identifier and title:
        return f"{identifier}: {title}"
    if title:
        return str(title)
    if identifier:
        return str(identifier)
    return None


def _items_summary(items: list[Any], skill: Skill | None = None) -> str:
    lines = [f"{len(items)} items"]
    if skill is not None:
        policies = skill.meta.policies
        self_heal = int(policies.get("self_heal_max_rounds", 1))
        reviewer = int(policies.get("reviewer_max_rounds", 0))
        per_item = 1 + self_heal + (1 if reviewer > 0 else 0)
        lines.append(f"estimated LLM calls: up to ~{len(items) * per_item} ({per_item}/item)")
    for idx, item in enumerate(items):
        detail = _item_detail(item) or str(item)
        lines.append(f"  [{idx}] {detail}")
    return "\n".join(lines)


class DAGRunner:
    def __init__(
        self,
        workflow: Workflow,
        skills_by_name: dict[str, Skill],
        runtime: SkillRuntime,
        workdir: Path,
        inputs: dict[str, Any],
        observer: StepObserver | None = None,
        *,
        llm_backend: LLMBackend | None = None,
        role_llm_backends: dict[str, LLMBackend] | None = None,
        mcp_pool: MCPClientPool | None = None,
        assembler: ContextAssembler | None = None,
        preloaded_outputs: dict[str, dict[str, Any]] | None = None,
        hook_registry: HookRegistry | None = None,
        skill_search_paths: list[Path] | None = None,
        project_root: Path | None = None,
        skill_cache: SkillCache | None = None,
        run_event_recorder: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self._wf = workflow
        self._skills = skills_by_name
        self._runtime = runtime
        self._workdir = Path(workdir)
        self._inputs = self._inputs_with_defaults(workflow, inputs)
        self._observer = observer or (lambda *a, **kw: None)
        self._llm = llm_backend
        self._role_llms = role_llm_backends or {}
        self._mcp = mcp_pool
        self._assembler = assembler
        self._preloaded_outputs = preloaded_outputs
        self._hook_registry = hook_registry
        self._skill_search_paths = [Path(p) for p in (skill_search_paths or [])]
        self._project_root = Path(project_root) if project_root is not None else self._workdir
        self._workspace_mode = workflow.workspace.mode
        self._skill_cache = skill_cache
        self._run_event_recorder = run_event_recorder
        self._is_resume = preloaded_outputs is not None
        self._active_step_id: str | None = None
        # Cache invoke_skill callable so it's created once per runner instance
        self._invoke_skill_fn: Callable[[str, dict[str, Any]], dict[str, Any]] = self._make_invoke_skill()

    @staticmethod
    def _inputs_with_defaults(workflow: Workflow, inputs: dict[str, Any]) -> dict[str, Any]:
        merged = {
            key: spec.default
            for key, spec in workflow.inputs.items()
            if spec.default is not None or not spec.required
        }
        merged.update(inputs)
        merged.update(workflow.preset_inputs)
        return merged

    def _observe(
        self,
        step_id: str,
        status: str,
        output: dict[str, Any] | None,
        error: str | None,
        detail: str | None = None,
    ) -> None:
        try:
            self._observer(step_id, status, output, error, detail)
        except TypeError:
            self._observer(step_id, status, output, error)

    def _skill_extras(self, step_id: str | None = None) -> dict[str, Any]:
        extras = {
            "project_root": self._project_root,
            "workspace_mode": self._workspace_mode,
            "is_resume": self._is_resume,
        }
        if step_id is not None:
            extras["current_step_id"] = step_id
        if self._skill_cache is not None:
            extras["skill_cache"] = self._skill_cache
        if self._run_event_recorder is not None:
            extras["run_event_recorder"] = self._run_event_recorder
        return extras

    def _llm_for_skill(self, skill: Skill) -> LLMBackend | None:
        role = skill.meta.role
        if role and role in self._role_llms:
            return self._role_llms[role]
        return self._llm

    def run(self) -> dict[str, dict[str, Any]]:
        order = self._topo_order()
        outputs: dict[str, dict[str, Any]] = dict(self._preloaded_outputs or {})

        for step in order:
            if step.id in outputs:
                record_step_attempt_status(
                    self._run_event_recorder,
                    STEP_ATTEMPT_REUSED,
                    observable_step_id=step.id,
                    reason="preloaded successful output reused during resume",
                    output=outputs[step.id],
                )
                continue  # resume: skip already-succeeded steps
            if not self._when_allows(step, outputs):
                output = {_SKIPPED_KEY: True, "reason": "when condition evaluated false"}
                outputs[step.id] = output
                record_step_attempt_status(
                    self._run_event_recorder,
                    STEP_ATTEMPT_SKIPPED,
                    observable_step_id=step.id,
                    reason="when condition evaluated false",
                    output=output,
                )
                self._observe(step.id, "skipped", output, None)
                continue
            if step.for_each is not None:
                outputs[step.id] = self._run_for_each(step, outputs)
                continue
            outputs[step.id] = self._run_single_step(step, step.id, step.inputs, outputs)
        return outputs

    def _when_allows(
        self,
        step: WorkflowStep,
        outputs: dict[str, dict[str, Any]],
    ) -> bool:
        if step.when is None:
            return True
        if isinstance(step.when, bool):
            return step.when
        return bool(self._resolve_value(step.when, outputs))

    def _make_invoke_skill(self) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
        def _invoke(skill_name: str, inputs: dict[str, Any]) -> dict[str, Any]:
            skill = self._skills.get(skill_name) or self._load_nested_skill(skill_name)
            sub_ctx = SkillContext(
                inputs=inputs,
                workdir=self._workdir,
                extras=self._skill_extras(self._active_step_id),
                llm=self._llm_for_skill(skill),
                mcp_pool=self._mcp,
                assembler=self._assembler,
                invoke_skill=_invoke,
            )
            return self._runtime.invoke(skill, sub_ctx)
        return _invoke

    def _load_nested_skill(self, skill_name: str) -> Skill:
        from code_minions.engine.skill import SkillLoadError, load_skill
        for base in self._skill_search_paths:
            cand = base / skill_name
            if cand.is_dir():
                try:
                    return load_skill(cand)
                except SkillLoadError as e:
                    raise DAGRunnerError(f"nested skill {skill_name!r} failed to load: {e}") from e
        raise DAGRunnerError(f"nested skill {skill_name!r} not found in search paths")

    def _run_single_step(
        self,
        step: WorkflowStep,
        observable_step_id: str,
        raw_inputs: dict[str, Any],
        outputs: dict[str, dict[str, Any]],
        detail: str | None = None,
        output_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        skill = self._skills.get(step.skill)
        if skill is None:
            raise DAGRunnerError(
                f"step {step.id!r} references unknown skill {step.skill!r}"
            )
        resolved = self._resolve_inputs(raw_inputs, outputs)
        llm = self._llm_for_skill(skill)
        record_step_attempt_started(
            self._run_event_recorder,
            observable_step_id=observable_step_id,
            step=step,
            skill=skill,
            resolved_inputs=resolved,
            workspace_mode=self._workspace_mode,
            is_resume=self._is_resume,
            llm=llm,
            detail=detail,
        )
        self._observe(observable_step_id, "running", None, None, detail)
        try:
            previous_step_id = self._active_step_id
            self._active_step_id = observable_step_id
            result = self._runtime.invoke(
                skill,
                SkillContext(
                    inputs=resolved,
                    workdir=self._workdir,
                    extras=self._skill_extras(observable_step_id),
                    llm=self._llm_for_skill(skill),
                    mcp_pool=self._mcp,
                    assembler=self._assembler,
                    invoke_skill=self._invoke_skill_fn,
                ),
            )
        except Exception as e:
            partial_output = getattr(e, "output", None)
            record_step_attempt_failed(
                self._run_event_recorder,
                observable_step_id=observable_step_id,
                error=repr(e),
                output=partial_output,
                detail=detail,
            )
            self._observe(observable_step_id, "failed", partial_output, repr(e), detail)
            raise
        finally:
            self._active_step_id = previous_step_id
        observed_result = result
        if output_metadata:
            observed_result = dict(result)
            observed_result.update(output_metadata)
        # Fire post_run hooks declared by the skill
        if self._hook_registry is not None:
            hooks_to_run = skill.meta.hooks.get("post_run", [])
            for hook_name in hooks_to_run:
                self._hook_registry.run(
                    hook_name,
                    HookContext(workdir=self._workdir, skill_name=skill.name,
                                step_id=observable_step_id, outputs=result),
                )
        sensor_findings = self._run_step_sensors(step)
        if sensor_findings:
            observed_result = dict(observed_result)
            observed_result["gate_findings"] = [
                *observed_result.get("gate_findings", []),
                *findings_to_dicts(sensor_findings),
            ]
            blocking = [finding for finding in sensor_findings if finding.severity in BLOCKING_SEVERITIES]
            if blocking:
                error = f"sensor {blocking[0].code.removeprefix('sensor-')} failed"
                record_step_attempt_failed(
                    self._run_event_recorder,
                    observable_step_id=observable_step_id,
                    error=error,
                    output=observed_result,
                    detail=detail,
                )
                self._observe(
                    observable_step_id,
                    "failed",
                    observed_result,
                    error,
                    detail,
                )
                raise DAGRunnerError(error)
        record_step_attempt_finished(
            self._run_event_recorder,
            observable_step_id=observable_step_id,
            status="success",
            output=observed_result,
            detail=detail,
        )
        self._observe(observable_step_id, "success", observed_result, None, detail)
        return result

    def _run_step_sensors(self, step: WorkflowStep):
        findings = []
        for sensor in step.sensors:
            if isinstance(sensor, str):
                name = sensor
                spec = self._wf.sensors[name]
            else:
                spec = SensorSpec.model_validate(sensor)
                name = str(sensor.get("name") or f"{step.id}-{len(findings) + 1}")
            sensor_run = run_sensor(name=name, spec=spec, workdir=self._workdir)
            if not sensor_run.passed or sensor_run.finding.severity not in BLOCKING_SEVERITIES:
                findings.append(sensor_run.finding)
            if self._run_event_recorder is not None:
                self._run_event_recorder(
                    "sensor",
                    {
                        "step_id": self._active_step_id,
                        "sensor": name,
                        "passed": sensor_run.passed,
                        "finding": sensor_run.finding.to_dict(),
                    },
                )
        return findings

    def _run_for_each(
        self,
        step: WorkflowStep,
        outputs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        items_value = self._resolve_value(step.for_each, outputs)
        if not isinstance(items_value, list):
            raise DAGRunnerError(
                f"step {step.id}: for_each must resolve to a list, got {type(items_value).__name__}"
            )
        skill = self._skills.get(step.skill)
        summary = _items_summary(items_value, skill)
        self._observe(step.id, "running", None, None, summary)
        collected: list[dict[str, Any]] = []
        for idx, item in enumerate(items_value):
            scratch = {"__item__": item, "__index__": idx}
            per_iter = {
                k: self._resolve_with_scratch(v, outputs, step.as_, scratch)
                for k, v in step.inputs.items()
            }
            sub_id = f"{step.id}[{idx}]"
            detail = _item_detail(item)
            item_hash = _for_each_item_hash(item)
            if sub_id in outputs:
                cached = outputs[sub_id]
                cached_hash = cached.get(_FOR_EACH_ITEM_HASH_KEY)
                if cached_hash is None or cached_hash == item_hash:
                    result = _strip_for_each_metadata(cached)
                else:
                    try:
                        result = self._run_single_step(
                            step,
                            sub_id,
                            per_iter,
                            outputs,
                            detail=detail,
                            output_metadata={_FOR_EACH_ITEM_HASH_KEY: item_hash},
                        )
                    except Exception:
                        self._observe(step.id, "failed", None, f"iteration {idx} failed", detail)
                        raise
                    outputs[sub_id] = _with_for_each_metadata(result, item_hash)
            else:
                try:
                    result = self._run_single_step(
                        step,
                        sub_id,
                        per_iter,
                        outputs,
                        detail=detail,
                        output_metadata={_FOR_EACH_ITEM_HASH_KEY: item_hash},
                    )
                except Exception:
                    self._observe(step.id, "failed", None, f"iteration {idx} failed", detail)
                    raise
                outputs[sub_id] = _with_for_each_metadata(result, item_hash)
            collected.append(result)
        self._observe(step.id, "success", {"items": collected}, None, summary)
        return {"items": collected}

    def _resolve_with_scratch(
        self,
        value: Any,
        outputs: dict[str, dict[str, Any]],
        as_name: str | None,
        scratch: dict[str, Any],
    ) -> Any:
        if isinstance(value, str) and value == f"${as_name}":
            return scratch["__item__"]
        if isinstance(value, str) and value.startswith(f"${as_name}."):
            path = value[len(f"${as_name}."):].split(".")
            cur = scratch["__item__"]
            for p in path:
                if isinstance(cur, dict):
                    cur = cur[p]
                else:
                    raise DAGRunnerError(
                        f"cannot traverse into {type(cur).__name__} at ${as_name}.{'.'.join(path)}"
                    )
            return cur
        return self._resolve_value(value, outputs)

    def _topo_order(self) -> list[WorkflowStep]:
        by_id = {s.id: s for s in self._wf.steps}
        visited: set[str] = set()
        visiting: set[str] = set()
        order: list[WorkflowStep] = []

        def visit(sid: str) -> None:
            if sid in visited:
                return
            if sid in visiting:
                raise DAGRunnerError(f"cycle detected involving {sid}")
            visiting.add(sid)
            step = by_id[sid]
            for dep in step.depends_on:
                visit(dep)
            visiting.remove(sid)
            visited.add(sid)
            order.append(step)

        for s in self._wf.steps:
            visit(s.id)
        return order

    def _resolve_inputs(
        self,
        raw: dict[str, Any],
        step_outputs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return {k: self._resolve_value(v, step_outputs) for k, v in raw.items()}

    def _resolve_value(
        self,
        value: Any,
        step_outputs: dict[str, dict[str, Any]],
    ) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            return self._resolve_ref(value, step_outputs)
        if isinstance(value, list):
            return [self._resolve_value(v, step_outputs) for v in value]
        if isinstance(value, dict):
            return {k: self._resolve_value(v, step_outputs) for k, v in value.items()}
        return value

    def _resolve_ref(
        self,
        ref: str,
        step_outputs: dict[str, dict[str, Any]],
    ) -> Any:
        parts = ref[1:].split(".")   # strip leading $
        root = parts[0]
        if root == "inputs":
            return self._walk(self._inputs, parts[1:], ref)
        if root == "steps":
            if len(parts) < 3 or parts[2] != "output":
                raise DAGRunnerError(f"bad step reference: {ref}")
            step_id = parts[1]
            if step_id not in step_outputs:
                raise DAGRunnerError(f"reference to unrun step {step_id}")
            return self._walk(step_outputs[step_id], parts[3:], ref)
        raise DAGRunnerError(f"unknown reference root: {ref}")

    @staticmethod
    def _walk(obj: Any, path: list[str], ref: str) -> Any:
        cur = obj
        for p in path:
            if isinstance(cur, dict):
                if p not in cur:
                    raise DAGRunnerError(f"missing key {p!r} in reference {ref}")
                cur = cur[p]
            else:
                raise DAGRunnerError(f"cannot traverse into {type(cur).__name__} at {ref}")
        return cur
