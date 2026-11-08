"""Object and person detection with Ultralytics YOLO."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from bbeye.config import DetectorConfig
from bbeye.observation import Box, Detection

# COCO classes that say something about a desk.
INTERESTING = frozenset(
    {"person", "laptop", "keyboard", "mouse", "tv", "cell phone", "book", "cup", "bottle", "chair", "remote"}
)


def _quiet_ultralytics() -> None:
    # Offline mode stops Ultralytics' usage analytics and automatic pip installs.
    # Model files are downloaded and verified by bbeye itself (see vision/models.py).
    os.environ.setdefault("YOLO_OFFLINE", "1")
    os.environ.setdefault("YOLO_VERBOSE", "False")


class YoloDetector:
    def __init__(self, config: DetectorConfig, model_path: Path) -> None:
        _quiet_ultralytics()
        from ultralytics import YOLO

        self.config = config
        self._model = YOLO(str(model_path))
        names: dict[int, str] = self._model.names
        self._classes = [index for index, name in names.items() if name in INTERESTING]
        if not self._classes:
            raise ValueError(f"{model_path} does not detect any of: {', '.join(sorted(INTERESTING))}")

    def detect(self, frame: np.ndarray) -> list[Detection]:
        result = self._model.predict(
            frame,
            conf=self.config.confidence,
            classes=self._classes,
            device=self.config.device or None,
            verbose=False,
        )[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return []
        names = result.names
        return [
            Detection(names[int(cls)], float(conf), Box(*map(float, xyxy)))
            for xyxy, cls, conf in zip(
                boxes.xyxy.cpu().numpy(), boxes.cls.cpu().numpy(), boxes.conf.cpu().numpy(), strict=True
            )
        ]
