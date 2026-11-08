"""Turns noisy per-frame verdicts into stable activity changes and work sessions.

Every event carries the time the change *really* happened, not the time it was
confirmed: a new activity is back-dated to its first frame, and a work session
ends at the last moment you were working.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime

from bbeye.config import TrackerConfig
from bbeye.observation import Activity


@dataclass(frozen=True)
class ActivityChanged:
    at: datetime
    previous: Activity
    current: Activity


@dataclass(frozen=True)
class WorkStarted:
    session_id: str
    at: datetime


@dataclass(frozen=True)
class WorkEnded:
    session_id: str
    started_at: datetime
    ended_at: datetime


Event = ActivityChanged | WorkStarted | WorkEnded


@dataclass(frozen=True)
class TrackerSnapshot:
    activity: Activity
    since: datetime
    session_id: str | None
    session_started_at: datetime | None
    last_working_at: datetime | None


def session_id_for(started_at: datetime) -> str:
    """Stable ID derived from the start time, so retries to other systems stay idempotent."""
    return "bbeye-" + started_at.strftime("%Y%m%dT%H%M%S")


# A change needs this share of recent verdicts to disagree with the current activity.
SWITCH_SHARE = 0.7
# This much uninterrupted agreement with the current activity cancels a pending change.
CANCEL_S = 10.0


class ActivityTracker:
    """Hysteresis over a short run of verdicts.

    When verdicts start disagreeing with the current activity, they are collected in a
    run. The run becomes a change once the most common new activity has lasted long
    enough (``confirm_s``, or ``away_after_s`` for away) and most of the run disagrees.
    A single odd frame neither confirms nor cancels anything. A gap in observations
    (sleep, camera lost) ends the picture at the last frame seen: unseen time is never work.
    """

    def __init__(self, config: TrackerConfig, now: datetime) -> None:
        self.config = config
        self.activity: Activity = "away"
        self.since = now
        self._run: deque[tuple[datetime, Activity]] = deque()
        self._agree_since: datetime | None = None
        self._last_seen_at: datetime | None = None
        self.session_id: str | None = None
        self.session_started_at: datetime | None = None
        self.last_working_at: datetime | None = None
        self._working_ended_at: datetime | None = None

    def snapshot(self) -> TrackerSnapshot:
        return TrackerSnapshot(
            self.activity, self.since, self.session_id, self.session_started_at, self.last_working_at
        )

    def update(self, at: datetime, activity: Activity, seen_leaving: bool = False) -> list[Event]:
        """``seen_leaving``: another camera saw the owner walk away, so absence needs less proof."""
        events: list[Event] = []
        last = self._last_seen_at
        if last is not None and (at - last).total_seconds() > self.config.max_gap_s:
            events.extend(self._gap(last))
        self._last_seen_at = at

        if activity == self.activity:
            self._agree_since = self._agree_since or at
            if self._run and (at - self._agree_since).total_seconds() >= CANCEL_S:
                self._run.clear()  # it really was a blip
            elif self._run:
                self._run.append((at, activity))
        else:
            self._agree_since = None
            self._run.append((at, activity))
        if self._run:
            horizon = 2 * max(self.config.confirm_s, self.config.away_after_s)
            while self._run and (at - self._run[0][0]).total_seconds() > horizon:
                self._run.popleft()
            events.extend(self._maybe_switch(at, seen_leaving))

        if self.activity == "working":
            pending = next((t for t, a in self._run if a != "working"), None)
            self.last_working_at = pending or at

        ended_at = self._working_ended_at
        gap_over = ended_at is not None and (at - ended_at).total_seconds() >= self.config.work_gap_s
        if self.session_id and self.activity != "working" and gap_over:
            assert ended_at is not None
            events.append(self._end_session(ended_at))
        return events

    def _maybe_switch(self, at: datetime, seen_leaving: bool = False) -> list[Event]:
        others = [(t, a) for t, a in self._run if a != self.activity]
        if not others or len(others) / len(self._run) < SWITCH_SHARE:
            return []
        counts = Counter(a for _, a in others)
        target = max(counts, key=lambda a: (counts[a], max(t for t, b in others if b == a)))
        first = next(t for t, a in others if a == target)
        if target == "away":
            needed = self.config.exit_confirm_s if seen_leaving else self.config.away_after_s
        else:
            needed = self.config.confirm_s
        if (at - first).total_seconds() < needed:
            return []
        return self._switch(target, first)

    def _gap(self, last_seen: datetime) -> list[Event]:
        """Nothing was observed after ``last_seen``: close the picture there."""
        end_of_work = self.last_working_at if self.activity == "working" else self._working_ended_at
        self._run.clear()
        self._agree_since = None
        events: list[Event] = []
        if self.activity != "away":
            events.extend(self._switch("away", last_seen))
        if self.session_id:
            events.append(self._end_session(min(end_of_work or last_seen, last_seen)))
        return events

    def close(self) -> list[Event]:
        """End an open work session at the last moment of work (used on shutdown)."""
        if not self.session_id:
            return []
        end = self._working_ended_at or self.last_working_at or self.session_started_at
        assert end is not None
        return [self._end_session(end)]

    def _switch(self, activity: Activity, at: datetime) -> list[Event]:
        previous = self.activity
        self.activity, self.since = activity, at
        self._run.clear()
        self._agree_since = None
        events: list[Event] = [ActivityChanged(at, previous, activity)]
        if activity == "working":
            self._working_ended_at = None
            self.last_working_at = at
            if self.session_id is None:
                self.session_id = session_id_for(at)
                self.session_started_at = at
                events.append(WorkStarted(self.session_id, at))
        elif previous == "working":
            self._working_ended_at = at
        return events

    def _end_session(self, ended_at: datetime) -> WorkEnded:
        assert self.session_id and self.session_started_at
        event = WorkEnded(self.session_id, self.session_started_at, max(ended_at, self.session_started_at))
        self.session_id = self.session_started_at = self._working_ended_at = None
        return event
