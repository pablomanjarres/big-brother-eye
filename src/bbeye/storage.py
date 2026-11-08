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
            ),
        )

    def prune_observations(self, older_than: datetime) -> int:
        cursor = self._db.execute("DELETE FROM observations WHERE at < ?", (iso(older_than),))
        return cursor.rowcount

    def last_observation_at(self) -> datetime | None:
        row = self._db.execute("SELECT at FROM observations ORDER BY at DESC LIMIT 1").fetchone()
        return parse_iso(row["at"]) if row else None

    # ── activity ───────────────────────────────────────────────────────────────

    def record_activity_change(self, at: datetime, previous: str, current: str) -> None:
        self._db.execute(
            "INSERT INTO activity_changes (at, previous, current) VALUES (?, ?, ?)",
            (iso(at), previous, current),
        )

    def activity_changes(self, start: datetime, end: datetime, limit: int = 500) -> list[dict[str, str]]:
        rows = self._db.execute(
            "SELECT at, previous, current FROM activity_changes WHERE at >= ? AND at < ? ORDER BY at LIMIT ?",
            (iso(start), iso(end), limit),
        )
        return [dict(row) for row in rows]

    def last_activity_change(self) -> dict[str, str] | None:
        row = self._db.execute(
            "SELECT at, previous, current FROM activity_changes ORDER BY at DESC, id DESC LIMIT 1"
        )
        found = row.fetchone()
        return dict(found) if found else None

    # ── work sessions ──────────────────────────────────────────────────────────

    def start_work_session(self, session_id: str, started_at: datetime) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO work_sessions (id, started_at, last_working_at) VALUES (?, ?, ?)",
            (session_id, iso(started_at), iso(started_at)),
        )

    def touch_work_session(self, session_id: str, last_working_at: datetime) -> None:
        self._db.execute(
            "UPDATE work_sessions SET last_working_at = ? WHERE id = ? AND ended_at IS NULL",
            (iso(last_working_at), session_id),
        )

    def end_work_session(self, session_id: str, ended_at: datetime) -> None:
        self._db.execute(
            "UPDATE work_sessions SET ended_at = ?, last_working_at = ? WHERE id = ? AND ended_at IS NULL",
            (iso(ended_at), iso(ended_at), session_id),
        )

    def open_work_session(self) -> WorkSession | None:
        row = self._db.execute(
            "SELECT * FROM work_sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return _session(row) if row else None

    def work_sessions(self, start: datetime, end: datetime) -> list[WorkSession]:
        rows = self._db.execute(
            "SELECT * FROM work_sessions WHERE started_at < ? AND COALESCE(ended_at, last_working_at) > ?"
            " ORDER BY started_at",
            (iso(end), iso(start)),
        )
        return [_session(row) for row in rows]

    # ── visits (other people in the room) ──────────────────────────────────────

    def start_visit(self, started_at: datetime, others: int, names: list[str], owner_present: bool) -> int:
        cursor = self._db.execute(
            "INSERT INTO visits (started_at, max_others, names, owner_present_at_start) VALUES (?, ?, ?, ?)",
            (iso(started_at), others, json.dumps(sorted(names)), int(owner_present)),
        )
        return int(cursor.lastrowid or 0)

    def update_visit(self, visit_id: int, others: int, names: list[str]) -> None:
        row = self._db.execute("SELECT max_others, names FROM visits WHERE id = ?", (visit_id,)).fetchone()
        if row is None:
            return
        merged = sorted(set(json.loads(row["names"])) | set(names))
        self._db.execute(
            "UPDATE visits SET max_others = ?, names = ? WHERE id = ?",
            (max(others, row["max_others"]), json.dumps(merged), visit_id),
        )

    def end_visit(self, visit_id: int, ended_at: datetime) -> None:
        self._db.execute(
            "UPDATE visits SET ended_at = ? WHERE id = ? AND ended_at IS NULL", (iso(ended_at), visit_id)
        )

    def end_open_visits(self, ended_at: datetime) -> None:
        self._db.execute("UPDATE visits SET ended_at = ? WHERE ended_at IS NULL", (iso(ended_at),))

    def visits(self, start: datetime, end: datetime, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM visits WHERE started_at >= ? AND started_at < ? ORDER BY started_at LIMIT ?",
            (iso(start), iso(end), limit),
        )
        return [{**dict(row), "names": json.loads(row["names"])} for row in rows]

    def open_visit_count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM visits WHERE ended_at IS NULL").fetchone()[0])

    # ── alerts (also the inbox agents read over MCP) ───────────────────────────

    def add_alert(
        self, created_at: datetime, kind: str, title: str, body: str, dedupe_key: str
    ) -> int | None:
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO alerts (created_at, kind, title, body, dedupe_key) VALUES (?, ?, ?, ?, ?)",
            (iso(created_at), kind, title, body, dedupe_key),
        )
        return int(cursor.lastrowid) if cursor.rowcount else None

    def set_alert_delivery(self, alert_id: int, delivery: dict[str, str]) -> None:
        self._db.execute("UPDATE alerts SET delivery = ? WHERE id = ?", (json.dumps(delivery), alert_id))

    def alerts(self, unacknowledged_only: bool = True, limit: int = 20) -> list[Alert]:
        where = "WHERE acknowledged_at IS NULL" if unacknowledged_only else ""
        rows = self._db.execute(f"SELECT * FROM alerts {where} ORDER BY created_at DESC LIMIT ?", (limit,))
        return [
            Alert(
                id=row["id"],
                created_at=parse_iso(row["created_at"]),
                kind=row["kind"],
                title=row["title"],
                body=row["body"],
                acknowledged=row["acknowledged_at"] is not None,
            )
            for row in rows
        ]

    def unacknowledged_alert_count(self) -> int:
        return int(
            self._db.execute("SELECT COUNT(*) FROM alerts WHERE acknowledged_at IS NULL").fetchone()[0]
        )

    def acknowledge_alert(self, alert_id: int, at: datetime) -> bool:
        cursor = self._db.execute(
            "UPDATE alerts SET acknowledged_at = ? WHERE id = ? AND acknowledged_at IS NULL",
            (iso(at), alert_id),
        )
        return cursor.rowcount == 1

    def last_alert_at(self, kind: str) -> datetime | None:
        row = self._db.execute("SELECT MAX(created_at) AS at FROM alerts WHERE kind = ?", (kind,)).fetchone()
        return parse_iso(row["at"]) if row and row["at"] else None

    # ── outbox (commands for services that may be offline) ─────────────────────

    def enqueue(self, target: str, payload: dict[str, Any], now: datetime) -> None:
        self._db.execute(
            "INSERT INTO outbox (created_at, target, payload, next_attempt_at) VALUES (?, ?, ?, ?)",
            (iso(now), target, json.dumps(payload), iso(now)),
        )

    def next_outbox_item(self, target: str, now: datetime) -> OutboxItem | None:
        """The oldest pending item for a target, only if it is due. Order is preserved per target."""
        row = self._db.execute(
            "SELECT * FROM outbox WHERE target = ? AND status = 'pending' ORDER BY id LIMIT 1", (target,)
        ).fetchone()
        if row is None or parse_iso(row["next_attempt_at"]) > now:
            return None
        return OutboxItem(row["id"], row["target"], json.loads(row["payload"]), row["attempts"])

    def finish_outbox_item(self, item_id: int, status: str, error: str | None = None) -> None:
        self._db.execute(
            "UPDATE outbox SET status = ?, last_error = ? WHERE id = ?", (status, error, item_id)
        )

    def retry_outbox_item(self, item_id: int, error: str, next_attempt_at: datetime) -> None:
        self._db.execute(
            "UPDATE outbox SET attempts = attempts + 1, last_error = ?, next_attempt_at = ? WHERE id = ?",
            (error, iso(next_attempt_at), item_id),
        )

    def outbox_summary(self) -> dict[str, int]:
        rows = self._db.execute("SELECT status, COUNT(*) AS n FROM outbox GROUP BY status")
        return {row["status"]: row["n"] for row in rows}

    # ── enrolled faces ─────────────────────────────────────────────────────────

    def replace_faces(self, name: str, embeddings: list[np.ndarray], now: datetime) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM faces WHERE name = ?", (name,))
            db.executemany(
                "INSERT INTO faces (name, embedding, created_at) VALUES (?, ?, ?)",
                [(name, np.asarray(e, dtype=np.float32).tobytes(), iso(now)) for e in embeddings],
            )

    def faces(self) -> dict[str, list[np.ndarray]]:
        found: dict[str, list[np.ndarray]] = {}
        for row in self._db.execute("SELECT name, embedding FROM faces ORDER BY id"):
            found.setdefault(row["name"], []).append(
                np.frombuffer(row["embedding"], dtype=np.float32).reshape(1, -1)
            )
        return found

    def forget_faces(self, name: str) -> int:
        return self._db.execute("DELETE FROM faces WHERE name = ?", (name,)).rowcount

    # ── sprint pauses made by the Eye (so it only resumes what it paused) ──────

    def record_sprint_pause(
        self, sprint_started_at: str, left_at: datetime, paused_at: datetime, left: int
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO sprint_pauses (sprint_started_at, left_at, paused_at, paused_time_left)"
            " VALUES (?, ?, ?, ?)",
            (sprint_started_at, iso(left_at), iso(paused_at), left),
        )

    def open_sprint_pause(self) -> SprintPause | None:
        row = self._db.execute(
            "SELECT * FROM sprint_pauses WHERE resolved_at IS NULL ORDER BY paused_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return SprintPause(row["sprint_started_at"], parse_iso(row["left_at"]), row["paused_time_left"])

    def resolve_sprint_pause(self, sprint_started_at: str, at: datetime, resolution: str) -> None:
        self._db.execute(
            "UPDATE sprint_pauses SET resolved_at = ?, resolution = ? WHERE sprint_started_at = ?",
            (iso(at), resolution, sprint_started_at),
        )


def _session(row: sqlite3.Row) -> WorkSession:
    return WorkSession(
        id=row["id"],
        started_at=parse_iso(row["started_at"]),
        ended_at=parse_iso(row["ended_at"]) if row["ended_at"] else None,
        last_working_at=parse_iso(row["last_working_at"]),
    )
