"""Time helpers. Storage and APIs use UTC ISO-8601 strings; days are local calendar days."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta


def now_utc() -> datetime:
    return datetime.now(UTC)


def backoff(base_s: float, attempts: int, cap_s: float) -> timedelta:
    """Capped exponential back-off: base, 2x, 4x, ... never more than ``cap_s``."""
    return timedelta(seconds=min(cap_s, base_s * 2 ** min(max(attempts, 0), 30)))


def iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("naive datetime")
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def local_day_bounds(day: datetime) -> tuple[datetime, datetime]:
    """UTC start and end of the local calendar day containing ``day`` (DST-safe)."""
    date = day.astimezone().date()
    start = datetime.combine(date, time()).astimezone()
    end = datetime.combine(date + timedelta(days=1), time()).astimezone()
    return start.astimezone(UTC), end.astimezone(UTC)
