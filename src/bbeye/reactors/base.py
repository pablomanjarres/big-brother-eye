"""Reactors turn tracker events and the passage of time into side effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from bbeye.observation import Observation
from bbeye.tracker import Event, TrackerSnapshot


@dataclass(frozen=True)
class Tick:
    now: datetime
    snapshot: TrackerSnapshot
    observation: Observation | None  # None when the camera gave no frame this round


class Reactor(Protocol):
    def on_events(self, events: list[Event]) -> None: ...

    def on_tick(self, tick: Tick) -> None: ...
