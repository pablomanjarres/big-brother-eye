from helpers import at

from bbeye.config import TrackerConfig
from bbeye.tracker import ActivityChanged, ActivityTracker, WorkEnded, WorkStarted

CONFIG = TrackerConfig(confirm_s=30, away_after_s=120, work_gap_s=300)


def feed(tracker, start, end, activity, step=2):
    events = []
    t = start
    while t <= end:
        events += tracker.update(at(t), activity)
        t += step
    return events


def test_working_is_confirmed_and_back_dated():
    tracker = ActivityTracker(CONFIG, at(0))
    events = feed(tracker, 10, 50, "working")
    assert events[0] == ActivityChanged(at(10), "away", "working")
    assert events[1] == WorkStarted("bbeye-20261107T150010", at(10))
    assert tracker.snapshot().activity == "working"


def test_short_blips_do_not_change_activity():
    tracker = ActivityTracker(CONFIG, at(0))
    feed(tracker, 0, 40, "working")
    events = feed(tracker, 42, 60, "away")  # 18 s of missed detections
    events += feed(tracker, 62, 100, "working")
    assert events == []


def test_away_needs_the_longer_grace():
    tracker = ActivityTracker(CONFIG, at(0))
    feed(tracker, 0, 40, "working")
    events = feed(tracker, 42, 160, "away")
    assert events == []  # 118 s < 120 s
    events = tracker.update(at(162), "away")
    assert events == [ActivityChanged(at(42), "working", "away")]


def test_session_survives_a_short_break_and_ends_at_last_work():
    tracker = ActivityTracker(CONFIG, at(0))
    feed(tracker, 0, 100, "working")
    feed(tracker, 102, 200, "phone")  # phone confirmed at 102
    events = feed(tracker, 202, 300, "working")
    assert [type(e) for e in events] == [ActivityChanged]  # same session continues
    events = feed(tracker, 302, 302 + 300 + 30, "away")
    ended = [e for e in events if isinstance(e, WorkEnded)]
    assert ended == [WorkEnded("bbeye-20261107T150000", at(0), at(302))]


def test_close_ends_open_session_at_last_working_moment():
    tracker = ActivityTracker(CONFIG, at(0))
    feed(tracker, 0, 100, "working")
    assert tracker.close() == [WorkEnded("bbeye-20261107T150000", at(0), at(100))]
    assert tracker.close() == []


def test_last_working_time_stops_at_pending_change():
    tracker = ActivityTracker(CONFIG, at(0))
    feed(tracker, 0, 100, "working")
    feed(tracker, 102, 110, "break")  # pending, not confirmed yet
    assert tracker.snapshot().last_working_at == at(102)


def test_laptop_sleep_is_not_counted_as_work():
    tracker = ActivityTracker(CONFIG, at(0))
    feed(tracker, 0, 60, "working")
    nine_hours = 9 * 3600
    events = tracker.update(at(nine_hours), "away")  # lid opened, nobody there
    assert ActivityChanged(at(60), "working", "away") in events
    assert WorkEnded("bbeye-20261107T150000", at(0), at(60)) in events


def test_waking_up_at_the_desk_starts_a_new_session():
    tracker = ActivityTracker(CONFIG, at(0))
    feed(tracker, 0, 60, "working")
    events = feed(tracker, 4000, 4040, "working")
    assert WorkEnded("bbeye-20261107T150000", at(0), at(60)) in events
    assert any(isinstance(e, WorkStarted) and e.at == at(4000) for e in events)


def test_one_misjudged_frame_never_creates_a_session():
    tracker = ActivityTracker(CONFIG, at(0))
    events = feed(tracker, 0, 100, "away")
    events += tracker.update(at(102), "working")
    events += feed(tracker, 104, 400, "away")
    assert not any(isinstance(e, WorkStarted) for e in events)


def test_one_odd_frame_does_not_shift_when_you_left():
    tracker = ActivityTracker(CONFIG, at(0))
    feed(tracker, 0, 40, "break")
    events = feed(tracker, 42, 140, "away")
    events += tracker.update(at(142), "break")  # one false detection
    events += feed(tracker, 144, 200, "away")
    assert [e for e in events if isinstance(e, ActivityChanged)] == [ActivityChanged(at(42), "break", "away")]


def test_flapping_between_phone_and_break_still_leaves_work():
    tracker = ActivityTracker(CONFIG, at(0))
    feed(tracker, 0, 60, "working")
    events = []
    for i, t in enumerate(range(62, 120, 2)):
        events += tracker.update(at(t), "phone" if i % 2 else "break")
    changes = [e for e in events if isinstance(e, ActivityChanged)]
    assert len(changes) == 1 and changes[0].previous == "working" and changes[0].at == at(62)


def test_pending_run_stays_bounded():
    tracker = ActivityTracker(CONFIG, at(0))
    feed(tracker, 0, 60, "working")
    for i, t in enumerate(range(62, 20000, 2)):  # endless 50/50 mix never decides
        tracker.update(at(t), "working" if i % 2 else "away")
    assert len(tracker._run) <= 2 * 120 / 2 + 2
