# Big Brother Eye

Big Brother Eye turns any camera into a desk monitor that knows when you are working. It runs on your own computer (Windows, macOS or Linux), recognizes your face, notices when someone else is in the room, and keeps everything in a local database.

It can also log your work hours in [Cortex](#cortex), pause your deep-work sprint when you leave the desk, warn you through [Pushcut](#pushcut) when your calendar says you should be at the desk and you are not, and answer questions from AI agents (ChatGPT Dots, Claude, others) over [MCP](#agents-mcp).

```text
camera ─► YOLO (people, laptop, phone…) ─► face recognition ─► judge (rules, Jev or OpenRouter)
       ─► activity tracker ─► SQLite ─► Cortex · calendar alerts · visitor alerts · MCP
```

## What it tracks

| Signal | How |
| --- | --- |
| Are you at the desk? | YOLO finds people; OpenCV face recognition (YuNet + SFace) says which one is you. Your name stays on you for a while when you turn away. |
| Are you working? | Keyboard and mouse activity on the computer running the Eye, the objects around you (laptop, keyboard, book), and a phone in your hand. Optionally a cheap model makes the call. |
| Is someone else here? | Anyone whose face the camera sees and who is not you. Shapes that never show a face (a coat, a door frame) do not count. Each visit is recorded with a start, an end and a head count. |
| How long did you work? | Work sessions with real start and end times, back-dated to when things actually changed. |

Activities are `working`, `phone`, `break` and `away`. Short blips do not count: a new activity has to last `confirm_s` (30 s) and absence has to last `away_after_s` (2 min). Time the camera could not see (laptop asleep, camera unplugged or busy in a video call) is never counted as work.

## Privacy

This is a camera pointed at a room, so the defaults are strict.

* **No images are saved.** The database stores events and counts. Enrollment stores face embeddings (128 numbers per sample), never photos.
* **Unknown faces are never stored.** They are compared with your enrolled face and forgotten.
* **Frames stay on your machine.** The default judge is offline. Jev receives only a text description. OpenRouter receives a frame only if you set `judge.send_images = true`.
* **No usage analytics.** Ultralytics runs in offline mode, and every model file is downloaded from a pinned URL and checked against a SHA-256 hash.
* **Secrets live outside the config**, in environment variables or a private `secrets.env`.

Recording other people can be regulated where you live. Tell the people who share the room, and check your local rules before you point a camera at a shared space.

## Install

You need Python 3.11 or newer, [uv](https://docs.astral.sh/uv/), and a camera OpenCV can open: a webcam index (`0`), an RTSP or HTTP stream from an IP camera or phone app, or a video file.

```bash
git clone https://github.com/pablomanjarres/big-brother-eye.git
cd big-brother-eye
uv sync
```

Then, from that folder:

```bash
uv run bbeye init        # writes config.toml and secrets.env, prints where
uv run bbeye doctor      # checks camera, models, keys and integrations
uv run bbeye enroll      # look at the camera for 20 s and turn your head slowly
uv run bbeye run --show  # start watching, with a preview window
```

The first run downloads three model files (about 45 MB) into the app data folder.

To start the Eye at every login:

```bash
uv run bbeye service install
```

That creates a Windows scheduled task, a macOS LaunchAgent or a systemd user service. Undo it with `bbeye service uninstall`.

**macOS camera permission:** macOS asks for camera access per app. Run `bbeye enroll` once from Terminal and allow access. If `doctor` says the camera failed, open System Settings, then Privacy & Security, then Camera, and allow your terminal (and Python, for the login service).

## Commands

| Command | What it does |
| --- | --- |
| `bbeye init` | Write an example config and a private secrets file. |
| `bbeye doctor` | Check every part and say how to fix what is wrong. |
| `bbeye enroll [--name NAME]` | Learn a face. Enroll housemates under their own names so they show up by name. |
| `bbeye run [--show]` | Watch. `--show` opens a preview with boxes and the current verdict. |
| `bbeye status` | What the Eye sees right now, as JSON. |
| `bbeye hours [--day YYYY-MM-DD]` | Work sessions for a day. |
| `bbeye alerts [--all]` | Recent alerts. |
| `bbeye mcp [--http]` | Serve the MCP tools to agents. |
| `bbeye service install` | Start automatically at login. |
| `bbeye notify-test` | Send a test alert through every notifier. |

## Configuration

`bbeye init` writes [`config.example.toml`](config.example.toml) to your config folder. Every setting is optional. The ones you will most likely change:

```toml
owner = "alex"           # the name you enroll with

[camera]
source = 0               # or "rtsp://user:pass@192.168.1.20/stream"

[judge]
kind = "jev"             # rules | jev | openrouter
```

Secrets go in `secrets.env` next to the config (or in the environment):

```bash
TYPESAFE_API_KEY=...         # Jev
OPENROUTER_API_KEY=...       # OpenRouter
PUSHCUT_API_KEY=...
PUSHCUT_NOTIFICATION_ID=...
BBEYE_ICS_URL=...            # private iCal link, if you use an .ics calendar
BBEYE_MCP_TOKEN=...          # 24+ random characters, for MCP over HTTP
```

## How it decides

* **`rules`** (default, offline): you are working if the keyboard or mouse was used in the last two minutes, or you are reading. A phone in your hand means `phone`. Present but idle means `break`.
* **`jev`**: [Jev](https://typesafe.ai) by TypeSafe AI answers typed questions with probabilities instead of free text. The Eye sends a short text description of the scene (no image) at most every 10 s, and only while you are in view.
* **`openrouter`**: any model on [OpenRouter](https://openrouter.ai), asked for a JSON verdict at most once a minute. The default model is `google/gemini-2.5-flash-lite`. Set `send_images = true` only if you want it to see the frame.

Model judges fall back to the rules whenever they are slow, rate limited or missing a key. Absence is always decided locally.

## Cortex

[Cortex](https://github.com/pablomanjarres/cortex) is a personal dashboard with a work-hours ledger and a deep-work sprint timer. Turn it on with:

```toml
[cortex]
enabled = true
url = "http://127.0.0.1:3456"   # or the Mac's Tailscale name when the Eye runs on another machine
project_id = "..."              # the Cortex project for automatic sessions
```

* **Work hours.** When a work session ends, the Eye records it in Cortex with its real start and end time. Cortex runs one timer at a time, so the Eye never holds it: it writes finished sessions only, fills time that no other timer tracked, and waits while another timer is running. Sessions arrive unbilled and marked for review. If Cortex is closed or the laptop is asleep, the commands wait in a local outbox and are sent later.
* **Deep-work sprint.** After `pause_after_min` (5 min) away, the Eye pauses a running sprint as of the moment you left, so away time never counts. When you are back and working, it resumes the sprint, but only if it paused it and nobody changed it since.
* **Calendar.** Set `calendar.source = "cortex"` to use the calendars Cortex reads.

Cortex only accepts connections from the same machine or from Tailscale addresses.

## Calendar alerts

Pick a source: `cortex`, or `ics` with `BBEYE_ICS_URL` set to any private iCal link (Google Calendar, Outlook, iCloud, Fastmail). An event needs you at the desk when its title or notes contain one of `desk_keywords` (default `desk`, `deep work`, `focus`) or it belongs to one of `desk_calendars`. All-day events are ignored.

If such an event is running and you have been away for `late_after_min` (10 min) of it, the Eye raises one alert for that event.

## Pushcut

Create a notification in the [Pushcut](https://www.pushcut.io) app, then set `PUSHCUT_API_KEY` and `PUSHCUT_NOTIFICATION_ID`. Every alert is pushed to your phone. Run `bbeye notify-test` to check.

## Agents (MCP)

`bbeye mcp` serves these tools over the [Model Context Protocol](https://modelcontextprotocol.io):

| Tool | Returns |
| --- | --- |
| `get_status` | Current activity and since when, whether you and others are in view, today's work minutes, unread alerts. |
| `get_work_hours(day)` | Sessions and total minutes for a day. |
| `get_timeline(start, end)` | Activity changes and visits in a range (up to 31 days). |
| `list_alerts(include_acknowledged, limit)` | The alert inbox, newest first. |
| `acknowledge_alert(alert_id)` | Mark an alert as handled. |

No tool returns images.

**Claude Desktop or Claude Code** (stdio), in the MCP config:

```json
{
  "mcpServers": {
    "big-brother-eye": { "command": "uv", "args": ["--directory", "/path/to/big-brother-eye", "run", "bbeye", "mcp"] }
  }
}
```

**Remote agents** (HTTP): set `BBEYE_MCP_TOKEN`, then run `bbeye mcp --http`, or set `mcp.serve_http = true` so `bbeye run` serves it too. It listens on `127.0.0.1:8765/mcp` and every request needs the token. [docs/dots.md](docs/dots.md) shows how to reach it from a ChatGPT Dot.

## Data

Everything lives in one SQLite file in the app data folder (`bbeye doctor` prints the path). Per-sample observations are kept for `observation_retention_days` (30); activity changes, work sessions, visits and alerts are kept until you delete them. Logs are in the `logs` folder next to it.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The tests use fakes for the camera, models and every HTTP service, so they run anywhere without hardware or keys. [docs/architecture.md](docs/architecture.md) explains which module owns what.

## License

Big Brother Eye is licensed under the [GNU AGPL v3.0 or later](LICENSE), because it uses [Ultralytics YOLO](https://github.com/ultralytics/ultralytics), which is AGPL-3.0. The downloaded models have their own licenses: YOLO11 weights (AGPL-3.0, Ultralytics), YuNet (MIT) and SFace (Apache-2.0) from the [OpenCV Zoo](https://github.com/opencv/opencv_zoo).
