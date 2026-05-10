from __future__ import annotations

from pathlib import Path
from typing import Any

from code_minions.delivery import infer_delivery_profile, language_counts, validate_delivery_profile

IGNORED_DIRS = {".git", ".devflow", ".pytest_cache", "__pycache__", ".ruff_cache"}


def _all_files(workdir: Path) -> list[Path]:
    files: list[Path] = []
    for path in workdir.rglob("*"):
        rel_parts = path.relative_to(workdir).parts
        if any(part in IGNORED_DIRS for part in rel_parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def _prd_text(structured_prd: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("goal", "constraints", "features", "non_functional"):
        value = structured_prd.get(key)
        parts.append(str(value))
    return "\n".join(parts)


def _requires_macos_swift(structured_prd: dict[str, Any]) -> tuple[bool, bool]:
    text = _prd_text(structured_prd).lower()
    requires_macos = "macos" in text or "mac os" in text or "mac app" in text
    requires_swift = "swift" in text or "swiftui" in text or "appkit" in text
    return requires_macos, requires_swift


def _build_system(workdir: Path) -> str:
    if (workdir / "Package.swift").exists():
        return "swift-package"
    if (workdir / "project.yml").exists():
        return "xcodegen"
    if any(workdir.glob("*.xcodeproj")):
        return "xcodeproj"
    if (workdir / "go.mod").exists():
        return "go-mod"
    if (workdir / "package.json").exists():
        return "npm"
    if (workdir / "pyproject.toml").exists() or (workdir / "pytest.ini").exists():
        return "python"
    return "unknown"


def _has_swift_app_entry(files: list[Path]) -> bool:
    for path in files:
        if path.suffix != ".swift":
            continue
        text = path.read_text(errors="ignore")
        if "@main" in text and (": App" in text or "NSApplication" in text):
            return True
    return False


def _has_tests(files: list[Path]) -> bool:
    return any("test" in path.name.lower() or "tests" in {p.lower() for p in path.parts} for path in files)


def _artifact_level(evidence: dict[str, Any]) -> str:
    languages = evidence["languages"]
    if evidence["has_swift_app_entry"] and evidence["build_system"] in {
        "xcodegen",
        "xcodeproj",
        "swift-package",
    }:
        if evidence["has_localization"] or evidence["has_entitlements"]:
            return "mvp-candidate"
        return "app-skeleton"
    if languages.get("swift", 0) > 0:
        return "library"
    return "prototype"


def _task_coverage(tasks: list[dict[str, Any]], implement_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, task in enumerate(tasks):
        result = implement_results[idx] if idx < len(implement_results) else {}
        files = result.get("files_changed") or []
        test_result = result.get("test_result") or {}
        status = "passed" if test_result.get("passed") and files else "missing-evidence"
        rows.append({
            "id": task.get("id", f"task-{idx + 1}"),
            "title": task.get("title") or task.get("name") or "",
            "status": status,
            "files_changed": files,
            "tests_passed": bool(test_result.get("passed")),
        })
    return rows


def _issue(code: str, message: str, severity: str = "blocker") -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def _acceptance_item(
    item_id: str,
    *,
    title: str,
    kind: str,
    status: str,
    message: str = "",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "title": title,
        "kind": kind,
        "status": status,
        "message": message,
        "evidence": evidence or {},
    }


def _task_acceptance_item(row: dict[str, Any]) -> dict[str, Any]:
    status = "pass" if row["status"] != "missing-evidence" else "fail"
    message = (
        "Task has passing test evidence and changed files."
        if status == "pass"
        else "Task has no passing test evidence or changed files."
    )
    return _acceptance_item(
        f"task:{row['id']}",
        title=row["title"] or row["id"],
        kind="task",
        status=status,
        message=message,
        evidence={
            "task_id": row["id"],
            "files_changed": row["files_changed"],
            "tests_passed": row["tests_passed"],
        },
    )


def _verifier_round(items: list[dict[str, Any]]) -> dict[str, Any]:
    failures = [item for item in items if item["status"] == "fail"]
    warnings = [item for item in items if item["status"] == "warn"]
    if failures:
        feedback = "\n".join(
            f"Blocking acceptance item failed: {item['id']} - {item.get('message') or item.get('title', '')}"
            for item in failures
        )
    elif warnings:
        feedback = "\n".join(
            f"Acceptance warning: {item['id']} - {item.get('message') or item.get('title', '')}"
            for item in warnings
        )
    else:
        feedback = "All acceptance items passed."
    return {
        "id": "acceptance-verifier-1",
        "qc_no": 1,
        "verifier": "deterministic-acceptance-verifier",
        "status": "fail" if failures else "pass",
        "verdict": {
            "pass": not failures,
            "failures": len(failures),
            "warnings": len(warnings),
        },
        "feedback": feedback,
        "input_item_ids": [item["id"] for item in items],
    }


def run(ctx) -> dict[str, Any]:
    workdir = Path(ctx.workdir)
    structured_prd = ctx.inputs["structured_prd"]
    tasks = ctx.inputs["tasks"]
    implement_results = ctx.inputs["implement_results"]

    files = _all_files(workdir)
    languages = language_counts(workdir)
    evidence = {
        "file_count": len(files),
        "languages": languages,
        "build_system": _build_system(workdir),
        "has_swift_app_entry": _has_swift_app_entry(files),
        "has_tests": _has_tests(files),
        "has_entitlements": any(path.suffix == ".entitlements" for path in files),
        "has_localization": any(path.suffix == ".strings" for path in files),
    }
    artifact_level = _artifact_level(evidence)
    coverage = _task_coverage(tasks, implement_results)
    delivery_profile = infer_delivery_profile(structured_prd)

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    acceptance_items = [_task_acceptance_item(row) for row in coverage]
    delivery_issues = validate_delivery_profile(workdir, delivery_profile)
    if delivery_issues:
        for issue in delivery_issues:
            item_status = "warn" if issue.get("severity", "error") == "warning" else "fail"
            acceptance_items.append(_acceptance_item(
                f"delivery-profile:{issue['code']}",
                title=issue["code"],
                kind="delivery-profile",
                status=item_status,
                message=issue["message"],
                evidence={"paths": issue.get("paths", []), "repair_hint": issue.get("repair_hint", "")},
            ))
    elif delivery_profile:
        acceptance_items.append(_acceptance_item(
            "delivery-profile",
            title="Delivery profile",
            kind="delivery-profile",
            status="pass",
            message="Delivery profile validation passed.",
            evidence={"stack_id": delivery_profile.get("stack_id", "")},
        ))

    for issue in delivery_issues:
        if issue.get("severity", "error") == "warning":
            warnings.append(_issue(issue["code"], issue["message"], severity="warning"))
        else:
            blockers.append(_issue(issue["code"], issue["message"]))

    requires_macos, requires_swift = _requires_macos_swift(structured_prd)
    if requires_swift and languages.get("swift", 0) == 0:
        message = "PRD requires Swift/SwiftUI/AppKit, but the worktree contains no Swift implementation files."
        blockers.append(_issue(
            "language-mismatch",
            message,
        ))
        acceptance_items.append(_acceptance_item(
            "platform:language-mismatch",
            title="Swift implementation files",
            kind="platform",
            status="fail",
            message=message,
            evidence={"languages": languages},
        ))
    if requires_macos and not evidence["has_swift_app_entry"]:
        message = "PRD requires a macOS native application, but no Swift app entry point was found."
        blockers.append(_issue(
            "platform-mismatch",
            message,
        ))
        acceptance_items.append(_acceptance_item(
            "platform:platform-mismatch",
            title="macOS app entry point",
            kind="platform",
            status="fail",
            message=message,
            evidence={"has_swift_app_entry": evidence["has_swift_app_entry"]},
        ))
    if requires_macos and evidence["build_system"] not in {"xcodegen", "xcodeproj", "swift-package"}:
        message = "PRD requires a macOS deliverable, but no Xcode, XcodeGen, or SwiftPM build definition was found."
        blockers.append(_issue(
            "missing-macos-build",
            message,
        ))
        acceptance_items.append(_acceptance_item(
            "platform:missing-macos-build",
            title="macOS build definition",
            kind="platform",
            status="fail",
            message=message,
            evidence={"build_system": evidence["build_system"]},
        ))
    for row in coverage:
        if row["status"] == "missing-evidence":
            blockers.append(_issue("missing-task-evidence", f"Task {row['id']} has no passing test evidence or changed files."))
    if languages.get("python", 0) and requires_swift:
        message = "Python files are present in a PRD that asks for Swift/macOS; classify this as prototype evidence only."
        warnings.append(_issue(
            "prototype-language",
            message,
            severity="warning",
        ))
        acceptance_items.append(_acceptance_item(
            "artifact:prototype-language",
            title="Prototype language evidence",
            kind="artifact",
            status="warn",
            message=message,
            evidence={"languages": languages},
        ))

    verifier_rounds = [_verifier_round(acceptance_items)]

    return {
        "accepted": not blockers,
        "artifact_level": artifact_level,
        "coverage": coverage,
        "acceptance_items": acceptance_items,
        "verifier_rounds": verifier_rounds,
        "blockers": blockers,
        "warnings": warnings,
        "evidence": {**evidence, "delivery_profile": delivery_profile},
    }
