"""Read models shared by the CLI and the MCP server. Everything here only reads storage."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from bbeye.storage import Storage
from bbeye.timeutil import iso, local_day_bounds, parse_iso

STALE_AFTER = timedelta(minutes=2)


def eye_status(storage: Storage, now: datetime) -> dict[str, Any]:
    meta = storage.meta()
    heartbeat = parse_iso(meta["heartbeat_at"]) if "heartbeat_at" in meta else None
    online = meta.get("running") == "1" and heartbeat is not None and now - heartbeat < STALE_AFTER
    session = storage.open_work_session()
    return {
        "eye_online": online,
        "last_heartbeat": meta.get("heartbeat_at"),
        "camera": meta.get("camera", "unknown"),
        "judge": meta.get("judge", "unknown"),
        "owner": meta.get("owner"),
        "activity": meta.get("activity", "unknown") if online else "unknown",
        "activity_since": meta.get("activity_since") if online else None,
        "owner_present": meta.get("owner_present") == "1" if online else None,
        "others_in_room": int(meta.get("others", "0")) if online else None,
        "work_session_started_at": iso(session.started_at) if session else None,
        "worked_today_minutes": work_summary(storage, now, now)["total_minutes"],
        "room_last_motion": meta.get("room_last_motion"),
        "unacknowledged_alerts": storage.unacknowledged_alert_count(),
    }


def work_summary(storage: Storage, day: datetime, now: datetime) -> dict[str, Any]:
    start, end = local_day_bounds(day)
    sessions = []
    total = 0.0
    for session in storage.work_sessions(start, end):
        session_end = session.ended_at or session.last_working_at
        clipped = max(0.0, (min(session_end, end, now) - max(session.started_at, start)).total_seconds())
        total += clipped
        sessions.append(
            {
                "id": session.id,
                "started_at": iso(session.started_at),
                "ended_at": iso(session.ended_at) if session.ended_at else None,
                "minutes": round(clipped / 60, 1),
            }
        )
    return {
        "day": start.astimezone().date().isoformat(),
        "total_minutes": round(total / 60, 1),
        "sessions": sessions,
    }


def parse_moment(value: str, default: datetime) -> datetime:
    """Accept an ISO date (local midnight) or datetime; empty means ``default``."""
    if not value:
        return default
    if len(value) == 10:
        return local_day_bounds(datetime.fromisoformat(value + "T12:00:00").astimezone())[0]
    moment = parse_iso(value)
    return moment if moment.tzinfo else moment.astimezone()


def timeline(storage: Storage, start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "start": iso(start),
        "end": iso(end),
        "activity_changes": storage.activity_changes(start, end),
        "visits": storage.visits(start, end),
    }


def alert_list(storage: Storage, include_acknowledged: bool, limit: int) -> list[dict[str, Any]]:
    return [
        {
            "id": a.id,
            "created_at": iso(a.created_at),
            "kind": a.kind,
            "title": a.title,
            "body": a.body,
            "acknowledged": a.acknowledged,
        }
        for a in storage.alerts(unacknowledged_only=not include_acknowledged, limit=limit)
    ]
