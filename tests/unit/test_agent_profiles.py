from code_minions.agent_profiles import resolve_agent_profile


def test_default_implementer_profile_has_safe_loop_defaults() -> None:
    profile = resolve_agent_profile(role="implementer", delivery_profile={})

    assert profile.profile_id == "default/implementer"
    assert profile.role == "implementer"
    assert profile.self_heal_max_rounds == 3
    assert profile.reviewer_max_rounds == 0
    assert profile.gate_strictness == "balanced"
    assert profile.temperature == 0.2


def test_react_vite_implementer_profile_inherits_delivery_strictness() -> None:
    profile = resolve_agent_profile(
        role="implementer",
        delivery_profile={
            "stack_id": "react-vite",
            "gate_strictness": "relaxed",
        },
    )

    assert profile.profile_id == "react-vite/implementer"
    assert profile.stack_id == "react-vite"
    assert profile.gate_strictness == "relaxed"
    assert "React/Vite" in "\n".join(profile.guidance)


def test_python_web_implementer_profile_guides_canonical_src_app_layout() -> None:
    profile = resolve_agent_profile(
        role="implementer",
        delivery_profile={
            "stack_id": "python-web",
            "gate_strictness": "relaxed",
        },
    )

    assert profile.profile_id == "python-web/implementer"
    assert profile.stack_id == "python-web"
    assert profile.gate_strictness == "relaxed"
    guidance = "\n".join(profile.guidance)
    assert "FastAPI" in guidance
    assert "src/<package>/app.py" in guidance
    assert "python-multipart" in guidance


def test_unknown_requested_profile_falls_back_with_warning() -> None:
    profile = resolve_agent_profile(
        role="implementer",
        delivery_profile={"stack_id": "react-vite"},
        requested_profile_id="missing/profile",
    )

    assert profile.profile_id == "react-vite/implementer"
    assert profile.warnings == [
        "Unknown agent profile `missing/profile`; using `react-vite/implementer`."
    ]
