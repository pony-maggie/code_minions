from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StackPack:
    stack_id: str
    aliases: tuple[str, ...]
    defaults: dict[str, Any]


STACK_PACKS: dict[str, StackPack] = {
    "react-vite": StackPack(
        stack_id="react-vite",
        aliases=("react-vite", "vite-react", "react vite", "react-vite-ts"),
        defaults={
            "kind": "web-app",
            "language": "typescript",
            "framework": "react",
            "build_system": "vite",
            "test_command": "npm test",
            "required_files": ["package.json", "index.html", "src"],
            "forbidden_product_languages": ["python", "swift", "go"],
        },
    ),
    "swift-xcodegen": StackPack(
        stack_id="swift-xcodegen",
        aliases=("swift-xcodegen", "xcodegen-swift", "swift xcodegen"),
        defaults={
            "kind": "native-macos-app",
            "language": "swift",
            "framework": "swiftui",
            "build_system": "xcodegen",
            "test_command": "xcodegen generate && xcodebuild test -scheme MacCalc",
            "required_files": ["project.yml", "**/*.swift", "**/*App.swift"],
            "forbidden_product_languages": ["python", "javascript", "typescript", "go", "rust"],
        },
    ),
    "go-service": StackPack(
        stack_id="go-service",
        aliases=("go-service", "go-web-service", "go-mod-service"),
        defaults={
            "kind": "web-service",
            "language": "go",
            "build_system": "go-mod",
            "test_command": "go test ./...",
            "required_files": ["go.mod", "**/*.go"],
            "forbidden_product_languages": ["python", "javascript", "typescript", "swift"],
        },
    ),
    "python-cli": StackPack(
        stack_id="python-cli",
        aliases=("python-cli", "python cli", "typer-cli"),
        defaults={
            "kind": "cli",
            "language": "python",
            "build_system": "python",
            "test_command": "python -m pytest -q",
            "required_files": ["**/*.py"],
            "forbidden_product_languages": [],
        },
    ),
}

_STACK_ALIASES = {
    alias: pack.stack_id
    for pack in STACK_PACKS.values()
    for alias in (*pack.aliases, pack.stack_id)
}


def _canonical_stack_id(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _profile_text(profile: dict[str, Any]) -> str:
    return "\n".join(
        str(profile.get(key, ""))
        for key in ("kind", "language", "framework", "build_system", "test_command")
    ).lower()


def stack_id_for_delivery(profile: dict[str, Any] | None) -> str:
    if not profile:
        return ""

    explicit = profile.get("stack_id")
    if isinstance(explicit, str) and explicit.strip():
        stack_id = _canonical_stack_id(explicit)
        return _STACK_ALIASES.get(stack_id, stack_id)

    text = _profile_text(profile)
    if "typescript" in text and "react" in text and "vite" in text:
        return "react-vite"
    if "swift" in text and ("xcodegen" in text or "native-macos-app" in text):
        return "swift-xcodegen"
    if "go" in text and ("web-service" in text or "http" in text or "api" in text or "go-mod" in text):
        return "go-service"
    if "python" in text and ("cli" in text or "typer" in text or "command line" in text):
        return "python-cli"
    return ""


def stack_pack_for_delivery(profile: dict[str, Any] | None) -> StackPack | None:
    stack_id = stack_id_for_delivery(profile)
    if not stack_id:
        return None
    return STACK_PACKS.get(stack_id)


def apply_stack_pack_defaults(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not profile:
        return {}

    result = dict(profile)
    stack_id = stack_id_for_delivery(result)
    if not stack_id:
        return result

    result["stack_id"] = stack_id
    pack = STACK_PACKS.get(stack_id)
    if not pack:
        return result

    for key, value in pack.defaults.items():
        if result.get(key) in (None, "", []):
            result[key] = value
    return result
