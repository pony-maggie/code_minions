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

    def __init__(
        self,
        message: str,
        output: dict[str, Any] | None = None,
        *,
        run_status: str | None = None,
    ):
        super().__init__(message)
        self.output = output
        self.run_status = run_status


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
        self._validate_prd_ready_for_planning(skill, ctx)
        self._ensure_required_mcps(skill, ctx)
        if skill.meta.entrypoint_script:
            return self._run_entrypoint_script(skill, ctx)
        return self._run_llm_path(skill, ctx)

    def _ensure_required_mcps(self, skill: Skill, ctx: SkillContext) -> None:
        if not skill.meta.required_mcps:
            return
        if ctx.mcp_pool is None:
            required = ", ".join(skill.meta.required_mcps)
            raise SkillValidationError(f"skill {skill.name!r} requires MCP server(s): {required}")
        ctx.mcp_pool.ensure_started(set(skill.meta.required_mcps))

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

        from code_minions.engine.agent_loop import AgentLoop, AgentLoopConfig
        from code_minions.engine.context_compaction import context_budget_chars
        from code_minions.engine.tool_executor import ToolExecutionContext, ToolExecutor

        executor = ToolExecutor(ToolExecutionContext(
            workdir=ctx.workdir,
            workspace_mode=ctx.extras.get("workspace_mode", "git-worktree"),
            event_recorder=ctx.extras.get("run_event_recorder"),
            step_id=ctx.extras.get("current_step_id"),
            tool_capabilities=skill.meta.tool_capabilities,
        ))

        def handle_tool(tc) -> str:
            if tc.name in local_tool_names:
                return executor.run_local(tc.name, tc.arguments, call_id=tc.id)
            server = tool_to_server.get(tc.name, "")
            real_tool = tool_to_real_name.get(tc.name, tc.name)
            if ctx.mcp_pool is None:
                return "[error] no MCP pool available"
            return executor.run_mcp(
                ctx.mcp_pool,
                server,
                real_tool,
                tc.arguments,
                call_id=tc.id,
                wire_name=tc.name,
            )

        def parse_final(content: str) -> dict[str, Any]:
            result = self._parse_final_json(content, skill)
            result = self._postprocess_output(result, skill, ctx)
            self._validate_output_policies(result, skill)
            return result

        def parser_retry(exc: Exception) -> str:
            return (
                f"{exc}. Reply again with a valid JSON object only. "
                f"The object must match these output keys: {list(skill.meta.outputs.keys())}."
            )

        loop = AgentLoop(
            llm=ctx.llm,
            config=AgentLoopConfig(
                max_iterations=skill.meta.llm.max_iterations,
                role=skill.meta.role,
                skill_name=skill.name,
                temperature=skill.meta.llm.temperature,
                max_tokens=skill.meta.llm.max_tokens,
                context_budget_chars=context_budget_chars(),
            ),
            event_recorder=ctx.extras.get("run_event_recorder"),
            step_id=ctx.extras.get("current_step_id"),
        )
        loop_result = loop.run(
            messages=messages,
            tools=tools or None,
            final_parser=parse_final,
            tool_handler=handle_tool,
            parser_retry_prompt=parser_retry,
        )
        if loop_result.failure is not None:
            raise SkillValidationError(
                f"skill {skill.name!r} {loop_result.failure.get('message', loop_result.failure)}"
            )
        result = loop_result.parsed
        if cache_key is not None:
            self._cache_put(ctx, cache_key, result)
        return result

    @staticmethod
    def _parse_final_json(content: str, skill: Skill) -> dict[str, Any]:
        import json
        import re

        def declared_output_match(data: dict[str, Any]) -> bool:
            declared = set(skill.meta.outputs)
            return not declared or any(key in data for key in declared)

        decoder = json.JSONDecoder()
        candidates: list[dict[str, Any]] = []

        def add_candidate(raw: str) -> dict[str, Any] | None:
            try:
                data, _ = decoder.raw_decode(raw.strip())
            except json.JSONDecodeError:
                return None
            if not isinstance(data, dict):
                return None
            candidates.append(data)
            return data

        txt = content.strip()
        for match in re.finditer(r"```(?:json)?\s*(.*?)```", txt, re.DOTALL | re.IGNORECASE):
            data = add_candidate(match.group(1))
            if data is not None and declared_output_match(data):
                return data

        for idx, char in enumerate(txt):
            if char != "{":
                continue
            data = add_candidate(txt[idx:])
            if data is not None and declared_output_match(data):
                return data

        if candidates:
            return candidates[0]
        if "{" not in txt:
            raise SkillValidationError(f"skill {skill.name!r}: final message has no JSON")
        raise SkillValidationError(f"skill {skill.name!r}: invalid JSON")

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
    def _validate_prd_ready_for_planning(skill: Skill, ctx: SkillContext) -> None:
        if skill.name not in {"plan-tasks", "python-web-plan-tasks"}:
            return
        structured_prd = ctx.inputs.get("structured_prd")
        if not isinstance(structured_prd, dict):
            return
        questions = structured_prd.get("questions")
        if not isinstance(questions, list) or not questions:
            return
        clean_questions = [str(question) for question in questions if str(question).strip()]
        if not clean_questions:
            return
        raise SkillExecutionError(
            "PRD needs clarification before task planning",
            output={
                "needs_clarification": True,
                "questions": clean_questions,
            },
            run_status="needs_clarification",
        )

    @staticmethod
    def _postprocess_output(output: dict[str, Any], skill: Skill, ctx: SkillContext) -> dict[str, Any]:
        if skill.name == "parse-prd":
            return SkillRuntime._postprocess_parse_prd_output(output, ctx)
        if skill.name in {"plan-tasks", "python-web-plan-tasks"}:
            return SkillRuntime._postprocess_plan_tasks_output(output, skill, ctx)
        return output

    @staticmethod
    def _postprocess_plan_tasks_output(output: dict[str, Any], skill: Skill, ctx: SkillContext) -> dict[str, Any]:
        SkillRuntime._validate_plan_task_compression(output, skill, ctx)
        tasks = output.get("tasks")
        if not isinstance(tasks, list):
            return output

        structured_prd = ctx.inputs.get("structured_prd")
        delivery_profile = None
        if isinstance(structured_prd, dict):
            raw_profile = structured_prd.get("delivery_profile")
            if isinstance(raw_profile, dict) and raw_profile:
                delivery_profile = raw_profile

        normalized_tasks: list[Any] = []
        changed = False
        for idx, task in enumerate(tasks, start=1):
            if not isinstance(task, dict):
                normalized_tasks.append(task)
                continue
            updated = dict(task)
            if not isinstance(updated.get("trace_id"), str) or not updated["trace_id"].strip():
                updated["trace_id"] = f"cm_task_{idx}"
                changed = True
            if delivery_profile is not None and updated.get("delivery_profile") != delivery_profile:
                updated["delivery_profile"] = dict(delivery_profile)
                changed = True
            normalized_tasks.append(updated)
        if not changed:
            return output
        return {**output, "tasks": normalized_tasks}

    @staticmethod
    def _validate_plan_task_compression(output: dict[str, Any], skill: Skill, ctx: SkillContext) -> None:
        max_tasks = skill.meta.policies.get("max_tasks")
        if max_tasks is None:
            return
        tasks = output.get("tasks")
        if not isinstance(tasks, list):
            return
        limit = int(max_tasks)
        if len(tasks) < limit:
            return
        structured_prd = ctx.inputs.get("structured_prd")
        if not isinstance(structured_prd, dict):
            return
        features = structured_prd.get("features")
        if not isinstance(features, list) or len(features) <= limit:
            return
        raise SkillExecutionError(
            "PRD task planning hit max_tasks and may have compressed features",
            output={
                "max_tasks_hit": True,
                "max_tasks": limit,
                "feature_count": len(features),
                "task_count": len(tasks),
                "repair_hint": (
                    "Split the PRD into smaller sub-PRDs, increase max_tasks explicitly, "
                    "or create an epic/sub-workflow plan instead of silently compressing features."
                ),
            },
            run_status="needs_human",
        )

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
        "properties": {
            "path": {"type": "string"},
            "file_path": {"type": "string"},
            "filePath": {"type": "string"},
            "filepath": {"type": "string"},
            "pathname": {"type": "string"},
        },
        "anyOf": [
            {"required": ["path"]},
            {"required": ["file_path"]},
            {"required": ["filePath"]},
            {"required": ["filepath"]},
            {"required": ["pathname"]},
        ],
    },
    "Glob": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "glob": {"type": "string"},
            "path": {"type": "string"},
        },
    },
    "Write": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "file_path": {"type": "string"},
            "filePath": {"type": "string"},
            "filepath": {"type": "string"},
            "pathname": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["content"],
        "anyOf": [
            {"required": ["path"]},
            {"required": ["file_path"]},
            {"required": ["filePath"]},
            {"required": ["filepath"]},
            {"required": ["pathname"]},
        ],
    },
    "Edit": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "file_path": {"type": "string"},
            "filePath": {"type": "string"},
            "filepath": {"type": "string"},
            "pathname": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
            "oldText": {"type": "string"},
            "newText": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "oldString": {"type": "string"},
            "newString": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"},
            "search": {"type": "string"},
            "replace": {"type": "string"},
        },
        "anyOf": [
            {"required": ["path", "old_text", "new_text"]},
            {"required": ["file_path", "old_text", "new_text"]},
            {"required": ["filePath", "old_text", "new_text"]},
            {"required": ["filepath", "old_text", "new_text"]},
            {"required": ["pathname", "old_text", "new_text"]},
            {"required": ["path", "oldText", "newText"]},
            {"required": ["file_path", "oldText", "newText"]},
            {"required": ["filePath", "oldText", "newText"]},
            {"required": ["filepath", "oldText", "newText"]},
            {"required": ["pathname", "oldText", "newText"]},
            {"required": ["path", "old_string", "new_string"]},
            {"required": ["file_path", "old_string", "new_string"]},
            {"required": ["filePath", "old_string", "new_string"]},
            {"required": ["filepath", "old_string", "new_string"]},
            {"required": ["pathname", "old_string", "new_string"]},
            {"required": ["path", "oldString", "newString"]},
            {"required": ["file_path", "oldString", "newString"]},
            {"required": ["filePath", "oldString", "newString"]},
            {"required": ["filepath", "oldString", "newString"]},
            {"required": ["pathname", "oldString", "newString"]},
            {"required": ["path", "old", "new"]},
            {"required": ["file_path", "old", "new"]},
            {"required": ["filePath", "old", "new"]},
            {"required": ["filepath", "old", "new"]},
            {"required": ["pathname", "old", "new"]},
            {"required": ["path", "search", "replace"]},
            {"required": ["file_path", "search", "replace"]},
            {"required": ["filePath", "search", "replace"]},
            {"required": ["filepath", "search", "replace"]},
            {"required": ["pathname", "search", "replace"]},
        ],
    },
    "Delete": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "file_path": {"type": "string"},
            "filePath": {"type": "string"},
            "filepath": {"type": "string"},
            "pathname": {"type": "string"},
        },
        "anyOf": [
            {"required": ["path"]},
            {"required": ["file_path"]},
            {"required": ["filePath"]},
            {"required": ["filepath"]},
            {"required": ["pathname"]},
        ],
    },
    "Bash": {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer"},
        },
        "required": ["command"],
    },
    "Command": {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cmd": {"type": "string"},
            "timeout": {"type": "integer"},
        },
        "anyOf": [
            {"required": ["command"]},
            {"required": ["cmd"]},
        ],
    },
}
