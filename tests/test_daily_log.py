from datetime import timedelta

from helpers import T0, at
from test_cortex import FakeCortex

from bbeye.integrations.cortex import NO_CHANGE
from bbeye.reactors.base import Tick
from bbeye.reactors.daily_log import DailyLogSync, daily_key, merge_session
from bbeye.timeutil import iso
from bbeye.tracker import TrackerSnapshot, WorkEnded, WorkStarted

KEY = daily_key(T0)


def sprint(start, end, id="manual"):
    return {
        "id": id,
        "task": "Deep work",
        "duration": int((end - start) / 60),
        "startedAt": iso(at(start)),
        "completedAt": iso(at(end)),
    }


def working(last_working):
    return TrackerSnapshot("working", at(0), "bbeye-1", at(0), at(last_working))


def test_merge_fills_only_uncovered_time_and_is_idempotent():
    existing = [sprint(600, 1200)]
    merged = merge_session(existing, "bbeye-1", at(0), at(1800), "Desk work")
    ours = [e for e in merged if e["id"].startswith("bbeye-1")]
    assert [(e["startedAt"], e["completedAt"], e["duration"]) for e in ours] == [
        (iso(at(0)), iso(at(600)), 10),
        (iso(at(1200)), iso(at(1800)), 10),
    ]
    assert merge_session(merged, "bbeye-1", at(0), at(1800), "Desk work") is NO_CHANGE


def test_merge_keeps_other_entries_even_if_malformed():
    existing = [{"id": "odd"}, sprint(0, 60)]
    merged = merge_session(existing, "bbeye-1", at(0), at(600), "Desk work")
    assert {"id": "odd"} in merged and sprint(0, 60) in merged


def test_live_updates_then_final_entry(storage):
    fake = FakeCortex()
    sync = DailyLogSync(storage, fake.client(), every=timedelta(minutes=5), clock=lambda: at(0))
    sync.on_events([WorkStarted("bbeye-1", at(0))])
    sync.on_tick(Tick(at(200), working(200), None))
    assert KEY not in fake.data  # not yet five minutes
    sync.on_tick(Tick(at(300), working(300), None))
    assert [e["duration"] for e in fake.data[KEY]] == [5]
    sync.on_tick(Tick(at(600), working(600), None))
    assert [e["duration"] for e in fake.data[KEY]] == [10]  # same entry grew, no duplicate
    sync.on_events([WorkEnded("bbeye-1", at(0), at(900))])
    sync.on_tick(Tick(at(901), working(900), None))
    assert [(e["duration"], e["completedAt"]) for e in fake.data[KEY]] == [(15, iso(at(900)))]


def test_final_entry_waits_for_cortex(storage):
    fake = FakeCortex()
    sync = DailyLogSync(storage, fake.client(), clock=lambda: at(0))
    sync.on_events([WorkEnded("bbeye-1", at(0), at(1200))])
    fake.down = True
    sync.on_tick(Tick(at(1201), working(1200), None))
    assert storage.outbox_summary() == {"pending": 1}
    fake.down = False
    sync.on_tick(Tick(at(1201) + timedelta(minutes=11), working(1200), None))
    assert storage.outbox_summary() == {"done": 1} and fake.data[KEY][0]["duration"] == 20


def test_a_sprint_logged_later_is_not_double_counted(storage):
    fake = FakeCortex()
    sync = DailyLogSync(storage, fake.client(), every=timedelta(minutes=5), clock=lambda: at(0))
    sync.on_events([WorkStarted("bbeye-1", at(0))])
    sync.on_tick(Tick(at(600), working(600), None))
    fake.data[KEY] = fake.data[KEY] + [sprint(300, 900)]  # a 10-minute sprint finishes and is logged
    sync.on_tick(Tick(at(900), working(900), None))
    total = sum(e["duration"] for e in fake.data[KEY])
    assert total == 15  # 5 desk minutes + 10 sprint minutes, never 25
