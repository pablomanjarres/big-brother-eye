"""Builds the calendar source chosen in the config."""

from __future__ import annotations

from bbeye.calendars.base import CalendarSource, CalendarUnavailable
from bbeye.config import Config, ConfigError, time_zone
from bbeye.integrations.cortex import CortexClient


def build_calendar_source(
    config: Config, secrets: dict[str, str], cortex: CortexClient | None = None
) -> CalendarSource | None:
    """None when no calendar is configured. Raises CalendarUnavailable when it is misconfigured."""
    if config.calendar.source == "cortex":
        from bbeye.calendars.cortex import CortexCalendar

        try:
            zone = time_zone(config.cortex.time_zone)
        except ConfigError as error:
            raise CalendarUnavailable(str(error)) from error
        return CortexCalendar(cortex or CortexClient(config.cortex.url), zone=zone)
    if config.calendar.source == "ics":
        from bbeye.calendars.ics import IcsCalendar

        return IcsCalendar(secrets.get("BBEYE_ICS_URL", ""))
    return None
