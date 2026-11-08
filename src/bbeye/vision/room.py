"""A second camera that watches the way in and out of the room.

It runs on its own thread at a few frames per second. Frame differencing finds motion
cheaply; person detection runs only on frames that moved, and a person counts only when
the motion is inside their box. A static false positive (a coat, a door frame) never
moves, so it never counts.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime

import cv2
import numpy as np

from bbeye.observation import Box, Detection
from bbeye.timeutil import now_utc

log = logging.getLogger(__name__)

SMALL = (160, 90)  # motion is measured on a tiny grayscale copy


class MotionDetector:
    """Pixels that changed since the previous frame, as a small boolean mask."""

    def __init__(self, pixel_threshold: int = 25, min_fraction: float = 0.01) -> None:
        self.pixel_threshold, self.min_fraction = pixel_threshold, min_fraction
        self._previous: np.ndarray | None = None

    def update(self, frame: np.ndarray) -> np.ndarray | None:
        """The motion mask, or None for the first frame or when too little moved."""
        small = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(frame, SMALL), cv2.COLOR_BGR2GRAY), (5, 5), 0)
        previous, self._previous = self._previous, small
        if previous is None:
            return None
        mask = cv2.absdiff(small, previous) > self.pixel_threshold
        return mask if mask.mean() >= self.min_fraction else None


def motion_inside(mask: np.ndarray, box: Box, frame_shape: tuple[int, ...]) -> float:
    """Share of the box's area (in mask pixels) that moved."""
    height, width = frame_shape[:2]
    sx, sy = mask.shape[1] / width, mask.shape[0] / height
    x1, x2 = max(0, int(box.x1 * sx)), min(mask.shape[1], int(box.x2 * sx) + 1)
    y1, y2 = max(0, int(box.y1 * sy)), min(mask.shape[0], int(box.y2 * sy) + 1)
    region = mask[y1:y2, x1:x2]
    return float(region.mean()) if region.size else 0.0


class RoomWatcher:
    def __init__(
        self,
        camera,  # anything with read() -> frame | None and close()
        detect: Callable[[np.ndarray], list[Detection]],
        motion: MotionDetector | None = None,
        interval_s: float = 0.15,
        min_box_motion: float = 0.05,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.camera, self.detect = camera, detect
        self.motion = motion or MotionDetector()
        self.interval_s, self.min_box_motion, self.clock = interval_s, min_box_motion, clock
        self._lock = threading.Lock()
        self._last_person_motion: datetime | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def step(self) -> bool:
        """Look at one frame. Returns True when a moving person was seen."""
        frame = self.camera.read()
        if frame is None:
            return False
        mask = self.motion.update(frame)
        if mask is None:
            return False
        people = [d for d in self.detect(frame) if d.label == "person"]
        if not any(motion_inside(mask, d.box, frame.shape) >= self.min_box_motion for d in people):
            return False
        with self._lock:
            self._last_person_motion = self.clock()
        return True

    def person_moved_since(self, moment: datetime) -> bool:
        with self._lock:
            return self._last_person_motion is not None and self._last_person_motion > moment

    @property
    def last_person_motion(self) -> datetime | None:
        with self._lock:
            return self._last_person_motion

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="bbeye-room", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:  # a broken frame must not kill the watcher
                log.exception("Room camera step failed")
            self._stop.wait(self.interval_s)
        self.camera.close()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
