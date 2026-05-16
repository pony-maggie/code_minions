"""Deterministic project memory for reusable workflow facts."""
from __future__ import annotations

from pathlib import Path
from typing import Any

HEADER = "# code_minions Project Memory\n\n"
MAX_RUN_ENTRIES = 20


def memory_path(project_root: Path) -> Path:
    return Path(project_root) / ".devflow" / "memory.md"


def read_project_memory(project_root: Path, *, limit: int = 6000) -> str:
    path = memory_path(project_root)
    if not path.is_file():
        return ""
    text = path.read_text().strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def update_project_memory(
    project_root: Path,
    *,
    run_id: str,
    workflow: str,
    status: str,
    outputs: dict[str, dict[str, Any]],
) -> None:
    path = memory_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else HEADER
    entries = _existing_entries(existing)
    entry = _render_run_entry(run_id=run_id, workflow=workflow, status=status, outputs=outputs)
    entries = [item for item in entries if f"## Run `{run_id}`" not in item]
    entries.append(entry)
    entries = entries[-MAX_RUN_ENTRIES:]
    path.write_text(HEADER + "\n\n".join(entries).rstrip() + "\n")


def _existing_entries(text: str) -> list[str]:
    entries: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("## Run `"):
            if current:
                entries.append("\n".join(current).strip())
            current = [line]
        elif current:
            current.append(line)
    if current:
        entries.append("\n".join(current).strip())
    return [entry for entry in entries if entry]


def _render_run_entry(
    *,
    run_id: str,
    workflow: str,
    status: str,
    outputs: dict[str, dict[str, Any]],
) -> str:
    lines = [
        f"## Run `{run_id}`",
        f"- Workflow: workflow `{workflow}`",
        f"- Status: `{status}`",
    ]
    acceptance = _first_output_with_key(outputs, "accepted")
    if acceptance:
        lines.append(f"- Product accepted: `{bool(acceptance.get('accepted'))}`")
        artifact_level = acceptance.get("artifact_level")
        if artifact_level:
            lines.append(f"- Artifact level: `{artifact_level}`")
        evidence = acceptance.get("evidence") or {}
        build_system = evidence.get("build_system")
        if build_system:
            lines.append(f"- Build system: `{build_system}`")
        profile = evidence.get("delivery_profile") or {}
        stack_id = profile.get("stack_id")
        if stack_id:
            lines.append(f"- Delivery stack: `{stack_id}`")
    implement_count = _implementation_result_count(outputs)
    if implement_count:
        lines.append(f"- Implementation result count: `{implement_count}`")
    return "\n".join(lines)


def _first_output_with_key(outputs: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    for output in outputs.values():
        if key in output:
            return output
        items = output.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and key in item:
                    return item
    return {}


def _implementation_result_count(outputs: dict[str, dict[str, Any]]) -> int:
    count = 0
    for output in outputs.values():
        if "commit_sha" in output or "files_changed" in output:
            count += 1
        items = output.get("items")
        if isinstance(items, list):
            count += sum(
                1
                for item in items
                if isinstance(item, dict) and ("commit_sha" in item or "files_changed" in item)
            )
    return count
