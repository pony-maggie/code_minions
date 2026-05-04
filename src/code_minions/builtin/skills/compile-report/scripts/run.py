from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _yes_no(value: bool) -> str:
    return "Yes" if value else "No"


def _implementation_section(results: list[dict[str, Any]]) -> list[str]:
    lines = ["## Implementation", ""]
    lines.append(f"Total tickets implemented: {len(results)}")
    lines.append("")
    for idx, result in enumerate(results, start=1):
        lines.append(f"### Ticket {idx}")
        lines.append(f"- **Commit SHA:** `{result.get('commit_sha', '')}`")
        files = result.get("files_changed") or []
        lines.append("- **Files Changed:**")
        for path in files:
            lines.append(f"  - `{path}`")
        test_result = result.get("test_result") or {}
        lines.append(f"- **Test Result:** {'Passed' if test_result.get('passed') else 'Failed'}")
        lines.append(f"- **Rounds Used:** {result.get('rounds_used', '')}")
        review = result.get("review_report") or {}
        lines.append(f"- **Review Summary:** {review.get('summary', '')}")
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
    return lines


def run(ctx) -> dict[str, str]:
    output_path = ctx.inputs["output_path"]
    path = Path(ctx.workdir) / output_path
    lines = ["# Implementation Report", ""]
    lines.extend(_implementation_section(ctx.inputs["implement_results"]))
    lines.extend(_acceptance_section(ctx.inputs.get("acceptance_output")))
    path.write_text("\n".join(lines).rstrip() + "\n")
    return {"report_path": output_path}
