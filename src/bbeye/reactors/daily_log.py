"""Shows automatic work time on the Cortex Daily page (Deep work, Weekly rhythm).

Each work session becomes entries in ``cortex-daily-sessions-<local day>``, the list the Daily
page adds up. While you work they are rewritten every few minutes so the page shows progress.
They only cover time no other entry (a focus sprint you ran) already covers, so nothing is
counted twice. The final version goes through the outbox, so a closed Cortex never loses it.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, tzinfo
from typing import Any

from bbeye.integrations.cortex import NO_CHANGE, CortexClient, CortexError
from bbeye.intervals import Interval, free_parts
from bbeye.reactors.base import Tick
from bbeye.reactors.outbox import flush_outbox
from bbeye.storage import Storage
from bbeye.timeutil import iso, now_utc, parse_iso
from bbeye.tracker import Event, WorkEnded, WorkStarted

log = logging.getLogger(__name__)

TARGET = "cortex.daily_log"


def daily_key(started_at: datetime, zone: tzinfo | None = None) -> str:
    """Cortex files a session under the day it started, in the zone of the computer running Cortex.

    ``zone`` is that zone (``cortex.time_zone``); None means this computer's zone.
    """
    return f"cortex-daily-sessions-{started_at.astimezone(zone).date().isoformat()}"


def _span(entry: Any) -> Interval | None:
    if not isinstance(entry, dict):
        return None
    try:
        return parse_iso(entry["startedAt"]), parse_iso(entry["completedAt"])
    except (KeyError, TypeError, ValueError):
        return None


def _id(entry: Any) -> str:
    return str(entry.get("id", "")) if isinstance(entry, dict) else ""


def _minutes(parts: list[Interval]) -> list[int]:
    """Whole minutes per part, rounded cumulatively so they add up to the rounded total."""
    minutes, elapsed, counted = [], 0.0, 0
    for part_start, part_end in parts:
        elapsed += (part_end - part_start).total_seconds() / 60
        total = math.floor(elapsed + 0.5)
        minutes.append(total - counted)
        counted = total
    return minutes


def merge_session(
    existing: Any,
    session_id: str,
    start: datetime,
    end: datetime,
    label: str,
    also_taken: list[Interval] | None = None,
) -> Any:
    """The day's list with this session's entries rewritten to fill only uncovered time.

    ``also_taken`` adds intervals from other days' lists (a session that crosses midnight).
    Entries that are not objects are kept as they are and ignored for overlap.
    """
    entries = existing if isinstance(existing, list) else []
    others = [e for e in entries if not _id(e).startswith(session_id)]
    taken = [span for span in map(_span, others) if span] + list(also_taken or [])
    parts = free_parts(start, end, taken)
    ours = [
        {
            "id": f"{session_id}-{part_start:%H%M%S}",
            "task": label,
            "duration": minutes,
            "startedAt": iso(part_start),
            "completedAt": iso(part_end),
        }
        for (part_start, part_end), minutes in zip(parts, _minutes(parts), strict=True)
    ]
    merged = sorted(others + ours, key=lambda e: str(e.get("startedAt", "")) if isinstance(e, dict) else "")
    return NO_CHANGE if merged == entries else merged


class DailyLogSync:
    def __init__(
        self,
        storage: Storage,
        cortex: CortexClient,
        label: str = "Desk work",
        every: timedelta = timedelta(minutes=5),
        clock=None,
        zone: tzinfo | None = None,
    ) -> None:
        self.storage, self.cortex, self.label, self.every = storage, cortex, label, every
        self.zone = zone
        self._now = clock or now_utc
        self._open: tuple[str, datetime] | None = None
        self._last_live: datetime | None = None

    def on_events(self, events: list[Event]) -> None:
        for event in events:
            if isinstance(event, WorkStarted):
                self._open, self._last_live = (event.session_id, event.at), event.at
            elif isinstance(event, WorkEnded):
                payload = {
                    "id": event.session_id,
                    "startedAt": iso(event.started_at),
                    "endedAt": iso(event.ended_at),
                }
                self.storage.enqueue(TARGET, payload, self._now())
                if self._open and self._open[0] == event.session_id:
                    self._open = None

    def on_tick(self, tick: Tick) -> None:
        flush_outbox(self.storage, TARGET, tick.now, self._send_final)
        if not self._open or not self._last_live or tick.now - self._last_live < self.every:
            return
        session_id, started_at = self._open
        until = tick.snapshot.last_working_at or tick.now
        try:
            self._write(session_id, started_at, until)
        except CortexError as error:
            log.debug("Live daily-log update failed, retrying next tick: %s", error)
            return
        self._last_live = tick.now

    def _send_final(self, payload: dict[str, Any]) -> str:
        self._write(payload["id"], parse_iso(payload["startedAt"]), parse_iso(payload["endedAt"]))
        return f"logged {payload['id']} on the Daily page"

    def _write(self, session_id: str, start: datetime, end: datetime) -> None:
        start_key, end_key = daily_key(start, self.zone), daily_key(end, self.zone)
        also_taken: list[Interval] = []
        if end_key != start_key:  # crossed midnight: sprints after it live in the next list
            later = self.cortex.read(end_key).data
            also_taken = [span for span in map(_span, later if isinstance(later, list) else []) if span]
        self.cortex.update(
            start_key, lambda data: merge_session(data, session_id, start, end, self.label, also_taken)
        )
