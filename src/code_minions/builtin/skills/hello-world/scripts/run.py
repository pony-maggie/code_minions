"""hello-world skill entrypoint.

Writes a greeting to the run workspace and returns its path + content.
"""
from __future__ import annotations

from pathlib import Path


def run(ctx):
    name = ctx.inputs["name"]
    greeting = f"hello, {name}!"
    out_path: Path = Path(ctx.workdir) / "greeting.txt"
    out_path.write_text(greeting + "\n")
    return {
        "file_path": str(out_path),
        "greeting": greeting,
    }
