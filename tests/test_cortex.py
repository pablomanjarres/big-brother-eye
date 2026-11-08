import json
from datetime import timedelta

import httpx
import pytest
from helpers import at

from bbeye.integrations.cortex import SPRINT_KEY, CortexClient, CortexConflict, CortexError
from bbeye.reactors.base import Tick
from bbeye.reactors.sprint import SprintGuard
from bbeye.reactors.work_hours import WorkHoursSync, free_parts
from bbeye.tracker import ActivityChanged, TrackerSnapshot, WorkEnded, WorkStarted

BASE = "http://cortex.test:3456"


class FakeCortex:
    """Implements the slice of Cortex's HTTP API the Eye uses, with the ledger's rules
    (one active timer, no overlapping sessions, idempotent owned commands)."""

    def __init__(self):
        self.commands = []
        self.data = {"cortex-project-time": {"projects": [], "active": None, "sessions": []}}
        self.rev = 0
        self.down = False
        self.reject_with = None
        self.conflicts = 0

    def handler(self, request: httpx.Request):
        if self.down:
            raise httpx.ConnectError("down")
        path = request.url.path
        if path == "/api/work-hours/command":
            if self.reject_with:
                return httpx.Response(400, json={"ok": False, "error": self.reject_with})
            command = json.loads(request.content)
            error = self.apply(command)
            if error:
                return httpx.Response(400, json={"ok": False, "error": error})
            self.commands.append(command)
            return httpx.Response(200, json={"ok": True, "state": {}})
        if path == "/api/data" and request.method == "GET":
            key = request.url.params["key"]
            return httpx.Response(
                200, content=json.dumps(self.data.get(key)).encode(), headers={"X-Cortex-Rev": str(self.rev)}
            )
        if path == "/api/data" and request.method == "POST":
            body = json.loads(request.content)
            if self.conflicts:
                self.conflicts -= 1
                self.rev += 1
                return httpx.Response(409, json={"error": "conflict"})
            if body["baseRev"] != str(self.rev):
                return httpx.Response(409, json={"error": "conflict"})
            self.rev += 1
            self.data[body["key"]] = body["data"]
            return httpx.Response(200, json={"ok": True, "rev": str(self.rev)})
        return httpx.Response(404)

    @property
    def ledger(self):
        return self.data["cortex-project-time"]

    def apply(self, command):
        ledger, kind = self.ledger, command["type"]
        if kind == "start-owned-at":
            if ledger["active"] and ledger["active"]["id"] == command["id"]:
                return None
            if any(row["id"] == command["id"] for row in ledger["sessions"]):
                return None
            if ledger["active"]:
                return "Work is already active; use Switch or Stop"
            start = command["startedAt"]
            if any(row["startedAt"] <= start < row["endedAt"] for row in ledger["sessions"]):
                return "Start time overlaps a saved session"
            ledger["active"] = {"id": command["id"], "projectId": command["projectId"], "startedAt": start}
        elif kind in ("stop-owned-at", "stop-owned"):
            active = ledger["active"]
            if not active or active["id"] != command["id"]:
                return None
            end = command.get("endedAt", "2099-01-01T00:00:00.000Z")
            later = [
                row["startedAt"] for row in ledger["sessions"] if row["startedAt"] >= active["startedAt"]
            ]
            end = min([end, *later])
            ledger["sessions"].append({"id": active["id"], "startedAt": active["startedAt"], "endedAt": end})
            ledger["active"] = None
        return None

    def client(self):
        return CortexClient(BASE, client=httpx.Client(transport=httpx.MockTransport(self.handler)))


def snapshot(activity="working", since=None):
    since = since or at(0)
    return TrackerSnapshot(activity, since, None, None, None)


