"""SkillRuntime: invokes deterministic entrypoints or LLM agentic-loop skills."""
from __future__ import annotations

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from code_minions.engine.skill import Skill
from code_minions.stacks import apply_stack_pack_defaults

if TYPE_CHECKING:
    from code_minions.engine.context import ContextAssembler
    from code_minions.llm.base import LLMBackend
    from code_minions.mcp.pool import MCPClientPool


class NoHandlerError(Exception):
    """Raised when a skill has no entrypoint and no LLM is configured."""


class SkillValidationError(Exception):
    """Raised when inputs/outputs fail skill contract."""


class SkillExecutionError(Exception):
    """Raised when a skill fails after producing useful partial output."""

    def __init__(self, message: str, output: dict[str, Any] | None = None):
        super().__init__(message)
        self.output = output


@dataclass
class SkillContext:
    """Runtime context passed to a skill handler."""

    inputs: dict[str, Any]
    workdir: Path
    extras: dict[str, Any] = field(default_factory=dict)

    # New in M2:
    llm: LLMBackend | None = None
    mcp_pool: MCPClientPool | None = None
    assembler: ContextAssembler | None = None

    # New in M4: allows deterministic entrypoints to invoke another skill by name
    invoke_skill: Callable[[str, dict], dict] | None = None

    # Set by SkillRuntime.invoke. Handlers can read ctx.skill.meta.policies etc.
    skill: Skill | None = None


