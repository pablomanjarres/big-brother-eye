"""Pauses Cortex's deep-work sprint when you leave, and resumes it when you are back.

The pause counts from the moment you left, so away time never counts as deep work.
It only ever resumes a sprint that it paused itself and that nobody touched since.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from bbeye.integrations.cortex import NO_CHANGE, SPRINT_KEY, CortexClient, CortexError
from bbeye.reactors.base import Tick
from bbeye.storage import Storage
from bbeye.timeutil import backoff
from bbeye.tracker import ActivityChanged, Event

log = logging.getLogger(__name__)


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


class SprintGuard:
    def __init__(
        self,
        storage: Storage,
        cortex: CortexClient,
        pause_after: timedelta,
        resume: bool = True,
        retry_every: timedelta = timedelta(minutes=1),
        max_retry: timedelta = timedelta(minutes=30),
    ) -> None:
        self.storage, self.cortex = storage, cortex
        self.pause_after, self.resume = pause_after, resume
        self.retry_every, self.max_retry = retry_every, max_retry
        self._failures = 0
        self._left_at: datetime | None = None
        self._next_try: datetime | None = None
        self._returned_at: datetime | None = None  # set while a resume is pending

    def on_events(self, events: list[Event]) -> None:
        for event in events:
            if not isinstance(event, ActivityChanged):
                continue
            if event.current == "away":
                self._left_at, self._next_try = event.at, None
                self._returned_at = None  # never resume while you are away
            elif event.previous == "away":
                self._left_at = None
            if event.current == "working" and self.resume:
                self._returned_at = event.at

    def on_tick(self, tick: Tick) -> None:
        now = tick.now
        if self._next_try and now < self._next_try:
            return
        try:
            if self._left_at and now - self._left_at >= self.pause_after:
                self._pause(self._left_at, now)
                self._left_at = None
            elif self._returned_at:
                self._resume(self._returned_at, now)
                self._returned_at = None
            self._next_try, self._failures = None, 0
        except CortexError as error:
            # Back off: a Cortex that hangs on writes must not collect a new stuck request every minute.
            delay = backoff(self.retry_every.total_seconds(), self._failures, self.max_retry.total_seconds())
            self._failures += 1
            log.info("Sprint update postponed %.0f s: %s", delay.total_seconds(), error)
            self._next_try = now + delay

    def _pause(self, left_at: datetime, now: datetime) -> None:
        def change(sprint: Any) -> Any:
            if not isinstance(sprint, dict) or sprint.get("isPaused") or "endTimeMs" not in sprint:
                return NO_CHANGE  # no sprint, or already paused
            remaining = round((int(sprint["endTimeMs"]) - _ms(left_at)) / 1000)
            if remaining <= 0:
                return NO_CHANGE  # it would have finished before you left; let Cortex complete it
            return {**sprint, "isPaused": True, "pausedTimeLeft": remaining}

        written = self.cortex.update(SPRINT_KEY, change)
        if written is not NO_CHANGE:
            remaining = written["pausedTimeLeft"]
            self.storage.record_sprint_pause(str(written["startedAt"]), left_at, now, remaining)
            log.info("Paused deep-work sprint with %d s left (you left at %s)", remaining, left_at)

    def _resume(self, returned_at: datetime, now: datetime) -> None:
        """Resume as of the moment you came back, so confirming your return costs no sprint time."""
        pause = self.storage.open_sprint_pause()
        if pause is None:
            return
        changed_elsewhere = False

        def change(sprint: Any) -> Any:
            nonlocal changed_elsewhere
            ours = (
                isinstance(sprint, dict)
                and sprint.get("isPaused") is True
                and str(sprint.get("startedAt")) == pause.sprint_started_at
                and sprint.get("pausedTimeLeft") == pause.paused_time_left
            )
            if not ours:  # finished, reset or changed by hand: leave it alone
                changed_elsewhere = True
                return NO_CHANGE
            end_ms = _ms(returned_at) + pause.paused_time_left * 1000
            return {**sprint, "isPaused": False, "endTimeMs": end_ms}

        written = self.cortex.update(SPRINT_KEY, change)
        if written is not NO_CHANGE:
            self.storage.resolve_sprint_pause(pause.sprint_started_at, now, "resumed")
            log.info("Resumed deep-work sprint with %d s left", pause.paused_time_left)
        elif changed_elsewhere:
            self.storage.resolve_sprint_pause(pause.sprint_started_at, now, "changed elsewhere")
