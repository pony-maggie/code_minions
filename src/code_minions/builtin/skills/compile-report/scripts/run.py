from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from code_minions.engine.failure_classification import classify_failure


def _yes_no(value: bool) -> str:
    return "Yes" if value else "No"


def _implementation_section(results: list[dict[str, Any]]) -> list[str]:
    lines = ["## Implementation", ""]
    lines.append(f"Total tickets implemented: {len(results)}")
    lines.append("")
    for idx, result in enumerate(results, start=1):
        lines.append(f"### Ticket {idx}")
        trace_id = result.get("trace_id")
        if trace_id:
            lines.append(f"- **Trace ID:** `{trace_id}`")
        lines.append(f"- **Commit SHA:** `{result.get('commit_sha', '')}`")
        files = result.get("files_changed") or []
        lines.append("- **Files Changed:**")
        for path in files:
            lines.append(f"  - `{path}`")
        test_result = result.get("test_result") or {}
        lines.append(f"- **Test Result:** {'Passed' if test_result.get('passed') else 'Failed'}")
        lines.append(f"- **Rounds Used:** {result.get('rounds_used', '')}")
        review = result.get("review_report") or {}
        lines.append(f"- **AI Review Summary:** {review.get('summary', '')}")
        lines.append("")
    return lines


def _acceptance_section(acceptance: dict[str, Any] | None) -> list[str]:
    if not acceptance:
        return []
    lines = ["## Product Acceptance", ""]
    lines.append(f"- **Accepted:** {_yes_no(bool(acceptance.get('accepted')))}")
    lines.append(f"- **Artifact Level:** {acceptance.get('artifact_level', 'unknown')}")
    evidence = acceptance.get("evidence") or {}
    lines.append(f"- **Build System:** {evidence.get('build_system', 'unknown')}")
    delivery_profile = evidence.get("delivery_profile")
    if delivery_profile:
        lines.append("- **Delivery Profile:**")
        lines.append("```json")
        lines.append(json.dumps(delivery_profile, ensure_ascii=False, indent=2, sort_keys=True))
        lines.append("```")
    lines.append("")

    blockers = acceptance.get("blockers") or []
    lines.append("### Blockers")
    if blockers:
        for issue in blockers:
            lines.append(f"- `{issue.get('code', 'unknown')}`: {issue.get('message', '')}")
    else:
        lines.append("- None")
    lines.append("")

    warnings = acceptance.get("warnings") or []
    lines.append("### Warnings")
    if warnings:
        for issue in warnings:
            lines.append(f"- `{issue.get('code', 'unknown')}`: {issue.get('message', '')}")
    else:
        lines.append("- None")
    lines.append("")

    coverage = acceptance.get("coverage") or []
    lines.append("### Coverage")
    if coverage:
        lines.append("| Task | Status | Tests | Files |")
        lines.append("|---|---|---|---|")
        for row in coverage:
            files = ", ".join(f"`{p}`" for p in row.get("files_changed", []))
            lines.append(
                f"| {row.get('id', '')}: {row.get('title', '')} "
                f"| {row.get('status', '')} "
                f"| {_yes_no(bool(row.get('tests_passed')))} "
                f"| {files} |"
            )
    else:
        lines.append("- No coverage rows")
    lines.append("")

    acceptance_items = acceptance.get("acceptance_items") or []
    lines.append("### Acceptance Items")
    if acceptance_items:
        lines.append("| Item | Kind | Status | Message |")
        lines.append("|---|---|---|---|")
        for item in acceptance_items:
            lines.append(
                f"| `{item.get('id', '')}` "
                f"| {item.get('kind', '')} "
                f"| {item.get('status', '')} "
                f"| {item.get('message', '')} |"
            )
    else:
        lines.append("- No acceptance items")
    lines.append("")

    verifier_rounds = acceptance.get("verifier_rounds") or []
    lines.append("### Verifier Rounds")
    if verifier_rounds:
        lines.append("| Round | Verifier | Status | Feedback |")
        lines.append("|---|---|---|---|")
        for round_ in verifier_rounds:
            lines.append(
                f"| `{round_.get('id', '')}` "
                f"| {round_.get('verifier', '')} "
                f"| {round_.get('status', '')} "
                f"| {round_.get('feedback', '')} |"
            )
    else:
        lines.append("- No verifier rounds")
    lines.append("")
    return lines


def _browser_acceptance_section(browser_acceptance: dict[str, Any] | None) -> list[str]:
    if not browser_acceptance:
        return []
    lines = ["## Browser Acceptance", ""]
    lines.append(f"- **Accepted:** {_yes_no(bool(browser_acceptance.get('accepted')))}")
    lines.append(f"- **Supported:** {_yes_no(bool(browser_acceptance.get('supported')))}")
    lines.append(f"- **Stack:** `{browser_acceptance.get('stack_id', '')}`")
    lines.append("")

    artifacts = browser_acceptance.get("artifacts") or {}
    lines.append("### Artifacts")
    if artifacts:
        for name, path in artifacts.items():
            lines.append(f"- **{name}:** `{path}`")
    else:
        lines.append("- None")
    lines.append("")

    scenarios = browser_acceptance.get("scenarios") or []
    lines.append("### Scenarios")
    if scenarios:
        lines.append("| Scenario | Status | Message |")
        lines.append("|---|---|---|")
        for scenario in scenarios:
            lines.append(
                f"| `{scenario.get('id', '')}` "
                f"| {scenario.get('status', '')} "
                f"| {scenario.get('message', '')} |"
            )
    else:
        lines.append("- No browser scenarios")
    lines.append("")
    return lines


