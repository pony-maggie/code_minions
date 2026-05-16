"""Built-in local tools exposed to LLM-path skills."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

MAX_BASH_TIMEOUT_SECONDS = 600
MAX_TOOL_OUTPUT_CHARS = 12_000
IGNORED_GLOB_PARTS = {".git", ".devflow", "build", "coverage", "dist", "node_modules"}


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


def _directory_listing(path: Path, workdir: Path) -> str:
    root = workdir.resolve()
    rel = path.relative_to(root)
    entries = []
    for child in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        suffix = "/" if child.is_dir() else ""
        entries.append(f"{child.name}{suffix}")
    if not entries:
        return f"directory {rel} is empty"
    return _truncate(f"directory {rel}:\n" + "\n".join(entries))


def _first_present(arguments: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in arguments:
            return arguments[name]
    return None


def _path_argument(arguments: dict[str, Any]) -> str:
    path = _first_present(arguments, ("path", "file_path", "filePath", "filepath", "pathname"))
    if not path:
        raise ValueError("path is required; aliases file_path, filePath, filepath, and pathname are also accepted")
    return str(path)


def _glob_listing(workdir: Path, pattern: str) -> str:
    if Path(pattern).is_absolute():
        raise ValueError("pattern must be relative to workdir")
    root = workdir.resolve()
    matches: list[str] = []
    for path in root.glob(pattern):
        rel = path.relative_to(root)
        if any(part in IGNORED_GLOB_PARTS for part in rel.parts):
            continue
        suffix = "/" if path.is_dir() else ""
        matches.append(f"{rel.as_posix()}{suffix}")
    matches = sorted(set(matches))
    if not matches:
        return f"no matches for {pattern}"
    return _truncate("\n".join(matches))


def run_local_tool(name: str, arguments: dict[str, Any], workdir: Path) -> str:
    """Run a built-in local tool inside a run workspace."""
    if name == "Read":
        path = _resolve_inside(workdir, _path_argument(arguments))
        if path.is_dir():
            return _directory_listing(path, workdir)
        if not path.is_file():
            raise ValueError("path is not a file or directory")
        return _truncate(path.read_text())

    if name == "Glob":
        pattern = str(arguments.get("pattern") or arguments.get("glob") or arguments.get("path") or "**/*")
        return _glob_listing(workdir, pattern)

    if name == "Write":
        path = _resolve_inside(workdir, _path_argument(arguments))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments["content"])
        return f"wrote {path.relative_to(workdir.resolve())}"

    if name == "Edit":
        path = _resolve_inside(workdir, _path_argument(arguments))
        if not path.is_file():
            raise ValueError("path is not a file")
        text = path.read_text()
        old = _first_present(arguments, (
            "old_text", "oldText", "old_string", "oldString", "oldstring",
            "old_content", "oldContent", "old", "search", "find", "target", "original",
        ))
        new = _first_present(arguments, (
            "new_text", "newText", "new_string", "newString", "newstring",
            "new_content", "newContent", "new", "replace", "with", "replacement", "updated",
        ))
        if old is None or new is None:
            keys = ", ".join(sorted(arguments.keys()))
            raise ValueError(
                "old_text and new_text are required; aliases oldText/newText, "
                "old_string/new_string, oldString/newString, oldstring/newstring, oldContent/newContent, "
                "old/new, search/replace, find/with, target/replacement, and original/updated "
                f"are also accepted; received keys: {keys}"
            )
        if text.count(old) != 1:
            raise ValueError("old_text must match exactly one location")
        path.write_text(text.replace(old, new, 1))
        return f"updated {path.relative_to(workdir.resolve())}"

    if name == "Delete":
        path = _resolve_inside(workdir, _path_argument(arguments))
        if not path.is_file():
            raise ValueError("path is not a file")
        rel = path.relative_to(workdir.resolve())
        path.unlink()
        return f"deleted {rel}"

    if name in {"Bash", "Command"}:
        timeout = min(int(arguments.get("timeout", 300)), MAX_BASH_TIMEOUT_SECONDS)
        command = arguments.get("command") or arguments.get("cmd")
        if not command:
            raise ValueError("command is required")
        result = subprocess.run(
            command,
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