class SkillRuntime:
    """Skill runtime: entrypoint-script path + LLM agentic-loop path."""

    def invoke(self, skill: Skill, ctx: SkillContext) -> dict[str, Any]:
        ctx.skill = skill
        self._validate_inputs(skill, ctx.inputs)
        if skill.meta.entrypoint_script:
            return self._run_entrypoint_script(skill, ctx)
        return self._run_llm_path(skill, ctx)

    def _run_entrypoint_script(self, skill: Skill, ctx: SkillContext) -> dict[str, Any]:
        if skill.meta.entrypoint_script is None:
            raise NoHandlerError(f"skill {skill.name!r} has no entrypoint-script")
        script = (skill.directory / skill.meta.entrypoint_script).resolve()
        if script.suffix != ".py":
            raise SkillValidationError("entrypoint-script v1 only supports Python files")
        if not script.is_file():
            raise NoHandlerError(f"entrypoint script not found: {script}")
        runner = self._load_python_entrypoint(script)
        result = runner(ctx)
        if not isinstance(result, dict):
            raise SkillValidationError(
                f"skill {skill.name!r} entrypoint must return dict, got {type(result).__name__}"
            )
        return self._postprocess_output(result, skill, ctx)

    def _run_llm_path(self, skill: Skill, ctx: SkillContext) -> dict[str, Any]:
        if ctx.llm is None or ctx.assembler is None:
            raise NoHandlerError(
                f"skill {skill.name!r} has no entrypoint-script and no LLM is configured"
            )
        cache_key = self._cache_key(skill, ctx)
        if cache_key is not None:
            cached = self._cache_get(ctx, cache_key)
            if cached is not None:
                cached = self._postprocess_output(cached, skill, ctx)
                self._validate_output_policies(cached, skill)
                return cached

        # Build tools from allowed built-in local tools and MCPs.
        from code_minions.llm.types import Tool as LLMTool
        tools: list = []
        local_tool_names = set(skill.meta.allowed_tools)
        for tool_name in skill.meta.allowed_tools:
            if tool_name not in LOCAL_TOOL_SCHEMAS:
                raise SkillValidationError(f"unknown allowed local tool: {tool_name}")
            tools.append(LLMTool(
                name=tool_name,
                description=f"Built-in local tool {tool_name}",
                input_schema=LOCAL_TOOL_SCHEMAS[tool_name],
            ))

        tool_to_server: dict[str, str] = {}
        tool_to_real_name: dict[str, str] = {}
        if ctx.mcp_pool is not None:
            allowed = set(skill.meta.required_mcps)
            for server_name, srv_tools in ctx.mcp_pool.list_tools().items():
                if server_name not in allowed:
                    continue
                for t in srv_tools:
                    wire_name = f"mcp__{server_name}__{t['name']}"
                    tools.append(LLMTool(
                        name=wire_name,
                        description=t["description"],
                        input_schema=t["input_schema"],
                    ))
                    tool_to_server[wire_name] = server_name
                    tool_to_real_name[wire_name] = t["name"]

        from code_minions.llm.types import Message
        system = ctx.assembler.build_system_prompt(
            skill_instructions=skill.instructions,
            step_summary=f"Inputs: {ctx.inputs}\nExpected outputs: {list(skill.meta.outputs.keys())}",
        )
        messages: list[Message] = [
            Message(role="system", content=system),
            Message(role="user", content=f"Execute skill {skill.name!r} with inputs: {ctx.inputs}"),
        ]

        max_iters = skill.meta.llm.max_iterations
        last_assistant_summary = ""
        for _ in range(max_iters):
            resp = ctx.llm.chat(
                messages=messages,
                tools=tools or None,
                temperature=skill.meta.llm.temperature,
                max_tokens=skill.meta.llm.max_tokens,
            )
            from code_minions.engine.tool_executor import record_llm_call
            record_llm_call(
                ctx.extras.get("run_event_recorder"),
                step_id=ctx.extras.get("current_step_id"),
                skill=skill.name,
                response=resp,
            )
            messages.append(resp.message)
            diagnostics = (
                f"stop_reason={resp.stop_reason}; model={resp.model}; "
                f"usage=input:{resp.usage.input_tokens},output:{resp.usage.output_tokens}"
            )
            if resp.message.tool_calls:
                calls = ", ".join(tc.name for tc in resp.message.tool_calls)
                last_assistant_summary = f"tool_calls=[{calls}]; {diagnostics}"
            else:
                last_assistant_summary = f"content={resp.message.content[:500]!r}; {diagnostics}"
            if not resp.message.tool_calls:
                try:
                    result = self._parse_final_json(resp.message.content, skill)
                    result = self._postprocess_output(result, skill, ctx)
                    self._validate_output_policies(result, skill)
                    if cache_key is not None:
                        self._cache_put(ctx, cache_key, result)
                    return result
                except SkillValidationError as e:
                    messages.append(Message(
                        role="user",
                        content=(
                            f"{e}. Reply again with a valid JSON object only. "
                            f"The object must match these output keys: {list(skill.meta.outputs.keys())}."
                        ),
                    ))
                    continue
            from code_minions.engine.tool_executor import ToolExecutionContext, ToolExecutor
            executor = ToolExecutor(ToolExecutionContext(
                workdir=ctx.workdir,
                workspace_mode=ctx.extras.get("workspace_mode", "git-worktree"),
                event_recorder=ctx.extras.get("run_event_recorder"),
                step_id=ctx.extras.get("current_step_id"),
            ))
            for tc in resp.message.tool_calls:
                if tc.name in local_tool_names:
                    result = executor.run_local(tc.name, tc.arguments, call_id=tc.id)
                    messages.append(Message(role="tool", tool_call_id=tc.id, content=result, name=tc.name))
                    continue
                server = tool_to_server.get(tc.name, "")
                real_tool = tool_to_real_name.get(tc.name, tc.name)
                if ctx.mcp_pool is None:
                    result = "[error] no MCP pool available"
                else:
                    result = executor.run_mcp(
                        ctx.mcp_pool,
                        server,
                        real_tool,
                        tc.arguments,
                        call_id=tc.id,
                        wire_name=tc.name,
                    )
                messages.append(Message(role="tool", tool_call_id=tc.id, content=result))
        raise SkillValidationError(
            f"skill {skill.name!r} exceeded max_iterations={max_iters}; "
            f"last assistant response: {last_assistant_summary}"
        )

    @staticmethod
    def _parse_final_json(content: str, skill: Skill) -> dict[str, Any]:
        import json
        import re
        txt = content.strip()
        m = re.search(r"\{.*\}", txt, re.DOTALL)
        if not m:
            raise SkillValidationError(f"skill {skill.name!r}: final message has no JSON")
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise SkillValidationError(f"skill {skill.name!r}: invalid JSON: {e}") from e
        if not isinstance(data, dict):
            raise SkillValidationError(f"skill {skill.name!r}: output is not a JSON object")
        return data

    @staticmethod
    def _validate_inputs(skill: Skill, inputs: dict[str, Any]) -> None:
        for key, spec in skill.meta.inputs.items():
            if isinstance(spec, dict) and spec.get("required") and key not in inputs:
                raise SkillValidationError(
                    f"missing required input {key!r} for skill {skill.name!r}"
                )

    @staticmethod
    def _validate_output_policies(output: dict[str, Any], skill: Skill) -> None:
        max_tasks = skill.meta.policies.get("max_tasks")
        if max_tasks is None:
            return
        tasks = output.get("tasks")
        if not isinstance(tasks, list):
            return
        limit = int(max_tasks)
        if len(tasks) > limit:
            raise SkillValidationError(
                f"skill {skill.name!r}: output has {len(tasks)} tasks; return at most {limit} tasks"
            )

    @staticmethod
    def _postprocess_output(output: dict[str, Any], skill: Skill, ctx: SkillContext) -> dict[str, Any]:
        if skill.name == "parse-prd":
            return SkillRuntime._postprocess_parse_prd_output(output, ctx)
        if skill.name != "plan-tasks":
            return output
        structured_prd = ctx.inputs.get("structured_prd")
        if not isinstance(structured_prd, dict):
            return output
        delivery_profile = structured_prd.get("delivery_profile")
        if not isinstance(delivery_profile, dict) or not delivery_profile:
            return output
        tasks = output.get("tasks")
        if not isinstance(tasks, list):
            return output

        normalized_tasks: list[Any] = []
        changed = False
        for task in tasks:
            if not isinstance(task, dict):
                normalized_tasks.append(task)
                continue
            current = task.get("delivery_profile")
            if current != delivery_profile:
                updated = dict(task)
                updated["delivery_profile"] = dict(delivery_profile)
                normalized_tasks.append(updated)
                changed = True
            else:
                normalized_tasks.append(task)
        if not changed:
            return output
        return {**output, "tasks": normalized_tasks}

    @staticmethod
    def _postprocess_parse_prd_output(output: dict[str, Any], ctx: SkillContext) -> dict[str, Any]:
        stack_id = ctx.inputs.get("delivery_stack_id")
        if not isinstance(stack_id, str) or not stack_id.strip():
            return output
        existing = output.get("delivery_profile")
        profile = dict(existing) if isinstance(existing, dict) else {}
        profile["stack_id"] = stack_id
        return {**output, "delivery_profile": apply_stack_pack_defaults(profile)}

    @staticmethod
    def _cache_key(skill: Skill, ctx: SkillContext) -> str | None:
        if not skill.meta.policies.get("cache"):
            return None
        if skill.meta.entrypoint_script:
            return None
        if skill.meta.required_mcps:
            return None
        if any(tool != "Read" for tool in skill.meta.allowed_tools):
            return None
        cache = ctx.extras.get("skill_cache")
        if cache is None:
            return None
        from code_minions.engine.skill_cache import build_skill_cache_key
        return build_skill_cache_key(
            skill=skill,
            inputs=ctx.inputs,
            workdir=ctx.workdir,
            llm=ctx.llm,
        )

    @staticmethod
    def _cache_get(ctx: SkillContext, key: str) -> dict[str, Any] | None:
        try:
            return ctx.extras["skill_cache"].get(key)
        except Exception:
            return None

    @staticmethod
    def _cache_put(ctx: SkillContext, key: str, output: dict[str, Any]) -> None:
        try:
            ctx.extras["skill_cache"].put(key, output)
        except Exception:
            return

    @staticmethod
    def _load_python_entrypoint(path: Path):
        spec = importlib.util.spec_from_file_location(
            f"code_minions_entrypoint_{path.parent.parent.name}", path
        )
        if spec is None or spec.loader is None:
            raise NoHandlerError(f"could not load entrypoint from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "run"):
            raise NoHandlerError(f"entrypoint at {path} has no run(ctx) function")
        return module.run


LOCAL_TOOL_SCHEMAS = {
    "Read": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    "Write": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
    "Edit": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
    },
    "Delete": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    "Bash": {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer"},
        },
        "required": ["command"],
    },
}
