"""summarize-file skill entrypoint.

Reads the target file deterministically, then asks the configured LLM for a
single-paragraph summary. This keeps the smoke test focused on LLM connectivity
without depending on provider-specific tool-call behavior.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from code_minions.llm.types import Message

MAX_SUMMARY_INPUT_CHARS = 20_000


def _relative_project_path(user_path: str) -> Path:
    raw = Path(user_path)
    if raw.is_absolute():
        raise ValueError("path must be relative to the project root")
    return raw


def _resolve_inside(root_dir: Path, relative_path: Path) -> Path:
    root = root_dir.resolve()
    target = (root / relative_path).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"path is outside project root: {relative_path}")
    return target


def _parse_summary(content: str) -> str:
    text = content.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            data = {}
        summary = data.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
    return text


def run(ctx):
    if ctx.llm is None:
        raise RuntimeError("summarize-file requires a configured LLM provider")

    relative_path = _relative_project_path(ctx.inputs["path"])
    workdir = Path(ctx.workdir)
    project_root = Path(ctx.extras.get("project_root", workdir))
    workspace_mode = ctx.extras.get("workspace_mode", "git-worktree")
    path = _resolve_inside(workdir, relative_path)
    if not path.is_file():
        project_path = _resolve_inside(project_root, relative_path)
        if workspace_mode == "git-worktree" and project_path.is_file():
            raise ValueError(
                f"path exists in project root but not in the run worktree: {relative_path}. "
                "Commit the file before running this workflow."
            )
        raise ValueError(f"path is not a file relative to project root: {relative_path}")

    content = path.read_text()
    byte_count = len(path.read_bytes())
    truncated = content[:MAX_SUMMARY_INPUT_CHARS]
    truncation_note = (
        "\n\n[Input truncated for summarization.]"
        if len(content) > MAX_SUMMARY_INPUT_CHARS
        else ""
    )

    resp = ctx.llm.chat(
        messages=[
            Message(
                role="system",
                content=(
                    "Summarize the provided file in one concise paragraph. "
                    "Reply with JSON only: {\"summary\": \"...\"}."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Path: {ctx.inputs['path']}\n\n"
                    f"File contents:\n{truncated}{truncation_note}"
                ),
            ),
        ],
        temperature=0.1,
    )
    return {
        "summary": _parse_summary(resp.message.content),
        "byte_count": byte_count,
    }
