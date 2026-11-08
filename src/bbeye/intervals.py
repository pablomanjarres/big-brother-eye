"""Interval arithmetic shared by everything that fills only uncovered time."""

from __future__ import annotations

from datetime import datetime, timedelta

Interval = tuple[datetime, datetime]


def free_parts(
    start: datetime, end: datetime, taken: list[Interval], min_part: timedelta = timedelta(minutes=1)
) -> list[Interval]:
    """The parts of [start, end) that no taken interval covers, in time order, at least ``min_part`` long."""
    parts, cursor = [], start
    for busy_start, busy_end in sorted(taken):
        if busy_end <= cursor or busy_start >= end:
            continue
        if busy_start > cursor:
            parts.append((cursor, min(busy_start, end)))
        cursor = max(cursor, busy_end)
        if cursor >= end:
            break
    if cursor < end:
        parts.append((cursor, end))
    return [(a, b) for a, b in parts if b - a >= min_part]