def test_client_errors_are_classified():
    fake = FakeCortex()
    client = fake.client()
    fake.reject_with = "Recorded start cannot be in the future"
    with pytest.raises(CortexError) as error:
        client.work_hours({"type": "start-owned-at"})
    assert error.value.retryable
    fake.reject_with = "Work is already active; use Switch or Stop"
    with pytest.raises(CortexError) as error:
        client.work_hours({"type": "start-owned-at"})
    assert error.value.retryable
    fake.reject_with = "Start time overlaps a saved session"
    with pytest.raises(CortexError) as error:
        client.work_hours({"type": "start-owned-at"})
    assert not error.value.retryable
    fake.down = True
    with pytest.raises(CortexError) as error:
        client.read(SPRINT_KEY)
    assert error.value.retryable


def test_conflict_is_reported():
    fake = FakeCortex()
    with pytest.raises(CortexConflict):
        fake.client().write(SPRINT_KEY, {}, "stale")


def sync_for(storage, fake):
    return WorkHoursSync(storage, fake.client(), "construcredit", clock=lambda: at(0))


def iso_at(seconds):
    return at(seconds).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def test_finished_session_is_recorded_whole_when_nothing_else_is_tracked(storage):
    fake = FakeCortex()
    sync = sync_for(storage, fake)
    sync.on_events([WorkStarted("bbeye-1", at(0))])
    sync.flush(at(1))
    assert fake.commands == []  # nothing is sent while the session is open
    sync.on_events([WorkEnded("bbeye-1", at(0), at(3600))])
    sync.flush(at(3601))
    assert fake.commands == [
        {
            "type": "start-owned-at",
            "id": "bbeye-1-150000",
            "projectId": "construcredit",
            "startedAt": iso_at(0),
        },
        {"type": "stop-owned-at", "id": "bbeye-1-150000", "endedAt": iso_at(3600)},
    ]
    assert fake.ledger["active"] is None


def test_only_free_time_around_a_manual_session_is_recorded(storage):
    fake = FakeCortex()
    fake.ledger["sessions"].append({"id": "manual", "startedAt": iso_at(7200), "endedAt": iso_at(9000)})
    sync = sync_for(storage, fake)
    sync.on_events([WorkEnded("bbeye-1", at(0), at(10800))])  # 15:00 to 18:00, manual 17:00 to 17:30
    sync.flush(at(10801))
    spans = sorted(
        (row["startedAt"], row["endedAt"]) for row in fake.ledger["sessions"] if row["id"] != "manual"
    )
    assert spans == [(iso_at(0), iso_at(7200)), (iso_at(9000), iso_at(10800))]


def test_waits_while_another_timer_runs(storage):
    fake = FakeCortex()
    fake.ledger["active"] = {"id": "ccw-hook", "projectId": "p", "startedAt": iso_at(1800)}
    sync = sync_for(storage, fake)
    sync.on_events([WorkEnded("bbeye-1", at(0), at(1200))])
    sync.flush(at(1300))
    assert fake.commands == [] and storage.outbox_summary() == {"pending": 1}
    fake.ledger["active"] = None
    fake.ledger["sessions"].append({"id": "ccw-hook", "startedAt": iso_at(1800), "endedAt": iso_at(2400)})
    sync.flush(at(1300) + timedelta(minutes=11))
    assert [c["type"] for c in fake.commands] == ["start-owned-at", "stop-owned-at"]


def test_work_hours_survive_cortex_being_offline(storage):
    fake = FakeCortex()
    sync = sync_for(storage, fake)
    sync.on_events([WorkEnded("bbeye-1", at(0), at(600))])
    fake.down = True
    sync.flush(at(601))
    assert storage.outbox_summary() == {"pending": 1}
    fake.down = False
    sync.flush(at(602))  # still waiting for the retry time
    assert fake.commands == []
    sync.flush(at(601) + timedelta(minutes=11))
    assert storage.outbox_summary() == {"done": 1} and len(fake.ledger["sessions"]) == 1


def test_interrupted_recording_is_finished_without_duplicates(storage):
    fake = FakeCortex()
    # A previous attempt started the first part and then lost the connection.
    fake.ledger["active"] = {"id": "bbeye-1-150000", "projectId": "construcredit", "startedAt": iso_at(0)}
    sync = sync_for(storage, fake)
    sync.on_events([WorkEnded("bbeye-1", at(0), at(600))])
    sync.flush(at(601))
    assert fake.ledger["active"] is None
