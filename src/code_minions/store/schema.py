"""SQLAlchemy Core schema for Run Store."""
from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

runs = Table(
    "runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("workflow", String, nullable=False),
    Column("status", String, nullable=False),
    Column("llm", String, nullable=True),
    Column("started_at", DateTime, nullable=False),
    Column("ended_at", DateTime, nullable=True),
    Column("input_json", Text, nullable=False),
)

steps = Table(
    "steps",
    metadata,
    Column("run_id", String, nullable=False),
    Column("step_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("detail", Text, nullable=True),
    Column("output_json", Text, nullable=True),
    Column("error", Text, nullable=True),
    Column("started_at", DateTime, nullable=True),
    Column("ended_at", DateTime, nullable=True),
    UniqueConstraint("run_id", "step_id", name="uq_run_step"),
)

run_events = Table(
    "run_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, nullable=False),
    Column("event_type", String, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("created_at", DateTime, nullable=False),
)
