"""Any calendar with an iCal (.ics) link: Google, Outlook, iCloud, Fastmail and others.

Treat the link as a secret (set BBEYE_ICS_URL); private iCal links grant read access.
"""

from __future__ import annotations

from datetime import date, datetime, time

import httpx
import icalendar
import recurring_ical_events

from bbeye.calendars.base import CalendarEvent, CalendarUnavailable


class IcsCalendar:
    def __init__(self, url: str, client: httpx.Client | None = None) -> None:
        if not url:
            raise CalendarUnavailable("BBEYE_ICS_URL is not set")
        self.url = url.replace("webcal://", "https://", 1)
        self._client = client or httpx.Client(timeout=20, follow_redirects=True)

    def events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        try:
            response = self._client.get(self.url)
            response.raise_for_status()
            calendar = icalendar.Calendar.from_ical(response.content)
        except (httpx.HTTPError, ValueError) as error:
            raise CalendarUnavailable(f"could not read the iCal feed: {type(error).__name__}") from error
        name = str(calendar.get("X-WR-CALNAME", ""))
        found = []
        for component in recurring_ical_events.of(calendar).between(start, end):
            begin = component.get("DTSTART")
            if begin is None:
                continue
            finish = component.get("DTEND")
            begin_value = begin.dt
            end_value = finish.dt if finish is not None else begin_value
            all_day = not isinstance(begin_value, datetime)
            found.append(
                CalendarEvent(
                    id=f"{component.get('UID', '')}",
                    title=str(component.get("SUMMARY", "")),
                    start=_aware(begin_value),
                    end=_aware(end_value),
                    calendar=name,
                    all_day=all_day,
                    notes=str(component.get("DESCRIPTION", "")),
                )
            )
        return found


def _aware(value: date | datetime) -> datetime:
    if not isinstance(value, datetime):
        value = datetime.combine(value, time())
    return value if value.tzinfo else value.astimezone()  # floating times are local
