"""Configuration: one TOML file for settings, environment variables for secrets.

Secrets never live in the TOML file. They come from the process environment or
from a ``secrets.env`` file next to the config (useful when the Eye runs as a
background task that does not inherit your shell environment).
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from platformdirs import user_config_dir, user_data_dir

APP_NAME = "bbeye"

JudgeKind = Literal["rules", "jev", "openrouter"]
CalendarKind = Literal["none", "cortex", "ics"]
VisitorAlert = Literal["never", "when_away", "always"]


class ConfigError(ValueError):
    """The configuration file is invalid."""


@dataclass(frozen=True)
class CameraConfig:
    source: str | int = 0
    interval_s: float = 2.0
    width: int = 1280
    height: int = 720


@dataclass(frozen=True)
class DetectorConfig:
    model: str = "yolo11n.pt"
    confidence: float = 0.4
    device: str = ""  # "" lets Ultralytics choose (CPU, CUDA or Apple MPS)


@dataclass(frozen=True)
class FacesConfig:
    match_threshold: float = 0.363  # SFace cosine threshold recommended by OpenCV
    identity_hold_s: float = 45.0  # keep a name on a person whose face turned away


@dataclass(frozen=True)
class SignalsConfig:
    use_input_idle: bool = True  # keyboard/mouse activity of the machine running the Eye
    input_active_s: float = 120.0


@dataclass(frozen=True)
class JudgeConfig:
    kind: JudgeKind = "rules"
    jev_model: str = "jev-latest"
    jev_min_interval_s: float = 10.0
    openrouter_model: str = "google/gemini-2.5-flash-lite"
    openrouter_min_interval_s: float = 60.0
    send_images: bool = False  # never send camera frames to the cloud unless this is true


@dataclass(frozen=True)
class TrackerConfig:
    confirm_s: float = 30.0  # a new activity must last this long before it counts
    away_after_s: float = 120.0  # absence must last this long before you count as away
    work_gap_s: float = 300.0  # non-working time longer than this ends a work session
    max_gap_s: float = 120.0  # no frames for longer than this (sleep, camera lost) ends what was seen


@dataclass(frozen=True)
class StorageConfig:
    path: str = ""  # "" = <data dir>/bbeye.sqlite3
    observation_retention_days: int = 30


@dataclass(frozen=True)
class CortexConfig:
    enabled: bool = False
    url: str = "http://127.0.0.1:3456"
    project_id: str = ""
    track_work_hours: bool = True
    pause_sprint: bool = True
    pause_after_min: float = 5.0
    resume_sprint: bool = True


@dataclass(frozen=True)
class CalendarConfig:
    source: CalendarKind = "none"
    desk_keywords: tuple[str, ...] = ("desk", "deep work", "focus")
    desk_calendars: tuple[str, ...] = ()
    late_after_min: float = 10.0
    refresh_min: float = 5.0


@dataclass(frozen=True)
class VisitorsConfig:
    alert: VisitorAlert = "when_away"
    min_seen_s: float = 10.0
    gone_after_s: float = 60.0
    cooldown_min: float = 30.0


@dataclass(frozen=True)
class McpConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    allowed_hosts: tuple[str, ...] = ()  # public hostnames that reach this server, e.g. through a tunnel
    serve_http: bool = False  # `bbeye run` also serves MCP over HTTP (needs BBEYE_MCP_TOKEN)


@dataclass(frozen=True)
class Config:
    owner: str = "me"
    camera: CameraConfig = field(default_factory=CameraConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    faces: FacesConfig = field(default_factory=FacesConfig)
    signals: SignalsConfig = field(default_factory=SignalsConfig)
    judge: JudgeConfig = field(default_factory=JudgeConfig)
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    cortex: CortexConfig = field(default_factory=CortexConfig)
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    visitors: VisitorsConfig = field(default_factory=VisitorsConfig)
    mcp: McpConfig = field(default_factory=McpConfig)


def config_dir() -> Path:
    return Path(os.environ.get("BBEYE_CONFIG_DIR") or user_config_dir(APP_NAME, appauthor=False))


def data_dir() -> Path:
    return Path(os.environ.get("BBEYE_DATA_DIR") or user_data_dir(APP_NAME, appauthor=False))


def config_path() -> Path:
    return config_dir() / "config.toml"


def database_path(config: Config) -> Path:
    return Path(config.storage.path).expanduser() if config.storage.path else data_dir() / "bbeye.sqlite3"


def load_config(path: Path | None = None) -> Config:
    """Read the TOML config. A missing file means all defaults."""
    path = path or config_path()
    if not path.exists():
        return Config()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: {error}") from error
    return _build(Config, raw, "")


def _build(cls: type, data: dict[str, Any], where: str) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"[{where}] must be a table")
    fields = {f.name: f for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise ConfigError(f"Unknown setting(s) in [{where or 'top level'}]: {', '.join(unknown)}")
    values: dict[str, Any] = {}
    defaults = cls()
    for name, value in data.items():
        default = getattr(defaults, name)
        key = f"{where}.{name}" if where else name
        if dataclasses.is_dataclass(default):
            values[name] = _build(type(default), value, key)
        else:
            values[name] = _coerce(value, default, key)
    return cls(**values)


def _coerce(value: Any, default: Any, key: str) -> Any:
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"{key} must be true or false")
        return value
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ConfigError(f"{key} must be a number")
        if value < 0:
            raise ConfigError(f"{key} must not be negative")
        return float(value)
    if isinstance(default, tuple):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigError(f"{key} must be a list of strings")
        return tuple(value)
    if key == "camera.source":
        if isinstance(value, bool) or not isinstance(value, int | str):
            raise ConfigError("camera.source must be a camera index (0, 1, ...) or a URL/path")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(f"{key} must be a whole number")
        return value
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be text")
    choices = _CHOICES.get(key)
    if choices and value not in choices:
        raise ConfigError(f"{key} must be one of: {', '.join(choices)}")
    return value


_CHOICES = {
    "judge.kind": ("rules", "jev", "openrouter"),
    "calendar.source": ("none", "cortex", "ics"),
    "visitors.alert": ("never", "when_away", "always"),
}


def load_secrets(path: Path | None = None) -> dict[str, str]:
    """Environment variables, with ``secrets.env`` filling only what the environment lacks."""
    path = path or config_dir() / "secrets.env"
    values: dict[str, str] = {}
    if path.exists():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, sep, value = line.partition("=")
            if not sep or not name.strip():
                raise ConfigError(f"{path}:{number}: expected NAME=value")
            values[name.strip()] = value.strip().strip('"').strip("'")
    values.update({key: value for key, value in os.environ.items() if value})
    return values


EXAMPLE_CONFIG = """\
# Big Brother Eye configuration. Every setting is optional; these are the defaults.
# Secrets (API keys, tokens, private calendar URLs) go in secrets.env next to this file
# or in environment variables, never here.

