"""Calendar events from Cortex (which reads the Mac's calendars through EventKit)."""

from __future__ import annotations

from datetime import datetime, timedelta

from bbeye.calendars.base import CalendarEvent, CalendarUnavailable
from bbeye.integrations.cortex import CortexClient, CortexError
from bbeye.timeutil import parse_iso


class CortexCalendar:
    def __init__(self, cortex: CortexClient) -> None:
        self.cortex = cortex

    def events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        try:
            raw = self.cortex.calendar_events(
                start.astimezone().date(), (end.astimezone() + timedelta(days=1)).date()
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
