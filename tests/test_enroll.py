import numpy as np

from bbeye.enroll import MIN_SAMPLES, _is_new, collect_embeddings
from bbeye.observation import Box
from bbeye.vision.faces import DetectedFace


def test_is_new_skips_near_duplicates():
    a = np.ones((1, 128), dtype=np.float32)
    b = a.copy()
    b[0, :64] = -1
    assert _is_new(a, [])
    assert not _is_new(a * 2, [a])
    assert _is_new(b, [a])


class FakeCamera:
    def read(self):
        return np.zeros((10, 10, 3), dtype=np.uint8)


class FakeRecognizer:
    def __init__(self, faces_per_frame=1, score=0.95):
        self.faces_per_frame, self.score, self.n = faces_per_frame, score, 0

    def detect(self, frame):
        return [DetectedFace(np.zeros(15), Box(0, 0, 50, 50), self.score)] * self.faces_per_frame

    def embed(self, frame, face):
        self.n += 1
        vector = np.zeros((1, 128), dtype=np.float32)
        vector[0, self.n % 128] = 1.0  # every sample a different direction
        return vector


def test_collects_distinct_samples():
    samples = collect_embeddings(FakeCamera(), FakeRecognizer(), seconds=5, max_samples=8, every_s=0)
    assert len(samples) == 8 >= MIN_SAMPLES


def test_refuses_crowds_and_blurry_faces():
    assert collect_embeddings(FakeCamera(), FakeRecognizer(faces_per_frame=2), seconds=0.2, every_s=0) == []
    assert collect_embeddings(FakeCamera(), FakeRecognizer(score=0.5), seconds=0.2, every_s=0) == []
