"""Alerts you when a calendar event says you should be at the desk and you are not."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from bbeye.alerts import AlertService
from bbeye.calendars.base import CalendarEvent, CalendarSource, CalendarUnavailable
from bbeye.config import CalendarConfig
from bbeye.reactors.base import Tick
from bbeye.timeutil import iso
from bbeye.tracker import Event

log = logging.getLogger(__name__)


def is_desk_event(event: CalendarEvent, config: CalendarConfig) -> bool:
    if event.all_day:
        return False
    if event.calendar.casefold() in {name.casefold() for name in config.desk_calendars}:
        return True
    text = f"{event.title}\n{event.notes}".casefold()
    return any(keyword.casefold() in text for keyword in config.desk_keywords)


class CalendarWatch:
    def __init__(self, source: CalendarSource, alerts: AlertService, config: CalendarConfig) -> None:
        self.source, self.alerts, self.config = source, alerts, config
        self._events: list[CalendarEvent] = []
        self._refreshed_at: datetime | None = None

    def on_events(self, events: list[Event]) -> None:
        pass

    def _refresh(self, now: datetime) -> None:
        if self._refreshed_at and now - self._refreshed_at < timedelta(minutes=self.config.refresh_min):
            return
        self._refreshed_at = now  # also after a failure, so an outage is retried at the refresh pace
        try:
            self._events = [
                e
                for e in self.source.events(now - timedelta(hours=12), now + timedelta(hours=12))
                if is_desk_event(e, self.config)
            ]
        except CalendarUnavailable as error:
            log.warning("Calendar unavailable: %s (keeping %d known events)", error, len(self._events))

    def on_tick(self, tick: Tick) -> None:
        now = tick.now
        self._refresh(now)
        if tick.snapshot.activity != "away":
            return
        late_after = timedelta(minutes=self.config.late_after_min)
        for event in self._events:
            if not event.start <= now < event.end:
                continue
            late_since = max(event.start, tick.snapshot.since)
            if now - late_since < late_after:
                continue
            minutes = int((now - event.start).total_seconds() // 60)
            self.alerts.raise_alert(
                now,
                kind="calendar",
                title=f"You should be at your desk: {event.title}",
                body=f"'{event.title}' started {minutes} min ago and nobody is at the desk.",
                dedupe_key=f"calendar:{event.id or event.title}:{iso(event.start)}",
            )
