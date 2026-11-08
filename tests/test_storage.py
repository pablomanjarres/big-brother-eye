import sqlite3

import numpy as np
import pytest
from helpers import at

from bbeye.observation import Box, Detection, Observation, Person, Verdict
from bbeye.storage import SCHEMA_VERSION, Storage
from bbeye.timeutil import local_day_bounds


def test_schema_version_and_reopen(tmp_path):
    path = tmp_path / "db.sqlite3"
    Storage(path).close()
    db = Storage(path)  # reopening an existing database is a no-op migration
    assert db._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    db.close()


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "db.sqlite3"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 99")
    raw.close()
    with pytest.raises(RuntimeError, match="newer"):
        Storage(path)


def test_observation_round_trip_and_prune(storage):
    person = Person(Box(0, 0, 10, 10), "me", True, False)
    obs = Observation(at(0), "me", (person,), (Detection("laptop", 0.9, Box(1, 1, 2, 2)),), 3.0)
    storage.record_observation(obs, Verdict("working", 0.9, "rules"))
    assert storage.last_observation_at() == at(0)
    assert storage.prune_observations(at(1)) == 1
    assert storage.last_observation_at() is None


def test_work_sessions(storage):
    storage.start_work_session("s1", at(0))
    storage.touch_work_session("s1", at(60))
    assert storage.open_work_session().last_working_at == at(60)
    storage.end_work_session("s1", at(90))
    assert storage.open_work_session() is None
    [session] = storage.work_sessions(at(-10), at(100))
    assert session.duration_s(at(1000)) == 90


def test_alerts_dedupe_and_acknowledge(storage):
    first = storage.add_alert(at(0), "calendar", "Late", "body", "calendar:1")
    assert first is not None
    assert storage.add_alert(at(5), "calendar", "Late", "body", "calendar:1") is None
    assert [a.id for a in storage.alerts()] == [first]
    assert storage.acknowledge_alert(first, at(10))
    assert not storage.acknowledge_alert(first, at(11))
    assert storage.alerts() == []
    assert len(storage.alerts(unacknowledged_only=False)) == 1


def test_outbox_keeps_order_and_waits_for_retry_time(storage):
    storage.enqueue("cortex", {"n": 1}, at(0))
    storage.enqueue("cortex", {"n": 2}, at(0))
    item = storage.next_outbox_item("cortex", at(0))
    assert item.payload == {"n": 1}
    storage.retry_outbox_item(item.id, "offline", at(60))
    assert storage.next_outbox_item("cortex", at(30)) is None  # first item blocks the second
    item = storage.next_outbox_item("cortex", at(60))
    assert item.payload == {"n": 1} and item.attempts == 1
    storage.finish_outbox_item(item.id, "done")
    assert storage.next_outbox_item("cortex", at(60)).payload == {"n": 2}


def test_faces_replace(storage):
    storage.replace_faces("me", [np.ones(128), np.zeros(128)], at(0))
    storage.replace_faces("me", [np.ones(128)], at(1))
    faces = storage.faces()
    assert list(faces) == ["me"] and len(faces["me"]) == 1 and faces["me"][0].shape == (1, 128)


def test_sprint_pause_records(storage):
    storage.record_sprint_pause("2026-11-07T15:00:00.000Z", at(0), at(300), 900)
    pause = storage.open_sprint_pause()
    assert pause.paused_time_left == 900
    storage.resolve_sprint_pause(pause.sprint_started_at, at(400), "resumed")
    assert storage.open_sprint_pause() is None


def test_visits(storage):
    visit = storage.start_visit(at(0), 1, [], owner_present=False)
    storage.update_visit(visit, 2, ["guest"])
    storage.end_visit(visit, at(60))
    [row] = storage.visits(at(-1), at(100))
    assert row["max_others"] == 2 and row["names"] == ["guest"] and row["ended_at"]


def test_local_day_bounds_cover_24ish_hours():
    start, end = local_day_bounds(at(0))
    assert start <= at(0) < end
    assert 23 * 3600 <= (end - start).total_seconds() <= 25 * 3600


def test_backoff_is_capped_even_after_many_failures():
    from datetime import timedelta

    from bbeye.timeutil import backoff

    assert [backoff(5, n, 600).total_seconds() for n in range(4)] == [5, 10, 20, 40]
    assert backoff(60, 10_000, 600) == timedelta(seconds=600)
