"""Automatic work-hours sessions in Cortex.

Cortex runs one timer at a time, and other tools (or you) may own it. So the Eye
never holds it. When a work session ends, the Eye reads Cortex's saved sessions,
works out which parts of its session nothing else tracked, and records each free
part as a finished interval (start-owned-at, then stop-owned-at, both with past
times). While another timer is running, the recording waits and retries.

Recordings go through the outbox, so a sleeping laptop or a closed Cortex never
loses time. A retry recomputes the free parts, and parts already recorded count as
taken, so replaying is safe. Automatic sessions arrive unbilled and marked for review.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from bbeye.integrations.cortex import CortexClient, CortexError
from bbeye.reactors.base import Tick
from bbeye.storage import Storage
from bbeye.timeutil import backoff, iso, now_utc, parse_iso
from bbeye.tracker import Event, WorkEnded

log = logging.getLogger(__name__)

TARGET = "cortex.work_hours"
MAX_ATTEMPTS = 200  # with the 10-minute cap this is more than a day of retrying
MIN_PART = timedelta(minutes=1)  # free slivers shorter than this are not worth a session


def free_parts(
    start: datetime, end: datetime, taken: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """The parts of [start, end) that no taken interval covers, in time order."""
    parts, cursor = [], start
    for busy_start, busy_end in sorted(taken):
        if busy_end <= cursor or busy_start >= end:
            continue
        if busy_start > cursor:
            parts.append((cursor, min(busy_start, end)))
        cursor = max(cursor, busy_end)
        if cursor >= end:
            break
    if cursor < end:
        parts.append((cursor, end))
    return [(a, b) for a, b in parts if b - a >= MIN_PART]


def retry_delay(attempts: int) -> timedelta:
    return backoff(5.0, attempts, cap_s=600.0)


class WorkHoursSync:
    def __init__(self, storage: Storage, cortex: CortexClient, project_id: str, clock=None) -> None:
        if not project_id:
            raise ValueError("cortex.project_id is required to track work hours")
        self.storage, self.cortex, self.project_id = storage, cortex, project_id
        self._now = clock or now_utc

    def on_events(self, events: list[Event]) -> None:
        now = self._now()
        for event in events:
            if isinstance(event, WorkEnded) and event.ended_at - event.started_at >= MIN_PART:
                payload = {
                    "id": event.session_id,
                    "startedAt": iso(event.started_at),
                    "endedAt": iso(event.ended_at),
                }
                self.storage.enqueue(TARGET, payload, now)

    def on_tick(self, tick: Tick) -> None:
        self.flush(tick.now)

    def flush(self, now: datetime, limit: int = 10) -> None:
        for _ in range(limit):
            item = self.storage.next_outbox_item(TARGET, now)
            if item is None:
                return
            try:
                recorded = self._record(item.payload)
            except CortexError as error:
                if error.retryable and item.attempts + 1 < MAX_ATTEMPTS:
                    self.storage.retry_outbox_item(item.id, str(error), now + retry_delay(item.attempts))
                    return  # keep order: nothing after this item may overtake it
                log.warning("Could not record %s in Cortex: %s", item.payload["id"], error)
                self.storage.finish_outbox_item(item.id, "rejected", str(error))
                continue
            log.info("Recorded %s in Cortex as %d interval(s)", item.payload["id"], recorded)
            self.storage.finish_outbox_item(item.id, "done")

    def _record(self, payload: dict[str, Any]) -> int:
        session_id = payload["id"]
        state = self.cortex.work_hours_state()
        active = state.get("active") or None
        if active:
            if not str(active.get("id", "")).startswith(session_id):
                raise CortexError("another timer is running", retryable=True)
            # Our own interval, left open by an interrupted recording: close it first.
            self.cortex.work_hours(
                {"type": "stop-owned-at", "id": active["id"], "endedAt": payload["endedAt"]}
            )
            state = self.cortex.work_hours_state()
        taken = [
            (parse_iso(row["startedAt"]), parse_iso(row["endedAt"])) for row in state.get("sessions", [])
        ]
        recorded = 0
        for part_start, part_end in free_parts(
            parse_iso(payload["startedAt"]), parse_iso(payload["endedAt"]), taken
        ):
            part_id = f"{session_id}-{part_start:%H%M%S}"
            try:
                self.cortex.work_hours(
                    {
                        "type": "start-owned-at",
                        "id": part_id,
                        "projectId": self.project_id,
                        "startedAt": iso(part_start),
                    }
                )
            except CortexError as error:
                if error.retryable:
                    raise
                log.info("Skipped part %s: %s", part_id, error)  # someone filled it in the meantime
                continue
            self._stop(part_id, part_end)
            recorded += 1
        return recorded

    def _stop(self, part_id: str, part_end: datetime) -> None:
        try:
            self.cortex.work_hours({"type": "stop-owned-at", "id": part_id, "endedAt": iso(part_end)})
        except CortexError as error:
            if error.retryable:
                raise  # the retry finds our open interval and closes it
            # Never leave the Eye holding Cortex's only timer: stop it now and let review fix the end.
            log.warning("Stopping %s at the current time after: %s", part_id, error)
            self.cortex.work_hours({"type": "stop-owned", "id": part_id})
