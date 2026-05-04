"""Background maintenance: orphan scanner + pid-file management for the web process.

The web process writes its pid into `.devflow/web.pid`. On startup, if a stale
pid is found (process no longer exists), any run stuck in `running` was left
behind by the previous web crash — mark it as failed so the user knows to resume.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from code_minions.engine.engine import Engine
from code_minions.store.run_store import RunStore
from code_minions.types import RunStatus, StepStatus


def _pid_alive(pid: int) -> bool:
    """Return True if a process with the given pid is running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    return True


def scan_orphans(store: RunStore, pid_file: Path) -> None:
    """On web startup, mark stale `running` runs as failed, then record current pid.

    Rules:
    - If pid_file doesn't exist, or points to a dead pid, or points to our own pid
      (a restart) → all `running` rows are orphans → mark failed.
    - If pid_file points to a different live pid → another web process is (presumed)
      actively driving runs; leave `running` alone.
    - Always rewrite pid_file with our current pid at the end.
    """
    prior_pid: int | None = None
    if pid_file.exists():
        try:
            prior_pid = int(pid_file.read_text().strip())
        except ValueError:
            prior_pid = None

    is_other_live = (
        prior_pid is not None
        and prior_pid != os.getpid()
        and _pid_alive(prior_pid)
    )

    if not is_other_live:
        for run in store.list_runs(limit=100):
            if run["status"] == RunStatus.RUNNING.value:
                store.upsert_step(
                    run_id=run["id"],
                    step_id="__orphaned__",
                    status=StepStatus.FAILED,
                    error="orphaned: web process restarted before run completed; use 'code-minions resume' to continue",
                )
                store.set_run_status(run["id"], RunStatus.FAILED)

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))


def start_run_in_background(
    engine: Engine, run_id: str, workflow: str, inputs: dict[str, Any]
) -> None:
    """Used as a FastAPI BackgroundTasks target.

    Caller has already created the run row; we just drive execution.
    Exceptions from execute_run are recorded in DB (failed status + __setup__
    step), so we swallow anything else as paranoia.
    """
    with contextlib.suppress(Exception):
        engine.execute_run(run_id, workflow, inputs)
