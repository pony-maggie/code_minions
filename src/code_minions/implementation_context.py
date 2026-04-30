from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_minions.agent_profiles import AgentProfile
from code_minions.gates import GateFinding, findings_to_text
from code_minions.stacks import stack_id_for_delivery


@dataclass(frozen=True)
class ImplementationContextPackage:
    ticket: dict[str, Any]
    delivery_profile: dict[str, Any]
    agent_profile: AgentProfile
    stack_id: str
    project_markers: list[str]
    agents_md: str
    build_config: str
    gate_findings: list[GateFinding]

    def render(self) -> str:
        return "\n\n".join([
            f"Agent profile:\n{json.dumps(self.agent_profile.to_dict(), ensure_ascii=False, indent=2)}",
            f"Delivery profile:\n{json.dumps(self.delivery_profile, ensure_ascii=False, indent=2, sort_keys=True)}",
            f"Ticket:\n{json.dumps(self.ticket, ensure_ascii=False, indent=2)}",
            f"Project markers:\n{json.dumps(self.project_markers, ensure_ascii=False)}",
            f"AGENTS.md excerpt:\n{self.agents_md}",
            f"Authoritative build/test configuration:\n{self.build_config}",
            findings_to_text(self.gate_findings) or "Gate findings: none",
        ])


def _read_optional(path: Path, limit: int = 4000) -> str:
    if not path.is_file():
        return ""
    return path.read_text(errors="ignore")[:limit]


def _build_config(workdir: Path) -> str:
    parts: list[str] = []
    for name in ("package.json", "vite.config.ts", "vitest.config.ts", "project.yml", "pyproject.toml"):
        path = workdir / name
        if path.is_file():
            parts.append(f"--- {name} ---\n{_read_optional(path)}")
    return "\n\n".join(parts) or "No recognized build/test configuration files found."


def build_implementation_context(
    *,
    workdir: Path,
    ticket: dict[str, Any],
    delivery_profile: dict[str, Any],
    agent_profile: AgentProfile,
    gate_findings: list[GateFinding] | None = None,
) -> ImplementationContextPackage:
    markers = [
        name
        for name in ("AGENTS.md", "package.json", "vite.config.ts", "vitest.config.ts", "project.yml", "pyproject.toml")
        if (workdir / name).exists()
    ]
    return ImplementationContextPackage(
        ticket=ticket,
        delivery_profile=delivery_profile,
        agent_profile=agent_profile,
        stack_id=stack_id_for_delivery(delivery_profile),
        project_markers=markers,
        agents_md=_read_optional(workdir / "AGENTS.md"),
        build_config=_build_config(workdir),
        gate_findings=gate_findings or [],
    )
