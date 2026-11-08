"""Enrollment: learn a person's face from the camera. Stores only face embeddings, never images."""

from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np

from bbeye.camera import Camera
from bbeye.vision.faces import FaceRecognizer

MIN_SAMPLES = 5


def collect_embeddings(
    camera: Camera,
    recognizer: FaceRecognizer,
    seconds: float = 20.0,
    max_samples: int = 20,
    every_s: float = 0.5,
    progress: Callable[[int, str], None] = lambda count, message: None,
    show: Callable[[np.ndarray], bool] | None = None,
) -> list[np.ndarray]:
    """Collect varied embeddings of exactly one clearly visible face."""
    samples: list[np.ndarray] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and len(samples) < max_samples:
        frame = camera.read()
        if frame is None:
            progress(len(samples), "waiting for the camera")
            time.sleep(1)
            continue
        faces = recognizer.detect(frame)
        if len(faces) != 1:
            progress(len(samples), "no face found" if not faces else "more than one face; be alone in view")
        elif faces[0].score < 0.9:
            progress(len(samples), "face not clear; look at the camera")
        else:
            embedding = recognizer.embed(frame, faces[0])
            if _is_new(embedding, samples):
                samples.append(embedding)
                progress(len(samples), "got it, turn your head a little")
        if show is not None and not show(frame):
            break
        time.sleep(every_s)
    return samples


def _is_new(embedding: np.ndarray, samples: list[np.ndarray], max_similarity: float = 0.97) -> bool:
    """Skip near-identical samples so the set covers different angles."""
    query = embedding.ravel() / np.linalg.norm(embedding)
    return all(float(query @ (s.ravel() / np.linalg.norm(s))) < max_similarity for s in samples)
