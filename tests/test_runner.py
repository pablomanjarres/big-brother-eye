from datetime import timedelta

import numpy as np
from helpers import at

from bbeye.config import Config, TrackerConfig
from bbeye.judges.rules import RulesJudge
from bbeye.observation import Box, Detection, Face
from bbeye.runner import Eye
from bbeye.status import eye_status, work_summary
from bbeye.tracker import WorkEnded

PERSON = Detection("person", 0.9, Box(100, 100, 400, 700))
LAPTOP = Detection("laptop", 0.9, Box(150, 500, 350, 650))
FACE = Face(Box(200, 150, 300, 260), "me", 0.8)


class Clock:
    def __init__(self):
        self.t = at(0)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


class FakeCamera:
    def __init__(self):
        self.on = True
        self.closed = False

    def read(self):
        return np.zeros((720, 1280, 3), dtype=np.uint8) if self.on else None

    def close(self):
        self.closed = True


class Scene:
    def __init__(self):
        self.owner_here = True

    def detect(self, frame):
        return [PERSON, LAPTOP] if self.owner_here else [LAPTOP]

    def identify(self, frame):
        return [FACE] if self.owner_here else []


class RecordingReactor:
    def __init__(self):
        self.events, self.ticks = [], 0

    def on_events(self, events):
        self.events += events

    def on_tick(self, tick):
        self.ticks += 1


def make_eye(storage, clock, scene, camera, reactor):
    config = Config(tracker=TrackerConfig(confirm_s=10, away_after_s=30, work_gap_s=60))
    return Eye(
        config,
        storage,
        camera,
        scene.detect,
        scene.identify,
        lambda: 1.0,
        RulesJudge(config.signals),
        [reactor],
        clock,
    )


def run(eye, clock, seconds, step=2):
    for _ in range(int(seconds / step)):
        eye.step()
        clock.advance(step)


def test_full_loop_tracks_work_and_absence(storage):
    clock, scene, camera, reactor = Clock(), Scene(), FakeCamera(), RecordingReactor()
    eye = make_eye(storage, clock, scene, camera, reactor)
    eye.recover()
    run(eye, clock, 600)
    status = eye_status(storage, clock())
    assert status["activity"] == "working" and status["eye_online"] and status["owner_present"]
    assert status["work_session_started_at"] == "2026-11-07T15:00:00.000Z"

    scene.owner_here = False
    run(eye, clock, 200)
    assert eye_status(storage, clock())["activity"] == "away"
    summary = work_summary(storage, clock(), clock())
    assert summary["total_minutes"] == 10.0 and len(summary["sessions"]) == 1
    assert any(isinstance(e, WorkEnded) for e in reactor.events)


def test_camera_outage_is_reported_and_does_not_count_as_away(storage):
    clock, scene, camera, reactor = Clock(), Scene(), FakeCamera(), RecordingReactor()
    eye = make_eye(storage, clock, scene, camera, reactor)
    run(eye, clock, 60)
    camera.on = False
    run(eye, clock, 300)
    status = eye_status(storage, clock())
    assert status["camera"] == "unavailable" and status["activity"] == "working"
    assert reactor.ticks == 180


def test_shutdown_closes_session_and_recover_closes_leftovers(storage):
    clock, scene, camera, reactor = Clock(), Scene(), FakeCamera(), RecordingReactor()
    eye = make_eye(storage, clock, scene, camera, reactor)
    run(eye, clock, 120)
    eye.shutdown()
    assert storage.open_work_session() is None and camera.closed
    assert not eye_status(storage, clock())["eye_online"]

    # Simulate a crash: a session left open in the database.
    storage.start_work_session("bbeye-crashed", at(1000))
    storage.touch_work_session("bbeye-crashed", at(1500))
    reactor2 = RecordingReactor()
    eye2 = make_eye(storage, clock, scene, FakeCamera(), reactor2)
    eye2.recover()
    assert storage.open_work_session() is None
    assert reactor2.events == [WorkEnded("bbeye-crashed", at(1000), at(1500))]


def test_a_failing_reactor_does_not_stop_the_loop(storage):
    class Broken:
        def on_events(self, events):
            raise RuntimeError("boom")

        def on_tick(self, tick):
            raise RuntimeError("boom")

    clock, scene, camera = Clock(), Scene(), FakeCamera()
    eye = make_eye(storage, clock, scene, camera, Broken())
    run(eye, clock, 60)
    assert storage.open_work_session() is not None
