from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    calendar: str
    all_day: bool
    notes: str = ""


class CalendarSource(Protocol):
    def events(self, start: datetime, end: datetime) -> list[CalendarEvent]: ...


class CalendarUnavailable(RuntimeError):
    pass
