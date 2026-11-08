"""Face detection (YuNet) and recognition (SFace), both run locally through OpenCV.

Only enrolled people have embeddings stored. Unknown faces are compared and forgotten.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from bbeye.observation import Box, Face

MIN_FACE_PX = 40

# OpenCV 5's DNN engine prints a harmless warning per model load; keep real errors only.
cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)


@dataclass(frozen=True)
class DetectedFace:
    row: np.ndarray  # YuNet output row: box, 5 landmarks, score
    box: Box
    score: float


class FaceRecognizer:
    def __init__(
        self,
        detector_path: Path,
        recognizer_path: Path,
        threshold: float,
        known: dict[str, list[np.ndarray]] | None = None,
        min_score: float = 0.8,
    ) -> None:
        self.threshold = threshold
        self._detector = cv2.FaceDetectorYN.create(str(detector_path), "", (320, 320), min_score, 0.3, 5000)
        self._recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")
        self._known: dict[str, np.ndarray] = {}
        self.set_known(known or {})

    def set_known(self, known: dict[str, list[np.ndarray]]) -> None:
        self._known = {name: _normalize(np.vstack(samples)) for name, samples in known.items() if samples}

    @property
    def known_names(self) -> list[str]:
        return sorted(self._known)

    def detect(self, frame: np.ndarray) -> list[DetectedFace]:
        height, width = frame.shape[:2]
        self._detector.setInputSize((width, height))
        _, rows = self._detector.detect(frame)
        if rows is None:
            return []
        found = []
        for row in rows:
            x, y, w, h = (float(v) for v in row[:4])
            if w < MIN_FACE_PX or h < MIN_FACE_PX:
                continue
            found.append(DetectedFace(row, Box(x, y, x + w, y + h), float(row[-1])))
        return found

    def embed(self, frame: np.ndarray, face: DetectedFace) -> np.ndarray:
        aligned = self._recognizer.alignCrop(frame, face.row)
        return np.asarray(self._recognizer.feature(aligned), dtype=np.float32).reshape(1, -1)

    def match(self, embedding: np.ndarray) -> tuple[str | None, float]:
        """Best enrolled name and its cosine similarity, or (None, best score) below the threshold."""
        best_name, best = None, -1.0
        query = _normalize(embedding)
        for name, samples in self._known.items():
            score = float(np.max(samples @ query.T))
            if score > best:
                best_name, best = name, score
        return (best_name, best) if best >= self.threshold else (None, best)

    def identify(self, frame: np.ndarray) -> list[Face]:
        faces = []
        for detected in self.detect(frame):
            name, score = self.match(self.embed(frame, detected))
            faces.append(Face(detected.box, name, score))
        return faces


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)
