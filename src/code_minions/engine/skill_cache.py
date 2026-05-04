"""Persistent cache for deterministic, opt-in LLM skill outputs."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from code_minions.engine.skill import Skill

FILE_INPUT_KEYS = {"file", "path", "prd", "prd_file"}


class SkillCache:
    """Small SQLite-backed cache keyed by skill metadata, inputs, and LLM identity."""

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT output_json FROM skill_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        return data if isinstance(data, dict) else None

    def put(self, key: str, output: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO skill_cache(cache_key, output_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                  output_json = excluded.output_json,
                  created_at = excluded.created_at
                """,
                (
                    key,
                    json.dumps(output, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_cache (
                  cache_key TEXT PRIMARY KEY,
                  output_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )


def build_skill_cache_key(
    *,
    skill: Skill,
    inputs: dict[str, Any],
    workdir: Path,
    llm: Any,
) -> str:
    payload = {
        "version": 1,
        "skill": {
            "name": skill.name,
            "metadata": skill.meta.model_dump(mode="json"),
            "instructions_sha256": _hash_text(skill.instructions),
        },
        "inputs": _fingerprint_value(inputs, Path(workdir)),
        "llm": _llm_identity(llm),
    }
    return _hash_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def _fingerprint_value(value: Any, workdir: Path, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: _fingerprint_value(v, workdir, k) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_fingerprint_value(item, workdir, key) for item in value]
    if isinstance(value, str) and key is not None and _is_file_input_key(key):
        return {
            "value": value,
            "file": _file_fingerprint(workdir, value),
        }
    return value


def _is_file_input_key(key: str) -> bool:
    normalized = key.lower()
    return (
        normalized in FILE_INPUT_KEYS
        or normalized.endswith("_file")
        or normalized.endswith("_path")
    )


def _file_fingerprint(workdir: Path, raw_path: str) -> dict[str, Any]:
    path = (workdir / raw_path).resolve()
    try:
        path.relative_to(workdir.resolve())
    except ValueError:
        return {"status": "outside-workdir"}
    if not path.is_file():
        return {"status": "missing"}
    return {
        "status": "present",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _llm_identity(llm: Any) -> dict[str, Any]:
    return {
        "name": getattr(llm, "name", type(llm).__name__),
        "provider": getattr(llm, "_provider", None),
        "model": getattr(llm, "_default_model", None),
        "api_base": getattr(llm, "_api_base", None),
    }


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
