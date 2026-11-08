from datetime import timedelta

import numpy as np
from helpers import at

from bbeye.config import Config, TrackerConfig
from bbeye.judges.rules import RulesJudge
from bbeye.observation import Box, Detection, Face
from bbeye.runner import Eye
from bbeye.status import eye_status
from bbeye.tracker import ActivityChanged, ActivityTracker
from bbeye.vision.room import MotionDetector, RoomWatcher, motion_inside

H, W = 360, 640


def blank():
    return np.zeros((H, W, 3), dtype=np.uint8)


def with_patch(x1, x2, value=255):
    frame = blank()
    frame[60:300, x1:x2] = value
    return frame


def test_motion_detector_needs_change():
    motion = MotionDetector()
    assert motion.update(blank()) is None  # first frame
    assert motion.update(blank()) is None  # nothing moved
    mask = motion.update(with_patch(300, 400))
    assert mask is not None and mask.any()


def test_motion_inside_a_box():
    motion = MotionDetector()
    motion.update(blank())
    mask = motion.update(with_patch(300, 400))
    assert motion_inside(mask, Box(300, 60, 400, 300), (H, W, 3)) > 0.5
    assert motion_inside(mask, Box(0, 0, 80, 360), (H, W, 3)) == 0.0


class Frames:
    def __init__(self, frames):
        self.frames = list(frames)

    def read(self):
        return self.frames.pop(0) if self.frames else None

    def close(self):
        pass


def test_a_static_person_shape_never_counts_but_a_walker_does():
    fixture = Detection("person", 0.45, Box(0, 0, 80, 330))  # the coat at the edge, always "detected"
    walker = Detection("person", 0.8, Box(300, 60, 400, 300))
    detections = {"walker": [fixture, walker], "still": [fixture]}
    state = {"now": "still"}
    frames = [blank(), with_patch(500, 520, 40), with_patch(300, 400)]
    clock = iter([at(t) for t in range(10)])
    watcher = RoomWatcher(Frames(frames), lambda f: detections[state["now"]], clock=lambda: next(clock))
    assert watcher.step() is False  # first frame
    assert watcher.step() is False  # small motion away from any person box; fixture does not move
    state["now"] = "walker"
    assert watcher.step() is True
    assert watcher.person_moved_since(at(-1)) and not watcher.person_moved_since(at(5))


def test_seen_leaving_confirms_away_fast_and_back_dated():
    tracker = ActivityTracker(TrackerConfig(confirm_s=30, away_after_s=120, exit_confirm_s=10), at(0))
    for t in range(0, 62, 2):
        tracker.update(at(t), "working")
    events = []
    for t in range(62, 76, 2):
        events += tracker.update(at(t), "away", seen_leaving=True)
    assert ActivityChanged(at(62), "working", "away") in events


class FakeRoom:
    def __init__(self):
        self.moved_at = None

    @property
    def last_person_motion(self):
        return self.moved_at

    def person_moved_since(self, moment):
        return self.moved_at is not None and self.moved_at > moment

    def stop(self):
        pass


def test_eye_uses_the_room_camera_to_confirm_leaving(storage):
    clock = {"t": at(0)}
    here = {"owner": True}
    person = Detection("person", 0.9, Box(100, 100, 400, 700))
    face = Face(Box(200, 150, 300, 260), "me", 0.8)

    class Cam:
        def read(self):
            return np.zeros((720, 1280, 3), dtype=np.uint8)

        def close(self):
            pass

    room = FakeRoom()
    config = Config(tracker=TrackerConfig(confirm_s=10, away_after_s=120, exit_confirm_s=10))
    eye = Eye(
        config,
        storage,
        Cam(),
        lambda f: [person] if here["owner"] else [],
        lambda f: [face] if here["owner"] else [],
        lambda: 1.0,
        RulesJudge(config.signals),
        clock=lambda: clock["t"],
        room=room,
    )
    for _ in range(30):
        eye.step()
        clock["t"] += timedelta(seconds=2)
    here["owner"] = False
    room.moved_at = clock["t"] + timedelta(seconds=3)  # walks past the room camera after leaving
    for _ in range(8):
        eye.step()
        clock["t"] += timedelta(seconds=2)
    status = eye_status(storage, clock["t"])
    assert status["activity"] == "away"  # 16 s, not 2 minutes
    assert status["room_last_motion"] is not None
