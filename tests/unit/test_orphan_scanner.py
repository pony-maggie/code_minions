"""Tests for orphan scanner: marks stuck 'running' runs as failed when web restarts."""
from __future__ import annotations

import os
from pathlib import Path

from code_minions.types import RunStatus


def test_scan_marks_stale_running_as_failed(tmp_path: Path) -> None:
    from code_minions.store.run_store import RunStore
    from code_minions.web.background import scan_orphans

    db = tmp_path / "runs.db"
    store = RunStore(db)
    run_id = store.create_run(workflow="demo", inputs={})
    store.set_run_status(run_id, RunStatus.RUNNING)

    # pid file pointing to a pid that doesn't exist
    pid_file = tmp_path / "web.pid"
    pid_file.write_text("999999")  # virtually guaranteed no process

    scan_orphans(store=store, pid_file=pid_file)

    row = store.get_run(run_id)
    assert row["status"] == "failed"
    # After scan, pid file should be updated to current pid
    assert int(pid_file.read_text().strip()) == os.getpid()


def test_scan_leaves_active_pid_alone(tmp_path: Path) -> None:
    from code_minions.store.run_store import RunStore
    from code_minions.web.background import scan_orphans

    db = tmp_path / "runs.db"
    store = RunStore(db)
    run_id = store.create_run(workflow="demo", inputs={})
    store.set_run_status(run_id, RunStatus.RUNNING)

    pid_file = tmp_path / "web.pid"
    pid_file.write_text(str(os.getpid()))  # we're alive

    # Need a "different" pid that's also alive for this test to be meaningful;
    # since we can't easily fake another live pid, the scanner's rule is:
    # "if the prior pid equals our own pid, we're restarting — clean up."
    # So write a DIFFERENT live pid: use os.getppid() (parent is alive while pytest runs)
    pid_file.write_text(str(os.getppid()))

    scan_orphans(store=store, pid_file=pid_file)

    # When the prior pid is a different live process, running runs are left alone
    assert store.get_run(run_id)["status"] == "running"


def test_scan_handles_missing_pid_file(tmp_path: Path) -> None:
    from code_minions.store.run_store import RunStore
    from code_minions.web.background import scan_orphans

    db = tmp_path / "runs.db"
    store = RunStore(db)
    run_id = store.create_run(workflow="demo", inputs={})
    store.set_run_status(run_id, RunStatus.RUNNING)

    pid_file = tmp_path / "web.pid"  # does not exist
    scan_orphans(store=store, pid_file=pid_file)

    # No prior pid → treat as "first run or crashed without writing pid" → orphan
    assert store.get_run(run_id)["status"] == "failed"
    assert int(pid_file.read_text().strip()) == os.getpid()
