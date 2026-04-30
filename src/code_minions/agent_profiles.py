from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from code_minions.stacks import stack_id_for_delivery

VALID_GATE_STRICTNESS = {"relaxed", "balanced", "strict"}


@dataclass(frozen=True)
class AgentProfile:
    profile_id: str
    role: str
    stack_id: str
    temperature: float = 0.2
    max_tokens: int = 16000
    self_heal_max_rounds: int = 3
    reviewer_max_rounds: int = 0
    gate_strictness: str = "balanced"
    guidance: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["guidance"] = list(self.guidance)
        return data


def _normalized_strictness(value: Any) -> str:
    strictness = str(value or "balanced").strip().lower()
    if strictness in VALID_GATE_STRICTNESS:
        return strictness
    return "balanced"


def _default_profile(role: str, strictness: str) -> AgentProfile:
    return AgentProfile(
        profile_id=f"default/{role}",
        role=role,
        stack_id="",
        gate_strictness=strictness,
    )


def _react_vite_profile(role: str, strictness: str) -> AgentProfile:
    return AgentProfile(
        profile_id=f"react-vite/{role}",
        role=role,
        stack_id="react-vite",
        gate_strictness=strictness,
        guidance=(
            "React/Vite implementers must preserve root project layout, Vitest jsdom setup, "
            "consistent TypeScript contracts, and stable Testing Library selectors.",
        ),
    )


def resolve_agent_profile(
    *,
    role: str,
    delivery_profile: dict[str, Any] | None,
    requested_profile_id: str | None = None,
) -> AgentProfile:
    profile = delivery_profile or {}
    stack_id = stack_id_for_delivery(profile)
    strictness = _normalized_strictness(profile.get("gate_strictness"))

    resolved = (
        _react_vite_profile(role, strictness)
        if stack_id == "react-vite"
        else _default_profile(role, strictness)
    )

    if requested_profile_id and requested_profile_id != resolved.profile_id:
        return AgentProfile(
            **{
                **resolved.to_dict(),
                "warnings": [
                    f"Unknown agent profile `{requested_profile_id}`; using `{resolved.profile_id}`."
                ],
            }
        )
    return resolved
