"""Deterministic workflow sensors."""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_minions.gates import GateFinding

BLOCKING_SEVERITIES = {"blocker", "error"}


@dataclass(frozen=True)
class SensorRun:
    name: str
    passed: bool
    finding: GateFinding


def run_sensor(
    *,
    name: str,
    spec: Any,
    workdir: Path,
) -> SensorRun:
    sensor_type = getattr(spec, "type", "")
    if sensor_type != "command":
        raise ValueError(f"unsupported sensor type {sensor_type!r}")
    return _run_command_sensor(name=name, spec=spec, workdir=workdir)


def _run_command_sensor(*, name: str, spec: Any, workdir: Path) -> SensorRun:
    command = getattr(spec, "command", None)
    if not command:
        raise ValueError(f"sensor {name} command is required")
    argv = command if isinstance(command, list) else shlex.split(str(command))
    if not argv:
        raise ValueError(f"sensor {name} command is empty")
    timeout = int(getattr(spec, "timeout_seconds", 300) or 300)
    severity = str(getattr(spec, "severity", "blocker") or "blocker")
    try:
        result = subprocess.run(
            argv,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = _compact_output(result.stdout, result.stderr)
        passed = result.returncode == 0
        message = (
            f"Command sensor `{name}` passed."
            if passed else
            f"Command sensor `{name}` failed with exit code {result.returncode}.\n{output}"
        )
    except subprocess.TimeoutExpired as e:
        passed = False
        output = _compact_output(e.stdout or "", e.stderr or "")
        message = f"Command sensor `{name}` timed out after {timeout}s.\n{output}"
    finding = GateFinding(
        code=f"sensor-{name}",
        severity=severity,
        stage="sensor",
        message=message.strip(),
        repair_hint=f"Run and fix this command: {' '.join(argv)}",
        source="workflow-sensor",
    )
    return SensorRun(name=name, passed=passed, finding=finding)


def _compact_output(stdout: str, stderr: str, *, limit: int = 4000) -> str:
    output = f"stdout:\n{stdout}\nstderr:\n{stderr}".strip()
    if len(output) <= limit:
        return output
    head = limit // 2
    tail = limit - head
    return f"{output[:head]}\n...[truncated]...\n{output[-tail:]}"
