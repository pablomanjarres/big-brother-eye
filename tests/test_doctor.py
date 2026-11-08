import numpy as np
import pytest

import bbeye.camera
from bbeye.doctor import _camera_check


class FakeCamera:
    frame: np.ndarray | None = None

    def __init__(self, source, width, height):
        self.source = source

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return FakeCamera.frame


@pytest.fixture(autouse=True)
def fake_camera(monkeypatch):
    monkeypatch.setattr(bbeye.camera, "Camera", FakeCamera)


def test_camera_check_reports_resolution_and_source():
    FakeCamera.frame = np.zeros((480, 640, 3), dtype=np.uint8)
    check = _camera_check("room camera", 0, 640, 480, "unused")
    assert (check.level, check.name, check.message) == ("OK", "room camera", "640x480 from source 0")


def test_camera_check_uses_hint_when_no_frame():
    FakeCamera.frame = None
    check = _camera_check("camera", "rtsp://cam", 640, 480, "check camera.source")
    assert (check.level, check.name, check.message) == ("FAIL", "camera", "check camera.source")
