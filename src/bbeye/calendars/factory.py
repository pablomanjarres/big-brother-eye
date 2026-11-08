"""Builds the calendar source chosen in the config."""

from __future__ import annotations

from bbeye.calendars.base import CalendarSource
from bbeye.config import Config
from bbeye.integrations.cortex import CortexClient


def build_calendar_source(
    config: Config, secrets: dict[str, str], cortex: CortexClient | None = None
) -> CalendarSource | None:
    """None when no calendar is configured. Raises CalendarUnavailable when it is misconfigured."""
    if config.calendar.source == "cortex":
        from bbeye.calendars.cortex import CortexCalendar

        return CortexCalendar(cortex or CortexClient(config.cortex.url))
    if config.calendar.source == "ics":
        from bbeye.calendars.ics import IcsCalendar

        return IcsCalendar(secrets.get("BBEYE_ICS_URL", ""))
    return None
