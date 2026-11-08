# Architecture

One loop, small parts, one owner per job. The loop samples the camera every `interval_s`, builds an observation, asks a judge what you are doing, smooths that into activity changes, stores everything, and hands events to reactors.

```text
Camera ─► Detector (YOLO) ─┐
          FaceRecognizer ──┼─► build_observation ─► Judge ─► ActivityTracker ─► events
          idle signal ─────┘                                                    │
                                     Storage (SQLite) ◄─────────────────────────┤
                                     Reactors: WorkHoursSync, SprintGuard,      │
                                               CalendarWatch, VisitorWatch ◄────┘
                                     AlertService ─► notifiers (console, Pushcut)
MCP server and CLI read Storage through status.py.
```

## Who owns what

| Module | Owns |
| --- | --- |
| `config.py` | Settings schema, validation, file locations, secrets loading. |
| `observation.py` | Shared data types: `Box`, `Detection`, `Face`, `Person`, `Observation`, `Verdict`. |
| `timeutil.py` | The clock, capped back-off, UTC ISO-8601 formatting and local day boundaries. |
| `camera.py` | Opening any OpenCV source, reconnecting with back-off, returning the newest frame. |
| `vision/models.py` | Model files: pinned URLs, SHA-256 checks, the download cache. |
| `vision/detector.py` | YOLO inference and the list of desk-relevant classes. |
| `vision/faces.py` | Face detection, embeddings and matching against enrolled people. |
| `perception.py` | Assigning faces and phones to people; keeping a name on a person who turned away. |
| `signals/idle.py` | Keyboard and mouse idle time per operating system. |
| `judges/` | Deciding the activity for one observation. `base.py` holds the judge contract and the shared question; `rules.py` is the offline default and the fallback; `factory.py` owns which secret each judge needs. |
| `tracker.py` | Debouncing, back-dating and work-session boundaries. Pure logic, no I/O. |
| `storage.py` | Every table and query. Nothing else writes SQL. |
| `reactors/` | Side effects of events and time: Cortex hours, Cortex sprint, calendar alerts, visits. |
| `alerts.py` | Raising an alert: store it once, then deliver it to every notifier. |
| `integrations/cortex.py` | The Cortex HTTP contract and its error classes. |
| `calendars/` | Calendar sources behind one `CalendarSource` protocol; `factory.py` picks one from the config. |
| `notify/` | Notifiers behind one `Notifier` protocol; `factory.py` decides which are configured. |
| `status.py` | Read models shared by the CLI and MCP. |
| `mcp_server.py` | MCP tools, the HTTP token guard and the token rule. |
| `runner.py` | The loop (`Eye`) and the composition root (`build_eye`, `build_reactors`). |
| `cli.py`, `doctor.py`, `enroll.py`, `service.py` | User commands. |

## Adding things

* **A judge:** implement `judge(observation) -> Verdict` with a `name`, raise `JudgeUnavailable` when it cannot answer, and wire it in `judges/factory.py` behind `ThrottledJudge`.
* **A notifier:** implement `send(title, body)` with a `name` and add it in `notify/factory.py`.
* **A calendar:** implement `events(start, end) -> list[CalendarEvent]`, raise `CalendarUnavailable` on failure, and add it to `calendars/factory.py`.
* **A reactor:** implement `on_events(events)` and `on_tick(tick)`. A reactor that raises is logged and skipped, so it cannot stop the loop. Anything that talks to a service that may be offline should go through the outbox in `storage.py`.

## Rules worth keeping

* Images never reach the database or the network unless a setting explicitly says so.
* Times are UTC in storage and back-dated to when things happened.
* Outside calls have timeouts and bounded retries.
* Tests use fakes for hardware and HTTP, so they run on any machine.
