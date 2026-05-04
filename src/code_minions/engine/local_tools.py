"""Built-in local tools exposed to LLM-path skills."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

MAX_BASH_TIMEOUT_SECONDS = 600
MAX_TOOL_OUTPUT_CHARS = 12_000


def _resolve_inside(workdir: Path, user_path: str) -> Path:
    raw = Path(user_path)
    if raw.is_absolute():
        raise ValueError("path must be relative to workdir")
    root = workdir.resolve()
    target = (root / raw).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"path is outside workdir: {user_path}")
    return target


def _truncate(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text
    return text[-MAX_TOOL_OUTPUT_CHARS:]


def run_local_tool(name: str, arguments: dict[str, Any], workdir: Path) -> str:
    """Run a built-in local tool inside a run workspace."""
    if name == "Read":
        path = _resolve_inside(workdir, arguments["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        return path.read_text()

    if name == "Write":
        path = _resolve_inside(workdir, arguments["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments["content"])
        return f"wrote {path.relative_to(workdir.resolve())}"

    if name == "Edit":
        path = _resolve_inside(workdir, arguments["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        text = path.read_text()
        old = arguments["old_text"]
        new = arguments["new_text"]
        if text.count(old) != 1:
            raise ValueError("old_text must match exactly one location")
        path.write_text(text.replace(old, new, 1))
        return f"updated {path.relative_to(workdir.resolve())}"

    if name == "Delete":
        path = _resolve_inside(workdir, arguments["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        rel = path.relative_to(workdir.resolve())
        path.unlink()
        return f"deleted {rel}"

    if name == "Bash":
        timeout = min(int(arguments.get("timeout", 300)), MAX_BASH_TIMEOUT_SECONDS)
        result = subprocess.run(
            arguments["command"],
            cwd=workdir,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return (
            f"exit_code={result.returncode}\n"
            f"stdout:\n{_truncate(result.stdout)}\n"
            f"stderr:\n{_truncate(result.stderr)}"
        )

    raise ValueError(f"unknown local tool: {name}")