owner = "me"                     # the name you enroll with `bbeye enroll`

[camera]
source = 0                       # webcam index, or a URL such as "rtsp://user:pass@192.168.1.20/stream"
interval_s = 2.0                 # seconds between samples
width = 1280
height = 720

[detector]
model = "yolo11n.pt"             # yolo11n.pt or yolo11s.pt (downloaded and verified), or a local path
confidence = 0.4
device = ""                      # "" = automatic, or "cpu", "cuda:0", "mps"

[faces]
match_threshold = 0.363
identity_hold_s = 45.0

[signals]
use_input_idle = true            # use keyboard/mouse activity of this computer as a work signal
input_active_s = 120.0

[judge]
# rules = offline | jev = needs TYPESAFE_API_KEY | openrouter = needs OPENROUTER_API_KEY
kind = "rules"
jev_model = "jev-latest"
jev_min_interval_s = 10.0
openrouter_model = "google/gemini-2.5-flash-lite"
openrouter_min_interval_s = 60.0
send_images = false              # true sends camera frames to OpenRouter. Off by default.

[tracker]
confirm_s = 30.0
away_after_s = 120.0
work_gap_s = 300.0
max_gap_s = 120.0                # no frames for this long (sleep, camera busy) and unseen time is not work

[storage]
path = ""                        # "" = the app data folder
observation_retention_days = 30

[cortex]
enabled = false
url = "http://127.0.0.1:3456"    # e.g. http://my-mac.tailnet-name.ts.net:3456 over Tailscale
project_id = ""                  # Cortex project that automatic work sessions belong to
track_work_hours = true
pause_sprint = true
pause_after_min = 5.0
resume_sprint = true

[calendar]
source = "none"                  # none | cortex | ics (set BBEYE_ICS_URL in secrets.env)
desk_keywords = ["desk", "deep work", "focus"]
desk_calendars = []
late_after_min = 10.0
refresh_min = 5.0

[visitors]
alert = "when_away"              # never | when_away | always
min_seen_s = 10.0
gone_after_s = 60.0
cooldown_min = 30.0

[mcp]
host = "127.0.0.1"
port = 8765
allowed_hosts = []               # e.g. ["my-laptop.tailnet.ts.net"] when exposed through Tailscale Funnel
serve_http = false               # true: `bbeye run` also serves MCP over HTTP (needs BBEYE_MCP_TOKEN)
"""

EXAMPLE_SECRETS = """\
# Big Brother Eye secrets. Keep this file private. Lines are NAME=value.
# TYPESAFE_API_KEY=
# OPENROUTER_API_KEY=
# PUSHCUT_API_KEY=
# PUSHCUT_NOTIFICATION_ID=
# BBEYE_ICS_URL=
# BBEYE_MCP_TOKEN=
"""
