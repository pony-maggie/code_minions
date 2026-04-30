from pathlib import Path

from code_minions.agent_profiles import resolve_agent_profile
from code_minions.gates import GateFinding
from code_minions.implementation_context import build_implementation_context


def test_context_package_includes_profile_stack_agents_and_ticket(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Use strict TypeScript.")
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}')
    ticket = {
        "id": "task-1",
        "title": "Board",
        "description": "Render a board.",
        "acceptance_criteria": ["shows board"],
        "delivery_profile": {"stack_id": "react-vite", "gate_strictness": "relaxed"},
    }
    profile = resolve_agent_profile(
        role="implementer",
        delivery_profile=ticket["delivery_profile"],
    )

    package = build_implementation_context(
        workdir=tmp_path,
        ticket=ticket,
        delivery_profile=ticket["delivery_profile"],
        agent_profile=profile,
        gate_findings=[
            GateFinding(
                code="missing-test-file",
                severity="warning",
                stage="preflight",
                message="No test file found.",
                repair_hint="Add a test.",
                source="react-vite",
                paths=[],
            )
        ],
    )

    rendered = package.render()
    assert package.stack_id == "react-vite"
    assert "task-1" in rendered
    assert "Use strict TypeScript." in rendered
    assert "react-vite/implementer" in rendered
    assert "missing-test-file" in rendered
