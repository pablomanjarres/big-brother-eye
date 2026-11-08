"""Model files: downloaded once into the data folder and verified by SHA-256."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from bbeye.config import data_dir

log = logging.getLogger(__name__)

_ZOO = "https://github.com/opencv/opencv_zoo/raw/48b10fa505aeb51fbd127fc60a5c660040e823b7/models"
_YOLO = "https://github.com/ultralytics/assets/releases/download/v8.3.0"


@dataclass(frozen=True)
class ModelFile:
    name: str
    url: str
    sha256: str
    license: str


KNOWN_MODELS: dict[str, ModelFile] = {
    model.name: model
    for model in (
        ModelFile(
            "yolo11n.pt",
            f"{_YOLO}/yolo11n.pt",
            "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1",
            "AGPL-3.0 (Ultralytics)",
        ),
        ModelFile(
            "yolo11s.pt",
            f"{_YOLO}/yolo11s.pt",
            "85a76fe86dd8afe384648546b56a7a78580c7cb7b404fc595f97969322d502d5",
            "AGPL-3.0 (Ultralytics)",
        ),
        ModelFile(
            "face_detection_yunet_2023mar.onnx",
            f"{_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
            "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
            "MIT (YuNet, OpenCV Zoo)",
        ),
        ModelFile(
            "face_recognition_sface_2021dec.onnx",
            f"{_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
            "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
            "Apache-2.0 (SFace, OpenCV Zoo)",
        ),
    )
}

FACE_DETECTOR = "face_detection_yunet_2023mar.onnx"
FACE_RECOGNIZER = "face_recognition_sface_2021dec.onnx"


class ModelError(RuntimeError):
    pass


def models_dir() -> Path:
    return data_dir() / "models"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_model(
    name_or_path: str, directory: Path | None = None, client: httpx.Client | None = None
) -> Path:
    """Return a verified local path. Known names are downloaded on first use; other values must be files."""
    known = KNOWN_MODELS.get(name_or_path)
    if known is None:
        path = Path(name_or_path).expanduser()
        if not path.is_file():
            raise ModelError(f"Model {name_or_path!r} is not a known model name and the file does not exist.")
        return path

    directory = directory or models_dir()
    path = directory / known.name
    if path.is_file():
        if _sha256(path) == known.sha256:
            return path
        log.warning("Checksum mismatch for %s; downloading it again", path)
    directory.mkdir(parents=True, exist_ok=True)
    _download(known, path, client)
    return path


def _download(model: ModelFile, path: Path, client: httpx.Client | None) -> None:
    log.info("Downloading %s (%s)", model.name, model.license)
    owned = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=120))
    partial = path.with_name(path.name + ".part")
    digest = hashlib.sha256()
    try:
        with client.stream("GET", model.url) as response, partial.open("wb") as out:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                out.write(chunk)
                digest.update(chunk)
    except httpx.HTTPError as error:
        partial.unlink(missing_ok=True)
        raise ModelError(f"Could not download {model.name}: {error}") from error
    finally:
        if owned:
            client.close()
    if digest.hexdigest() != model.sha256:
        partial.unlink(missing_ok=True)
        raise ModelError(f"Downloaded {model.name} failed its checksum, so it was not used.")
    partial.replace(path)
