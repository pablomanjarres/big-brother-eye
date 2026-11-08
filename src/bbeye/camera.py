"""Any camera OpenCV can open: a webcam index, an RTSP/HTTP stream URL or a video file."""

from __future__ import annotations

import logging
import sys
import time

import cv2
import numpy as np

from bbeye.timeutil import backoff

log = logging.getLogger(__name__)


def parse_source(source: str | int) -> str | int:
    if isinstance(source, int):
        return source
    return int(source) if source.strip().isdigit() else source


class Camera:
    def __init__(
        self, source: str | int, width: int = 1280, height: int = 720, flush_frames: int = 4
    ) -> None:
        self.source = parse_source(source)
        self.width, self.height = width, height
        self.flush_frames = flush_frames
        self._capture: cv2.VideoCapture | None = None
        self._failures = 0
        self._retry_at = 0.0

    def _open(self) -> bool:
        if time.monotonic() < self._retry_at:
            return False
        if isinstance(self.source, int) and sys.platform == "win32":
            capture = cv2.VideoCapture(self.source, cv2.CAP_DSHOW)  # DirectShow opens USB webcams reliably
        else:
            capture = cv2.VideoCapture(self.source)
        if not capture.isOpened():
            capture.release()
            self._failed(f"Could not open camera {self._label()}")
            return False
        if isinstance(self.source, int):
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._capture = capture
        return True

    def _failed(self, message: str) -> None:
        self._failures += 1
        delay = backoff(1.0, self._failures, cap_s=60.0).total_seconds()
        self._retry_at = time.monotonic() + delay
        log.warning("%s; retrying in %.0f s", message, delay)

    def _label(self) -> str:
        if isinstance(self.source, int):
            return f"#{self.source}"
        return self.source.split("@")[-1]  # never log credentials embedded in a stream URL

    def read(self) -> np.ndarray | None:
        """The newest frame, or None if the camera is unavailable right now."""
        if self._capture is None and not self._open():
            return None
        assert self._capture is not None
        for _ in range(self.flush_frames):  # drop buffered frames so we analyse "now"
            self._capture.grab()
        ok, frame = self._capture.read()
        if not ok or frame is None:
            self.close()
            self._failed(f"Camera {self._label()} stopped sending frames")
            return None
        self._failures = 0
        return frame

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> Camera:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
