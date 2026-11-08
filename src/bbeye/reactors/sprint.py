"""Pauses Cortex's deep-work sprint when you leave, and resumes it when you are back.

The pause counts from the moment you left, so away time never counts as deep work.
It only ever resumes a sprint that it paused itself and that nobody touched since.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from bbeye.integrations.cortex import SPRINT_KEY, CortexClient, CortexConflict, CortexError
from bbeye.reactors.base import Tick
from bbeye.storage import Storage
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
    ) -> None:
        self.storage, self.cortex = storage, cortex
        self.pause_after, self.resume = pause_after, resume
        self.retry_every = retry_every
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
            self._next_try = None
        except CortexError as error:
            log.info("Sprint update postponed: %s", error)
            self._next_try = now + self.retry_every

    def _update(self, change: Callable[[Any], tuple[dict, Callable[[], None]] | None]) -> None:
        """Read-modify-write with Cortex's revision check; one re-read on a conflict.

        ``change`` returns the new value and a function to run only after Cortex saved it.
        """
        for attempt in range(2):
            current = self.cortex.read(SPRINT_KEY)
            planned = change(current.data)
            if planned is None:
                return
            new, saved = planned
            try:
                self.cortex.write(SPRINT_KEY, new, current.rev)
            except CortexConflict:
                if attempt:
                    raise
                continue
            saved()
            return

    def _pause(self, left_at: datetime, now: datetime) -> None:
        def change(sprint: Any):
            if not isinstance(sprint, dict) or sprint.get("isPaused") or "endTimeMs" not in sprint:
                return None  # no sprint, or already paused
            remaining = round((int(sprint["endTimeMs"]) - _ms(left_at)) / 1000)
            if remaining <= 0:
                return None  # it would have finished before you left; let Cortex complete it

            def saved() -> None:
                self.storage.record_sprint_pause(str(sprint["startedAt"]), left_at, now, remaining)
                log.info("Paused deep-work sprint with %d s left (you left at %s)", remaining, left_at)

            return {**sprint, "isPaused": True, "pausedTimeLeft": remaining}, saved

        self._update(change)

    def _resume(self, returned_at: datetime, now: datetime) -> None:
        """Resume as of the moment you came back, so confirming your return costs no sprint time."""
        pause = self.storage.open_sprint_pause()
        if pause is None:
            return

        def change(sprint: Any):
            ours = (
                isinstance(sprint, dict)
                and sprint.get("isPaused") is True
                and str(sprint.get("startedAt")) == pause.sprint_started_at
                and sprint.get("pausedTimeLeft") == pause.paused_time_left
            )
            if not ours:  # finished, reset or changed by hand: leave it alone
                self.storage.resolve_sprint_pause(pause.sprint_started_at, now, "changed elsewhere")
                return None

            def saved() -> None:
                self.storage.resolve_sprint_pause(pause.sprint_started_at, now, "resumed")
                log.info("Resumed deep-work sprint with %d s left", pause.paused_time_left)

            end_ms = _ms(returned_at) + pause.paused_time_left * 1000
            return {**sprint, "isPaused": False, "endTimeMs": end_ms}, saved

        self._update(change)
