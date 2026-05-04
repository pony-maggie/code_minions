"""Compose the system prompt for an LLM-driven skill invocation."""
from __future__ import annotations

from pathlib import Path

BASE_PROMPT = """You are a code_minions skill executor. Work inside the given run workspace.
Use the provided built-in local tools to read files, edit files, write files, and run local commands.
Use MCP tools only for external systems such as Jira or GitHub.
When finished, reply with a JSON object matching the skill's declared outputs.
Do not add narration outside the JSON in your final message.
"""


class ContextAssembler:
    def __init__(self, project_root: Path):
        self._root = Path(project_root)

    def build_system_prompt(self, skill_instructions: str, step_summary: str) -> str:
        agents = self._load_agents_md()
        parts = [BASE_PROMPT.strip()]
        parts.append("## Project (AGENTS.md)\n" + agents)
        parts.append("## Skill (SKILL.md)\n" + skill_instructions.strip())
        if step_summary.strip():
            parts.append("## Current step\n" + step_summary.strip())
        return "\n\n".join(parts)

    def _load_agents_md(self) -> str:
        p = self._root / "AGENTS.md"
        if p.exists():
            return f"(source: {p})\n" + p.read_text().strip()
        return "(no AGENTS.md found — ask user to provide one for better results)"
