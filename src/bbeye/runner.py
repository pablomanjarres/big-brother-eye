"""The capture loop and the place where all the parts are wired together."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

import numpy as np

from bbeye.alerts import AlertService
from bbeye.camera import Camera
from bbeye.config import Config, database_path
from bbeye.judges.base import Judge
from bbeye.observation import Detection, Face, Observation, Verdict
from bbeye.perception import IdentityMemory, build_observation
from bbeye.reactors.base import Reactor, Tick
from bbeye.storage import Storage
from bbeye.timeutil import iso, now_utc
from bbeye.tracker import ActivityChanged, ActivityTracker, Event, WorkEnded, WorkStarted

log = logging.getLogger(__name__)


class FrameSource(Protocol):
    def read(self) -> np.ndarray | None: ...

    def close(self) -> None: ...


@dataclass
class Eye:
    config: Config
    storage: Storage
    camera: FrameSource
    detect: Callable[[np.ndarray], list[Detection]]
    identify: Callable[[np.ndarray], list[Face]]
    idle_seconds: Callable[[], float | None]
    judge: Judge
    reactors: list[Reactor] = field(default_factory=list)
    clock: Callable[[], datetime] = now_utc
    on_frame: Callable[[np.ndarray, Observation, Verdict], bool] | None = None  # return False to stop

    def __post_init__(self) -> None:
        now = self.clock()
        self.tracker = ActivityTracker(self.config.tracker, now)
        self.memory = IdentityMemory(
            self.config.faces.identity_hold_s, continuity_threshold=self.config.faces.continuity_threshold
        )
        self._pruned_at: datetime | None = None
        self.stopped = threading.Event()

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def recover(self) -> None:
        """Close what a previous run left open (crash, power loss, sleep) at its last known moment."""
        now = self.clock()
        session = self.storage.open_work_session()
        if session is not None:
            log.info("Closing work session %s left open by the previous run", session.id)
            self._dispatch([WorkEnded(session.id, session.started_at, session.last_working_at)])
        self.storage.end_open_visits(now)

    def run(self) -> None:
        self.recover()
        interval = self.config.camera.interval_s
        log.info(
            "Big Brother Eye running: sampling every %.1f s with the %s judge", interval, self.judge.name
        )
        try:
            while not self.stopped.is_set():
                started = time.monotonic()
                if not self.step():
                    break
                self.stopped.wait(max(0.0, interval - (time.monotonic() - started)))
        finally:
            self.shutdown()

    def stop(self) -> None:
        self.stopped.set()

    def shutdown(self) -> None:
        events = self.tracker.close()
        if events:
            self._dispatch(events)
        self._tick(None)  # lets reactors flush what the shutdown produced
        self.storage.end_open_visits(self.clock())
        self.storage.set_meta({"camera": "stopped", "running": "0"})
        self.camera.close()

    # ── one sample ─────────────────────────────────────────────────────────────

    def step(self) -> bool:
        now = self.clock()
        frame = self.camera.read()
        observation = None
        keep_going = True
        if frame is not None:
            idle = self.idle_seconds()
            observation = build_observation(
                now, self.config.owner, self.detect(frame), self.identify(frame), idle, self.memory, frame
            )
            verdict = self.judge.judge(observation)
            events = self.tracker.update(now, verdict.activity)
            self.storage.record_observation(observation, verdict)
            self._dispatch(events)
            snapshot = self.tracker.snapshot()
            if snapshot.session_id and snapshot.last_working_at:
                self.storage.touch_work_session(snapshot.session_id, snapshot.last_working_at)
            if self.on_frame is not None:
                keep_going = self.on_frame(frame, observation, verdict)
        self._tick(observation)
        self._write_meta(now, observation)
        self._prune(now)
        return keep_going

    def _dispatch(self, events: list[Event]) -> None:
        for event in events:
            if isinstance(event, ActivityChanged):
                self.storage.record_activity_change(event.at, event.previous, event.current)
                log.info(
                    "Activity: %s -> %s (since %s)", event.previous, event.current, event.at.astimezone()
                )
            elif isinstance(event, WorkStarted):
                self.storage.start_work_session(event.session_id, event.at)
            elif isinstance(event, WorkEnded):
                self.storage.end_work_session(event.session_id, event.ended_at)
                minutes = (event.ended_at - event.started_at).total_seconds() / 60
                log.info("Work session %s ended after %.0f min", event.session_id, minutes)
        for reactor in self.reactors:
            try:
                reactor.on_events(events)
            except Exception:
                log.exception("%s failed handling events", type(reactor).__name__)

    def _tick(self, observation: Observation | None) -> None:
        tick = Tick(self.clock(), self.tracker.snapshot(), observation)
        for reactor in self.reactors:
            try:
                reactor.on_tick(tick)
            except Exception:
                log.exception("%s failed on tick", type(reactor).__name__)

    def _write_meta(self, now: datetime, observation: Observation | None) -> None:
        snapshot = self.tracker.snapshot()
        values = {
            "heartbeat_at": iso(now),
            "running": "1",
            "camera": "ok" if observation is not None else "unavailable",
            "judge": self.judge.name,
            "owner": self.config.owner,
            "activity": snapshot.activity,
            "activity_since": iso(snapshot.since),
        }
        if observation is not None:
            values["owner_present"] = "1" if observation.owner_present else "0"
            values["others"] = str(len(observation.others))
        self.storage.set_meta(values)

    def _prune(self, now: datetime) -> None:
        if self._pruned_at and now - self._pruned_at < timedelta(hours=6):
            return
        self._pruned_at = now
        days = self.config.storage.observation_retention_days
        if days > 0:
            removed = self.storage.prune_observations(now - timedelta(days=days))
            if removed:
                log.info("Removed %d observations older than %d days", removed, days)


def build_eye(config: Config, secrets: dict[str, str], storage: Storage | None = None) -> Eye:
    """Composition root: the only place that knows which concrete parts are used."""
    from bbeye.judges.factory import build_judge
    from bbeye.notify.factory import build_notifiers
    from bbeye.signals.idle import build_idle_reader
    from bbeye.vision.detector import YoloDetector
    from bbeye.vision.faces import FaceRecognizer
    from bbeye.vision.models import FACE_DETECTOR, FACE_RECOGNIZER, resolve_model

    storage = storage or Storage(database_path(config))
    detector = YoloDetector(config.detector, resolve_model(config.detector.model))
    known = storage.faces()
    recognizer = FaceRecognizer(
        resolve_model(FACE_DETECTOR), resolve_model(FACE_RECOGNIZER), config.faces.match_threshold, known
    )
    if config.owner not in known:
        log.warning(
            "No face enrolled for %r. Run `bbeye enroll` first, or you will always count as away.",
            config.owner,
        )
    alerts = AlertService(storage, build_notifiers(secrets))
    return Eye(
        config=config,
        storage=storage,
        camera=Camera(config.camera.source, config.camera.width, config.camera.height),
        detect=detector.detect,
        identify=recognizer.identify,
        idle_seconds=build_idle_reader(config.signals.use_input_idle, config.signals.idle_urls),
        judge=build_judge(config, secrets),
        reactors=build_reactors(config, secrets, storage, alerts),
    )


def build_reactors(
    config: Config, secrets: dict[str, str], storage: Storage, alerts: AlertService
) -> list[Reactor]:
    from bbeye.reactors.visitors import VisitorWatch

    reactors: list[Reactor] = [VisitorWatch(storage, alerts, config.visitors)]
    cortex = None
    if config.cortex.enabled:
        from bbeye.integrations.cortex import CortexClient

        cortex = CortexClient(config.cortex.url)
        if config.cortex.track_work_hours:
            from bbeye.reactors.work_hours import WorkHoursSync

            if config.cortex.project_id:
                reactors.append(WorkHoursSync(storage, cortex, config.cortex.project_id))
            else:
                log.warning("cortex.project_id is empty, so work hours are not sent to Cortex")
        if config.cortex.pause_sprint:
            from bbeye.reactors.sprint import SprintGuard

            reactors.append(
                SprintGuard(
                    storage,
                    cortex,
                    timedelta(minutes=config.cortex.pause_after_min),
                    resume=config.cortex.resume_sprint,
                )
            )

    from bbeye.calendars.base import CalendarUnavailable
    from bbeye.calendars.factory import build_calendar_source

    try:
        source = build_calendar_source(config, secrets, cortex)
    except CalendarUnavailable as error:
        log.warning("Calendar disabled: %s", error)
        source = None
    if source is not None:
        from bbeye.reactors.calendar_watch import CalendarWatch

        reactors.append(CalendarWatch(source, alerts, config.calendar))
    return reactors
