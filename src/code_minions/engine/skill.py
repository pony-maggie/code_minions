"""Skill model and loader."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError


class SkillLoadError(Exception):
    """Raised when a skill directory fails to load or validate."""


class LLMPref(BaseModel):
    preferred_model: str | None = None
    max_iterations: int = 20
    temperature: float = 0.2
    max_tokens: int = 4096


class SkillMeta(BaseModel):
    name: str
    description: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    required_mcps: list[str] = Field(default_factory=list)
    entrypoint_script: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    # Advisory metadata in v0.1: declared but NOT enforced at runtime.
    # An entrypoint may invoke any loadable skill via ctx.invoke_skill regardless.
    invokes_skills: list[str] = Field(default_factory=list)
    llm: LLMPref = Field(default_factory=LLMPref)
    hooks: dict[str, list[str]] = Field(default_factory=dict)
    policies: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None
    effort: str | None = None


class Skill(BaseModel):
    meta: SkillMeta
    instructions: str
    directory: Path

    # Convenience accessors
    @property
    def name(self) -> str:
        return self.meta.name

    model_config = {"arbitrary_types_allowed": True}


FRONTMATTER_KEY_ALIASES = {
    "allowed-tools": "allowed_tools",
    "required-mcps": "required_mcps",
    "entrypoint-script": "entrypoint_script",
    "invokes-skills": "invokes_skills",
}


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise SkillLoadError("SKILL.md must start with YAML frontmatter")
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        raise SkillLoadError("SKILL.md frontmatter is not closed")
    raw_yaml = parts[0][4:]
    body = parts[1]
    try:
        meta = yaml.safe_load(raw_yaml) or {}
    except yaml.YAMLError as e:
        raise SkillLoadError(f"invalid SKILL.md frontmatter: {e}") from e
    if not isinstance(meta, dict):
        raise SkillLoadError("SKILL.md frontmatter must be a mapping")
    return meta, body


def _normalize_frontmatter_keys(raw: dict[str, Any]) -> dict[str, Any]:
    return {FRONTMATTER_KEY_ALIASES.get(k, k): v for k, v in raw.items()}


def load_skill(directory: Path) -> Skill:
    """Load a skill from a directory."""
    d = Path(directory)
    md = d / "SKILL.md"
    if not md.exists():
        raise SkillLoadError(f"missing SKILL.md in {d}")
    raw, body = _split_frontmatter(md.read_text())
    raw = _normalize_frontmatter_keys(raw)

    try:
        meta = SkillMeta.model_validate(raw)
    except ValidationError as e:
        raise SkillLoadError(f"SKILL.md frontmatter schema error: {e}") from e

    return Skill(
        meta=meta,
        instructions=body.strip(),
        directory=d,
    )
