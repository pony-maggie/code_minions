from __future__ import annotations

from code_minions.stacks import (
    apply_stack_pack_defaults,
    stack_id_for_delivery,
    stack_pack_for_delivery,
)


def test_react_vite_profile_gets_canonical_stack_id_and_defaults() -> None:
    profile = apply_stack_pack_defaults({
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
    })

    assert profile["stack_id"] == "react-vite"
    assert profile["test_command"] == "npm test"
    assert profile["required_files"] == ["package.json", "index.html", "src"]
    assert profile["forbidden_product_languages"] == ["python", "swift", "go"]


def test_explicit_stack_id_resolves_stack_pack_without_shape_guessing() -> None:
    profile = {"stack_id": "react-vite"}

    assert stack_id_for_delivery(profile) == "react-vite"
    assert stack_pack_for_delivery(profile).stack_id == "react-vite"


def test_python_web_profile_gets_defaults_from_fastapi_shape() -> None:
    profile = apply_stack_pack_defaults({
        "kind": "web-service",
        "language": "python",
        "framework": "fastapi",
        "build_system": "python",
    })

    assert profile["stack_id"] == "python-web"
    assert profile["test_command"] == "python -m pytest -q"
    assert profile["required_files"] == ["pyproject.toml", "src", "tests"]
    assert profile["forbidden_product_languages"] == ["javascript", "typescript", "swift", "go"]


def test_unknown_explicit_stack_id_is_preserved_but_has_no_builtin_pack() -> None:
    profile = {"stack_id": "custom-company-stack"}

    assert stack_id_for_delivery(profile) == "custom-company-stack"
    assert stack_pack_for_delivery(profile) is None


def test_swift_xcodegen_profile_gets_canonical_stack_id() -> None:
    profile = apply_stack_pack_defaults({
        "kind": "native-macos-app",
        "language": "swift",
        "build_system": "xcodegen",
    })

    assert profile["stack_id"] == "swift-xcodegen"
    assert profile["test_command"] == "xcodegen generate && xcodebuild test -scheme MacCalc"
