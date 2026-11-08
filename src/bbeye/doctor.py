"""`bbeye doctor`: checks every part and says how to fix what is wrong."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from bbeye.config import Config, config_path, database_path
from bbeye.timeutil import now_utc


@dataclass(frozen=True)
class Check:
    level: str  # OK, WARN, FAIL, SKIP
    name: str
    message: str


def run_checks(config: Config, secrets: dict[str, str]) -> list[Check]:
    checks: list[Check] = []
    add = checks.append
    add(
        Check("OK", "config", str(config_path()) if config_path().exists() else "defaults (run `bbeye init`)")
    )

    from bbeye.storage import Storage

    try:
        storage = Storage(database_path(config))
        add(Check("OK", "database", str(database_path(config))))
    except Exception as error:  # report, never crash the doctor
        add(Check("FAIL", "database", f"{database_path(config)}: {error}"))
        return checks

    from bbeye.vision.models import FACE_DETECTOR, FACE_RECOGNIZER, ModelError, resolve_model

    for label, name in (
        ("yolo model", config.detector.model),
        ("face detector", FACE_DETECTOR),
        ("face recognizer", FACE_RECOGNIZER),
    ):
        try:
            add(Check("OK", label, str(resolve_model(name))))
        except ModelError as error:
            add(Check("FAIL", label, str(error)))

    from bbeye.camera import Camera

    with Camera(config.camera.source, config.camera.width, config.camera.height) as camera:
        frame = camera.read()
    if frame is None:
        hint = "check camera.source; on macOS allow camera access for your terminal in System Settings"
        add(Check("FAIL", "camera", hint))
    else:
        add(Check("OK", "camera", f"{frame.shape[1]}x{frame.shape[0]} from source {config.camera.source!r}"))

    faces = storage.faces()
    if config.owner in faces:
        add(Check("OK", "owner face", f"{len(faces[config.owner])} samples for {config.owner!r}"))
    else:
        add(Check("FAIL", "owner face", f"no face enrolled for {config.owner!r}: run `bbeye enroll`"))

    from bbeye.signals.idle import input_idle_reader

    idle = input_idle_reader()() if config.signals.use_input_idle else None
    if not config.signals.use_input_idle:
        add(Check("SKIP", "input activity", "disabled in config"))
    elif idle is None:
        add(Check("WARN", "input activity", "not available on this system; camera only"))
    else:
        add(Check("OK", "input activity", f"last input {idle:.0f} s ago"))

    from bbeye.judges.factory import SECRET_FOR

    key = SECRET_FOR.get(config.judge.kind)
    if key is None:
        add(Check("OK", "judge", "offline rules"))
    elif secrets.get(key):
        add(Check("OK", "judge", f"{config.judge.kind} ({key} set)"))
    else:
        add(Check("WARN", "judge", f"{key} missing: falling back to offline rules"))

    checks.extend(_cortex_checks(config))
    checks.append(_calendar_check(config, secrets))

    from bbeye.notify.factory import pushcut_configured

    if pushcut_configured(secrets):
        add(Check("OK", "pushcut", "configured (try `bbeye notify-test`)"))
    else:
        add(
            Check(
                "WARN",
                "pushcut",
                "PUSHCUT_API_KEY / PUSHCUT_NOTIFICATION_ID not set: alerts stay in the inbox",
            )
        )

    from bbeye.mcp_server import token_error

    problem = token_error(secrets.get("BBEYE_MCP_TOKEN", ""))
    if problem is None:
        add(Check("OK", "mcp http token", "set"))
    else:
        add(Check("WARN", "mcp http token", f"{problem}: only `bbeye mcp` (stdio) works"))
    storage.close()
    return checks


def _cortex_checks(config: Config) -> list[Check]:
    if not config.cortex.enabled:
        return [Check("SKIP", "cortex", "disabled in config")]
    from bbeye.integrations.cortex import SPRINT_KEY, CortexClient, CortexError

    try:
        CortexClient(config.cortex.url).read(SPRINT_KEY)
    except CortexError as error:
        return [Check("FAIL", "cortex", f"{config.cortex.url}: {error}")]
    checks = [Check("OK", "cortex", f"reachable at {config.cortex.url}")]
    if config.cortex.track_work_hours and not config.cortex.project_id:
        checks.append(Check("FAIL", "cortex project", "set cortex.project_id to track work hours"))
    return checks


def _calendar_check(config: Config, secrets: dict[str, str]) -> Check:
    if config.calendar.source == "none":
        return Check("SKIP", "calendar", "disabled in config")
    from bbeye.calendars.base import CalendarUnavailable
    from bbeye.calendars.factory import build_calendar_source
    from bbeye.reactors.calendar_watch import is_desk_event

    try:
        source = build_calendar_source(config, secrets)
        assert source is not None
        now = now_utc()
        events = source.events(now - timedelta(hours=12), now + timedelta(hours=36))
    except CalendarUnavailable as error:
        return Check("FAIL", "calendar", str(error))
    desk = sum(is_desk_event(event, config.calendar) for event in events)
    return Check("OK", "calendar", f"{len(events)} events nearby, {desk} of them need you at the desk")
