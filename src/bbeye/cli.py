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
    handlers: list[logging.Handler] = [logging.StreamHandler()]
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


def cmd_mcp(args: argparse.Namespace, config: Config) -> int:
    from bbeye.mcp_server import build_server, http_app, token_error

    storage = _open_storage(config)
    server = build_server(storage)
    if not args.http:
        server.run("stdio")
        return 0
    token = load_secrets().get("BBEYE_MCP_TOKEN", "")
    if problem := token_error(token):
        print(f"Cannot serve MCP over HTTP: {problem} (in secrets.env).")
        return 1
    import uvicorn

    app = http_app(server, token, config.mcp.host, config.mcp.allowed_hosts)
    print(f"MCP over HTTP at http://{config.mcp.host}:{config.mcp.port}/mcp (bearer token required)")
    uvicorn.run(app, host=config.mcp.host, port=config.mcp.port, log_level="warning")
    return 0


def cmd_service(args: argparse.Namespace, config: Config) -> int:
    from bbeye import service

    target = service.IDLE if args.idle_server else service.EYE
    try:
        print(
            service.install(data_dir() / "logs", target)
            if args.action == "install"
            else service.uninstall(target)
        )
    except RuntimeError as error:
        print(error)
        return 1
    return 0


def cmd_idle_serve(args: argparse.Namespace, config: Config) -> int:
    from bbeye.signals.idle import input_idle_reader
    from bbeye.signals.idle_server import make_server

    server = make_server(args.host, args.port, input_idle_reader())
    log.info("Serving idle time at http://%s:%d/idle (localhost and Tailscale only)", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_notify_test(args: argparse.Namespace, config: Config) -> int:
    from bbeye.alerts import AlertService
    from bbeye.notify.factory import build_notifiers

    notifiers = build_notifiers(load_secrets())
    service = AlertService(_open_storage(config), notifiers)
    now = now_utc()
    service.raise_alert(
        now, "test", "Big Brother Eye test", "If you can read this, alerts work.", f"test:{now.isoformat()}"
    )
    alert = service.storage.alerts(limit=1)[0]
    service.storage.acknowledge_alert(alert.id, now)
    print("Sent through: " + ", ".join(n.name for n in notifiers))
    return 0


# ── parser ─────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bbeye", description="Big Brother Eye: a private, YOLO-powered desk monitor."
    )
    parser.add_argument("--version", action="version", version=f"bbeye {__version__}")
    parser.add_argument("--config", type=Path, help="path to config.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="write an example config and secrets file")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    sub.add_parser("doctor", help="check camera, models, keys and integrations").set_defaults(func=cmd_doctor)

    p = sub.add_parser("enroll", help="teach the Eye your face (stores embeddings, not images)")
    p.add_argument("--name", help="who is enrolling (default: owner from config)")
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--show", action="store_true", help="show the camera while enrolling")
    p.add_argument("--forget", action="store_true", help="delete this person's face samples")
    p.set_defaults(func=cmd_enroll)

    p = sub.add_parser("run", help="start watching")
    p.add_argument("--show", action="store_true", help="preview window with detections")
    p.set_defaults(func=cmd_run)

    sub.add_parser("status", help="what the Eye sees right now").set_defaults(func=cmd_status)

    p = sub.add_parser("hours", help="work sessions for a day")
    p.add_argument("--day", help="YYYY-MM-DD (default: today)")
    p.set_defaults(func=cmd_hours)

    p = sub.add_parser("alerts", help="recent alerts")
    p.add_argument("--all", action="store_true", help="include acknowledged alerts")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_alerts)

    p = sub.add_parser("mcp", help="serve the MCP tools (stdio by default)")
    p.add_argument("--http", action="store_true", help="serve over HTTP with a bearer token")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("service", help="start automatically at login")
    p.add_argument("action", choices=["install", "uninstall"])
    p.add_argument("--idle-server", action="store_true", help="the idle-serve endpoint instead of the Eye")
    p.set_defaults(func=cmd_service)

    p = sub.add_parser("idle-serve", help="share this computer's keyboard/mouse idle time with the Eye")
    p.add_argument(
        "--host", default="127.0.0.1", help="address to listen on (share it with `tailscale serve`)"
    )
    p.add_argument("--port", type=int, default=8771)
    p.set_defaults(func=cmd_idle_serve)

    sub.add_parser("notify-test", help="send a test alert to every notifier").set_defaults(
        func=cmd_notify_test
    )
    return parser


def _ensure_std_streams() -> None:
    """pythonw.exe (the Windows login task) starts with no stdout/stderr. Libraries that
    write to them, or configure logging for them, would crash; give them a null device."""
    import os

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - lives for the process
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def main(argv: list[str] | None = None) -> int:
    _ensure_std_streams()
    args = build_parser().parse_args(argv)
    if args.config:
        import os

        os.environ["BBEYE_CONFIG_DIR"] = str(args.config.expanduser().resolve().parent)
    # Logs go to stderr (and a file for long-running commands); stdout stays clean for stdio MCP.
    setup_logging(args.verbose, to_file=args.command in ("run", "mcp", "idle-serve"))
    try:
        config = load_config(args.config.expanduser() if args.config else None)
    except ConfigError as error:
        print(f"Config error: {error}", file=sys.stderr)
        return 2
    return int(args.func(args, config) or 0)


if __name__ == "__main__":
    sys.exit(main())
