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
    reviewer_max_rounds: int = 2
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
            "consistent TypeScript contracts, and stable Testing Library selectors. When using "
            "`vi.useFakeTimers()` with Testing Library `userEvent`, create the user with "
            "`userEvent.setup({ advanceTimers: vi.advanceTimersByTime })` and advance timer-driven "
            "state changes inside `act(...)` before asserting DOM movement. For grid movement apps, "
            "keep stable semantic markers synchronized from the same state used for visuals and behavior "
            "across later tasks. Hook tests must drive behavior through the public "
            "hook API or pure helpers; do not access imagined internals such as "
            "`result.current['_setState']`. Vitest `vi.spyOn` can only mock exported module properties, "
            "so do not spy on non-exported implementation helpers. When a test creates deterministic "
            "initial state, pass that fixture through a public component prop or hook initializer and make "
            "the implementation consume it; do not leave unused setup objects in tests. Movement tests for grid apps "
            "must respect the current direction and 180-degree reversal rule; use legal turn sequences "
            "or deterministic initial state helpers before asserting each direction.",
            "Treat acceptance criteria phrased with `or` as alternatives, not a mandatory checklist. Tests "
            "should prove at least one acceptable path works unless the UI contract intentionally promises "
            "both. For movement/grid tests, prefer before/after relative row/column deltas over exact "
            "coordinates unless a public deterministic initializer fixes the initial state and coordinate "
            "base explicitly.",
        ),
    )


def _python_web_profile(role: str, strictness: str) -> AgentProfile:
    return AgentProfile(
        profile_id=f"python-web/{role}",
        role=role,
        stack_id="python-web",
        gate_strictness=strictness,
        guidance=(
            "Python web implementers should keep a canonical FastAPI src-layout project: "
            "`src/<package>/app.py` exports the ASGI `app`, tests import from `<package>.app`, "
            "later tasks extend that same package instead of creating a second app package, "
            "existing route paths stay stable across tasks, "
            "`pyproject.toml` configures pytest with `pythonpath = ['src']`, "
            "and FastAPI `Form(...)` routes declare `python-multipart` as a runtime dependency.",
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

    if stack_id == "react-vite":
        resolved = _react_vite_profile(role, strictness)
    elif stack_id == "python-web":
        resolved = _python_web_profile(role, strictness)
    else:
        resolved = _default_profile(role, strictness)

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
