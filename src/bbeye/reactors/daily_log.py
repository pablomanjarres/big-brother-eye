"""Shows automatic work time on the Cortex Daily page (Deep work, Weekly rhythm).

Each work session becomes entries in ``cortex-daily-sessions-<local day>``, the list the Daily
page adds up. While you work they are rewritten every few minutes so the page shows progress.
They only cover time no other entry (a focus sprint you ran) already covers, so nothing is
counted twice. The final version goes through the outbox, so a closed Cortex never loses it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
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


def daily_key(started_at: datetime) -> str:
    """Cortex files a session under the local day it started."""
    return f"cortex-daily-sessions-{started_at.astimezone().date().isoformat()}"


def _span(entry: Any) -> Interval | None:
    try:
        return parse_iso(entry["startedAt"]), parse_iso(entry["completedAt"])
    except (KeyError, TypeError, ValueError):
        return None


def merge_session(existing: Any, session_id: str, start: datetime, end: datetime, label: str) -> Any:
    """The day's list with this session's entries rewritten to fill only uncovered time."""
    entries = existing if isinstance(existing, list) else []
    others = [e for e in entries if not str((e or {}).get("id", "")).startswith(session_id)]
    taken = [span for span in map(_span, others) if span]
    ours = [
        {
            "id": f"{session_id}-{part_start:%H%M%S}",
            "task": label,
            "duration": max(1, round((part_end - part_start).total_seconds() / 60)),
            "startedAt": iso(part_start),
            "completedAt": iso(part_end),
        }
        for part_start, part_end in free_parts(start, end, taken)
    ]
    merged = sorted(others + ours, key=lambda e: str((e or {}).get("startedAt", "")))
    return NO_CHANGE if merged == entries else merged


class DailyLogSync:
    def __init__(
        self,
        storage: Storage,
        cortex: CortexClient,
        label: str = "Desk work",
        every: timedelta = timedelta(minutes=5),
        clock=None,
    ) -> None:
        self.storage, self.cortex, self.label, self.every = storage, cortex, label, every
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
        self._last_live = tick.now
        session_id, started_at = self._open
        until = tick.snapshot.last_working_at or tick.now
        try:
            self._write(session_id, started_at, until)
        except CortexError as error:
            log.debug("Live daily-log update skipped: %s", error)  # the next one or the final covers it

    def _send_final(self, payload: dict[str, Any]) -> str:
        self._write(payload["id"], parse_iso(payload["startedAt"]), parse_iso(payload["endedAt"]))
        return f"logged {payload['id']} on the Daily page"

    def _write(self, session_id: str, start: datetime, end: datetime) -> None:
        self.cortex.update(
            daily_key(start), lambda data: merge_session(data, session_id, start, end, self.label)
        )
