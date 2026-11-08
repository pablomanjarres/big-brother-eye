"""Calendar events from Cortex (which reads the Mac's calendars through EventKit)."""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo

from bbeye.calendars.base import CalendarEvent, CalendarUnavailable
from bbeye.integrations.cortex import CortexClient, CortexError, cortex_day
from bbeye.timeutil import parse_iso


class CortexCalendar:
    def __init__(self, cortex: CortexClient, zone: tzinfo | None = None) -> None:
        self.cortex = cortex
        self.zone = zone  # the zone of the computer running Cortex; None = this computer's

    def events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        try:
            raw = self.cortex.calendar_events(
                cortex_day(start, self.zone), cortex_day(end, self.zone) + timedelta(days=1)
            )
        except CortexError as error:
            raise CalendarUnavailable(str(error)) from error
        events = []
        for item in raw:
            try:
                event = CalendarEvent(
                    id=str(item.get("id") or ""),
                    title=str(item.get("title") or ""),
                    start=_moment(item["startDate"]),
                    end=_moment(item["endDate"]),
                    calendar=str(item.get("calendar") or ""),
                    all_day=bool(item.get("isAllDay")),
                    notes=str(item.get("notes") or ""),
                )
            except (KeyError, TypeError, ValueError):
                continue  # skip one malformed event rather than losing the whole calendar
            if event.start < end and event.end > start:
                events.append(event)
        return events


def _moment(value: str) -> datetime:
    moment = parse_iso(value)
    return moment if moment.tzinfo else moment.astimezone()
