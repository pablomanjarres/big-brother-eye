"""Local SQLite storage. The single owner of every table and query.

The capture loop writes; the MCP server and CLI read. WAL mode lets them run at
the same time. Times are stored as UTC ISO-8601 strings.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from bbeye.observation import Observation, Verdict
from bbeye.timeutil import iso, parse_iso

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  owner_present INTEGER NOT NULL,
  people INTEGER NOT NULL,
  others INTEGER NOT NULL,
  activity TEXT NOT NULL,
  confidence REAL NOT NULL,
  judge TEXT NOT NULL,
  input_idle_s REAL,
  objects TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS observations_at ON observations (at);
CREATE TABLE IF NOT EXISTS activity_changes (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  previous TEXT NOT NULL,
  current TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS activity_changes_at ON activity_changes (at);
CREATE TABLE IF NOT EXISTS work_sessions (
  id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  last_working_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS work_sessions_started ON work_sessions (started_at);
CREATE TABLE IF NOT EXISTS visits (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  max_others INTEGER NOT NULL,
  names TEXT NOT NULL,
  owner_present_at_start INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  dedupe_key TEXT NOT NULL UNIQUE,
  delivery TEXT NOT NULL DEFAULT '{}',
  acknowledged_at TEXT
);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  target TEXT NOT NULL,
  payload TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL,
  last_error TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS faces (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  embedding BLOB NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sprint_pauses (
  sprint_started_at TEXT PRIMARY KEY,
  left_at TEXT NOT NULL,
  paused_at TEXT NOT NULL,
  paused_time_left INTEGER NOT NULL,
  resolved_at TEXT,
  resolution TEXT
);
"""


@dataclass(frozen=True)
class WorkSession:
    id: str
    started_at: datetime
    ended_at: datetime | None
    last_working_at: datetime

    def duration_s(self, now: datetime) -> float:
        end = self.ended_at or self.last_working_at
        return max(0.0, (min(end, now) - self.started_at).total_seconds())


@dataclass(frozen=True)
class OutboxItem:
    id: int
    target: str
    payload: dict[str, Any]
    attempts: int


@dataclass(frozen=True)
class Alert:
    id: int
    created_at: datetime
    kind: str
    title: str
    body: str
    acknowledged: bool


@dataclass(frozen=True)
class SprintPause:
    sprint_started_at: str
    left_at: datetime
    paused_time_left: int


class Storage:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), timeout=10, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self._db
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema {version} is newer than this app ({SCHEMA_VERSION}). Update bbeye."
            )
        if version < 1:
            # Every statement is idempotent, so a crash halfway is safe to re-run.
            self._db.executescript(_SCHEMA)
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # ── meta (live status shared with the MCP server) ──────────────────────────

    def set_meta(self, values: dict[str, str]) -> None:
        with self._tx() as db:
            db.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                list(values.items()),
            )

    def meta(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self._db.execute("SELECT key, value FROM meta")}

    # ── observations ───────────────────────────────────────────────────────────

    def record_observation(self, observation: Observation, verdict: Verdict) -> None:
        self._db.execute(
            "INSERT INTO observations (at, owner_present, people, others, activity, confidence, judge,"
            " input_idle_s,"
            " objects) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                iso(observation.at),
                int(observation.owner_present),
                len(observation.people),
                len(observation.others),
                verdict.activity,
                round(verdict.confidence, 3),
                verdict.source,
                observation.input_idle_s,
                json.dumps(observation.object_labels),
