"""Run Store: persist runs and steps to SQLite."""
from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, delete, insert, select, text, update
from sqlalchemy.engine import Engine

from code_minions.store.schema import metadata, run_events, runs, steps
from code_minions.types import (
    TERMINAL_RUN_STATUSES,
    TERMINAL_STEP_STATUSES,
    RunStatus,
    StepStatus,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _new_run_id() -> str:
    return "r_" + secrets.token_hex(4)


class RunStore:
    """Thin CRUD wrapper around the runs/steps tables."""

    def __init__(self, db_path: Path):
        self._engine: Engine = create_engine(f"sqlite:///{db_path}", future=True)
        metadata.create_all(self._engine)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._engine.begin() as conn:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(steps)")).all()}
            if "detail" not in cols:
                conn.execute(text("ALTER TABLE steps ADD COLUMN detail TEXT"))
            run_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(runs)")).all()}
            if "llm" not in run_cols:
                conn.execute(text("ALTER TABLE runs ADD COLUMN llm TEXT"))

    def create_run(self, workflow: str, inputs: dict[str, Any], llm: str | None = None) -> str:
        run_id = _new_run_id()
        with self._engine.begin() as conn:
            conn.execute(
                insert(runs).values(
                    id=run_id,
                    workflow=workflow,
                    status=RunStatus.PENDING.value,
                    llm=llm,
                    started_at=_now(),
                    ended_at=None,
                    input_json=json.dumps(inputs),
                )
            )
        return run_id

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
            return dict(row) if row else None

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(runs).order_by(runs.c.started_at.desc()).limit(limit)
            ).mappings().all()
            return [dict(r) for r in rows]

    def set_run_status(self, run_id: str, status: RunStatus) -> None:
        values: dict[str, Any] = {"status": status.value}
        if status in TERMINAL_RUN_STATUSES:
            values["ended_at"] = _now()
        with self._engine.begin() as conn:
            conn.execute(update(runs).where(runs.c.id == run_id).values(**values))

    def upsert_step(
        self,
        run_id: str,
        step_id: str,
        status: StepStatus,
        output: dict[str, Any] | None = None,
        error: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self._engine.begin() as conn:
            existing = conn.execute(
                select(steps).where(steps.c.run_id == run_id, steps.c.step_id == step_id)
            ).first()
            values: dict[str, Any] = {
                "status": status.value,
                "detail": detail,
                "output_json": json.dumps(output) if output is not None else None,
                "error": error,
            }
            if existing is None:
                values.update(
                    {
                        "run_id": run_id,
                        "step_id": step_id,
                        "started_at": _now(),
                        "ended_at": _now() if status in TERMINAL_STEP_STATUSES else None,
                    }
                )
                conn.execute(insert(steps).values(**values))
            else:
                if status in TERMINAL_STEP_STATUSES:
                    values["ended_at"] = _now()
                conn.execute(
                    update(steps)
                    .where(steps.c.run_id == run_id, steps.c.step_id == step_id)
                    .values(**values)
                )

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(steps).where(steps.c.run_id == run_id).order_by(steps.c.started_at.asc())
            ).mappings().all()
            return [dict(r) for r in rows]

    def append_run_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(run_events).values(
                    run_id=run_id,
                    event_type=event_type,
                    payload_json=json.dumps(payload),
                    created_at=_now(),
                )
            )

    def list_run_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(run_events)
                .where(run_events.c.run_id == run_id)
                .order_by(run_events.c.id.asc())
            ).mappings().all()
        events = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            events.append(item)
        return events

    def get_successful_outputs(self, run_id: str) -> dict[str, dict[str, Any]]:
        """Return {step_id: parsed_output} for all successful steps of the run.
        Includes for_each sub-step ids like 'x[0]' so resume can skip
        successful iterations inside a failed parent fan-out."""
        import json as _json
        result: dict[str, dict[str, Any]] = {}
        for s in self.list_steps(run_id):
            if s["status"] == "success" and s["output_json"]:
                result[s["step_id"]] = _json.loads(s["output_json"])
        return result

    def delete_run(self, run_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(run_events).where(run_events.c.run_id == run_id))
            conn.execute(delete(steps).where(steps.c.run_id == run_id))
            conn.execute(delete(runs).where(runs.c.id == run_id))
