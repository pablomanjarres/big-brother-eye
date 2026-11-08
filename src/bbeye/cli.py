"""Command line: `bbeye init`, `doctor`, `enroll`, `run`, `status`, `hours`, `alerts`, `mcp`, `service`."""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import signal
import sys
from pathlib import Path

from bbeye import __version__
from bbeye.config import (
    EXAMPLE_CONFIG,
    EXAMPLE_SECRETS,
    Config,
    ConfigError,
    config_dir,
    config_path,
    data_dir,
    database_path,
    load_config,
    load_secrets,
)
from bbeye.storage import Storage
from bbeye.timeutil import now_utc

log = logging.getLogger("bbeye")


def setup_logging(verbose: bool, to_file: bool) -> None:
    # pythonw.exe (the Windows login task) has no console, so sys.stderr is None there.
    handlers: list[logging.Handler] = [logging.StreamHandler()] if sys.stderr is not None else []
    if to_file:
        logs = data_dir() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                logs / "bbeye.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
        )
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    for noisy in ("httpx", "httpcore", "ultralytics", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _open_storage(config: Config) -> Storage:
    return Storage(database_path(config))


# ── commands ───────────────────────────────────────────────────────────────────


def cmd_init(args: argparse.Namespace, config: Config) -> int:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not args.force:
        print(f"Config already exists: {path} (use --force to overwrite)")
    else:
        path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
        print(f"Wrote {path}")
    secrets = config_dir() / "secrets.env"
    if not secrets.exists():
        secrets.write_text(EXAMPLE_SECRETS, encoding="utf-8")
        if sys.platform != "win32":
            secrets.chmod(0o600)
        print(f"Wrote {secrets} (private: put API keys here)")
    print(f"Data folder: {data_dir()}")
    print("Next: edit the config, then run `bbeye doctor` and `bbeye enroll`.")
    return 0


def cmd_doctor(args: argparse.Namespace, config: Config) -> int:
    from bbeye.doctor import run_checks

    results = run_checks(config, load_secrets())
    width = max(len(r.name) for r in results)
    for result in results:
        print(f"{result.level:<4}  {result.name:<{width}}  {result.message}")
    failed = [r for r in results if r.level == "FAIL"]
    print(f"\n{len(failed)} problem(s)." if failed else "\nAll required checks passed.")
    return 1 if failed else 0


def cmd_enroll(args: argparse.Namespace, config: Config) -> int:
    from bbeye.camera import Camera
    from bbeye.enroll import MIN_SAMPLES, collect_embeddings
    from bbeye.vision.faces import FaceRecognizer
    from bbeye.vision.models import FACE_DETECTOR, FACE_RECOGNIZER, resolve_model

    name = args.name or config.owner
    storage = _open_storage(config)
    if args.forget:
        removed = storage.forget_faces(name)
        print(f"Removed {removed} face sample(s) for {name!r}.")
        return 0
    recognizer = FaceRecognizer(
        resolve_model(FACE_DETECTOR), resolve_model(FACE_RECOGNIZER), config.faces.match_threshold
    )
    show = _preview_window("bbeye enroll - press q to stop") if args.show else None
    print(f"Enrolling {name!r}: look at the camera and slowly turn your head for {args.seconds:.0f} seconds.")
    last = [""]

    def progress(count: int, message: str) -> None:
        line = f"  {count:2d} samples - {message}"
        if line != last[0]:
            print(line)
            last[0] = line

    with Camera(config.camera.source, config.camera.width, config.camera.height) as camera:
        samples = collect_embeddings(camera, recognizer, seconds=args.seconds, progress=progress, show=show)
    if len(samples) < MIN_SAMPLES:
        print(f"Only {len(samples)} good samples (need {MIN_SAMPLES}). Nothing saved. Try better light.")
        return 1
    storage.replace_faces(name, samples, now_utc())
    print(f"Saved {len(samples)} face samples for {name!r}. No images were stored.")
    if name != config.owner:
        print(
            f"Note: the owner in your config is {config.owner!r}; {name!r} will be counted as another person."
        )
    return 0


def _preview_window(title: str):
    import cv2

    def show(frame) -> bool:
        cv2.imshow(title, frame)
        return (cv2.waitKey(1) & 0xFF) != ord("q")

    return show


def cmd_run(args: argparse.Namespace, config: Config) -> int:
    from bbeye.runner import build_eye

    secrets = load_secrets()
    eye = build_eye(config, secrets)
    if config.mcp.serve_http:
        from bbeye.mcp_server import serve_http_in_background, token_error

        token = secrets.get("BBEYE_MCP_TOKEN", "")
        if problem := token_error(token):
            print(f"mcp.serve_http is on, but {problem}.")
            return 1

        serve_http_in_background(
            _open_storage(config), token, config.mcp.host, config.mcp.port, config.mcp.allowed_hosts
        )
    if args.show:
        import cv2

        from bbeye.vision.overlay import draw

        window = _preview_window("Big Brother Eye - press q to quit")
        eye.on_frame = lambda frame, observation, verdict: window(draw(frame, observation, verdict))
    else:
        cv2 = None

    def stop(*_: object) -> None:
        eye.stop()

    signal.signal(signal.SIGTERM, stop)
    try:
        eye.run()
    except KeyboardInterrupt:
        eye.stop()
    finally:
        if cv2 is not None:
            cv2.destroyAllWindows()
    return 0


def cmd_status(args: argparse.Namespace, config: Config) -> int:
    from bbeye.status import eye_status

    print(json.dumps(eye_status(_open_storage(config), now_utc()), indent=2))
    return 0


def cmd_hours(args: argparse.Namespace, config: Config) -> int:
    from bbeye.status import parse_moment, work_summary

    now = now_utc()
    print(json.dumps(work_summary(_open_storage(config), parse_moment(args.day or "", now), now), indent=2))
    return 0


def cmd_alerts(args: argparse.Namespace, config: Config) -> int:
    from bbeye.status import alert_list

    print(json.dumps(alert_list(_open_storage(config), args.all, args.limit), indent=2))
    return 0