def _failure_classification_section(inputs: dict[str, Any]) -> list[str]:
    acceptance = inputs.get("acceptance_output") or {}
    browser_acceptance = inputs.get("browser_acceptance_output") or {}
    failed_output: dict[str, Any] = {}
    if acceptance and not acceptance.get("accepted", True):
        failed_output = {"acceptance": acceptance}
    elif browser_acceptance and not browser_acceptance.get("accepted", True):
        failed_output = {"browser_acceptance": browser_acceptance}
    if not failed_output:
        return []
    classification = classify_failure(step_output=failed_output)
    return [
        "## Failure Classification",
        "",
        f"- **Classification:** `{classification.classification}`",
        f"- **Message:** {classification.message}",
        f"- **Next Action:** {classification.next_action}",
        "",
    ]


def _traceability_rows(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    results = inputs.get("implement_results") or []
    acceptance = inputs.get("acceptance_output") or {}
    coverage = acceptance.get("coverage") or []
    rows: list[dict[str, Any]] = []
    max_len = max(len(results), len(coverage))
    for idx in range(max_len):
        result = results[idx] if idx < len(results) else {}
        row = coverage[idx] if idx < len(coverage) else {}
        rows.append({
            "trace_id": result.get("trace_id") or row.get("trace_id") or f"cm_task_{idx + 1}",
            "task_id": result.get("task_id") or row.get("id") or f"task-{idx + 1}",
            "title": result.get("task_title") or row.get("title") or "",
            "status": row.get("status") or "",
            "commit_sha": result.get("commit_sha", ""),
            "files_changed": result.get("files_changed") or row.get("files_changed") or [],
            "tests_passed": bool((result.get("test_result") or {}).get("passed") or row.get("tests_passed")),
        })
    return rows


def _write_json(path: Path, data: Any) -> str:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return path.as_posix()


def _write_evidence_artifacts(workdir: Path, inputs: dict[str, Any]) -> list[str]:
    evidence_dir = workdir / ".devflow" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    payloads = {
        "implementation-results.json": inputs.get("implement_results") or [],
        "acceptance-output.json": inputs.get("acceptance_output") or {},
        "browser-acceptance-output.json": inputs.get("browser_acceptance_output") or {},
        "traceability.json": _traceability_rows(inputs),
    }
    for filename, payload in payloads.items():
        paths.append(_write_json(evidence_dir / filename, payload))

    trace_lines = [
        "# Traceability",
        "",
        "| Trace | Task | Status | Tests | Commit | Files |",
        "|---|---|---|---|---|---|",
    ]
    for row in payloads["traceability.json"]:
        files = ", ".join(f"`{path}`" for path in row["files_changed"])
        trace_lines.append(
            f"| `{row['trace_id']}` "
            f"| {row['task_id']}: {row['title']} "
            f"| {row['status']} "
            f"| {_yes_no(row['tests_passed'])} "
            f"| `{row['commit_sha']}` "
            f"| {files} |"
        )
    traceability_md = evidence_dir / "traceability.md"
    traceability_md.write_text("\n".join(trace_lines).rstrip() + "\n")
    paths.append(traceability_md.as_posix())
    return [str(Path(path).relative_to(workdir)) for path in paths]


def _evidence_artifacts_section(paths: list[str]) -> list[str]:
    lines = ["## Evidence Artifacts", ""]
    for path in paths:
        lines.append(f"- `{path}`")
    lines.append("")
    return lines


def run(ctx) -> dict[str, Any]:
    output_path = ctx.inputs["output_path"]
    workdir = Path(ctx.workdir)
    path = workdir / output_path
    evidence_paths = _write_evidence_artifacts(workdir, ctx.inputs)
    lines = [
        "# Implementation Report",
        "",
        "## Deterministic Evidence",
        "",
        "The implementation, browser acceptance, and product acceptance sections below are assembled from deterministic workflow outputs unless a field is explicitly labelled as AI narrative.",
        "",
        "## AI Narrative",
        "",
        "AI-generated review summaries are labelled `AI Review Summary`; verify them against the deterministic evidence in this report and the run worktree.",
        "",
    ]
    lines.extend(_evidence_artifacts_section(evidence_paths))
    lines.extend(_failure_classification_section(ctx.inputs))
    lines.extend(_implementation_section(ctx.inputs["implement_results"]))
    lines.extend(_browser_acceptance_section(ctx.inputs.get("browser_acceptance_output")))
    lines.extend(_acceptance_section(ctx.inputs.get("acceptance_output")))
    path.write_text("\n".join(lines).rstrip() + "\n")
    return {"report_path": output_path, "evidence_paths": evidence_paths}
