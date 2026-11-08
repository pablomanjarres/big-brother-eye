"""Notices other people in the room, records each visit, and optionally alerts you."""

from __future__ import annotations

from datetime import datetime, timedelta

from bbeye.alerts import AlertService
from bbeye.config import VisitorsConfig
from bbeye.reactors.base import Tick
from bbeye.storage import Storage
from bbeye.tracker import Event


class VisitorWatch:
    def __init__(self, storage: Storage, alerts: AlertService, config: VisitorsConfig) -> None:
        self.storage, self.alerts, self.config = storage, alerts, config
        self._visit_id: int | None = None
        self._first_seen: datetime | None = None
        self._last_seen: datetime | None = None

    def on_events(self, events: list[Event]) -> None:
        pass

    def on_tick(self, tick: Tick) -> None:
        observation, now = tick.observation, tick.now
        if observation is None:
            return
        others = observation.others
        if not others:
            if self._last_seen and (now - self._last_seen).total_seconds() > self.config.min_seen_s:
                self._first_seen = None
            if (
                self._visit_id
                and self._last_seen
                and (now - self._last_seen).total_seconds() >= self.config.gone_after_s
            ):
                self.storage.end_visit(self._visit_id, self._last_seen)
                self._visit_id = None
            return

        names = sorted({person.name for person in others if person.name})
        self._last_seen = now
        if self._visit_id is not None:
            self.storage.update_visit(self._visit_id, len(others), names)
            return
        self._first_seen = self._first_seen or now
        if (now - self._first_seen).total_seconds() < self.config.min_seen_s:
            return
        owner_present = observation.owner_present
        self._visit_id = self.storage.start_visit(self._first_seen, len(others), names, owner_present)
        self._maybe_alert(now, len(others), names, owner_present)

    def _maybe_alert(self, now: datetime, count: int, names: list[str], owner_present: bool) -> None:
        policy = self.config.alert
        if policy == "never" or (policy == "when_away" and owner_present):
            return
        last = self.storage.last_alert_at("visitor")
        if last and now - last < timedelta(minutes=self.config.cooldown_min):
            return
        who = (
            ", ".join(names)
            if names
            else ("someone you have not enrolled" if count == 1 else f"{count} people")
        )
        where = "while you are away" if not owner_present else "with you"
        self.alerts.raise_alert(
            now,
            "visitor",
            "Someone is at your desk",
            f"Camera sees {who} {where}.",
            f"visitor:{self._visit_id}",
        )
