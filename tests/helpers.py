from __future__ import annotations

from datetime import UTC, datetime, timedelta

T0 = datetime(2026, 11, 7, 15, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)
