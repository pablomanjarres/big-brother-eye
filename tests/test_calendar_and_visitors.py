from datetime import timedelta
from zoneinfo import ZoneInfo

import httpx
from helpers import T0, at

from bbeye.alerts import AlertService
from bbeye.calendars.base import CalendarEvent, CalendarUnavailable
from bbeye.calendars.cortex import CortexCalendar
from bbeye.calendars.ics import IcsCalendar
from bbeye.config import CalendarConfig, VisitorsConfig
from bbeye.integrations.cortex import CortexClient
from bbeye.observation import Box, Observation, Person
from bbeye.reactors.base import Tick
from bbeye.reactors.calendar_watch import CalendarWatch, is_desk_event
from bbeye.reactors.visitors import VisitorWatch
from bbeye.tracker import TrackerSnapshot


class Recorder:
    name = "recorder"

    def __init__(self):
        self.sent = []

    def send(self, title, body):
        self.sent.append((title, body))


class FixedCalendar:
    def __init__(self, events, fail=False):
        self._events, self.fail, self.calls = events, fail, 0

    def events(self, start, end):
        self.calls += 1
        if self.fail:
            raise CalendarUnavailable("offline")
        return self._events


def event(title, start, minutes=60, **kw):
    return CalendarEvent(
        kw.get("id", title),
        title,
        start,
        start + timedelta(minutes=minutes),
        kw.get("calendar", "Work"),
        kw.get("all_day", False),
        kw.get("notes", ""),
    )


def snap(activity, since):
    return TrackerSnapshot(activity, since, None, None, None)


def test_desk_event_matching():
    config = CalendarConfig(desk_keywords=("desk", "deep work"), desk_calendars=("Focus",))
    assert is_desk_event(event("Deep Work block", T0), config)
    assert is_desk_event(event("Anything", T0, calendar="focus"), config)
    assert is_desk_event(event("Call", T0, notes="be at your desk"), config)
    assert not is_desk_event(event("Lunch", T0), config)
    assert not is_desk_event(event("Desk day", T0, all_day=True), config)


def test_late_for_desk_event_alerts_once(storage):
    recorder = Recorder()
    alerts = AlertService(storage, [recorder])
    watch = CalendarWatch(
        FixedCalendar([event("Deep work", at(0))]), alerts, CalendarConfig(late_after_min=10)
    )
    watch.on_tick(Tick(at(5 * 60), snap("away", at(-3600)), None))
    assert recorder.sent == []
    watch.on_tick(Tick(at(10 * 60), snap("away", at(-3600)), None))
    watch.on_tick(Tick(at(11 * 60), snap("away", at(-3600)), None))
    assert len(recorder.sent) == 1 and "Deep work" in recorder.sent[0][0]
    assert storage.alerts()[0].kind == "calendar"


def test_no_alert_when_at_desk_or_recently_left(storage):
    recorder = Recorder()
    watch = CalendarWatch(
        FixedCalendar([event("Deep work", at(0))]), AlertService(storage, [recorder]), CalendarConfig()
    )
    watch.on_tick(Tick(at(20 * 60), snap("working", at(0)), None))
    watch.on_tick(Tick(at(20 * 60), snap("away", at(15 * 60)), None))  # left 5 min ago
    assert recorder.sent == []


def test_calendar_outage_keeps_cache_and_refresh_pace(storage):
    source = FixedCalendar([event("Deep work", at(0))])
    watch = CalendarWatch(source, AlertService(storage, []), CalendarConfig(refresh_min=5))
    watch.on_tick(Tick(at(0), snap("working", at(0)), None))
    source.fail = True
    watch.on_tick(Tick(at(6 * 60), snap("working", at(0)), None))
    watch.on_tick(Tick(at(7 * 60), snap("working", at(0)), None))
    assert source.calls == 2 and len(watch._events) == 1


def test_cortex_calendar_parses_events():
    def handler(request):
        assert request.url.path == "/api/calendar/events"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "e1",
                    "title": "Deep work",
                    "startDate": "2026-11-07T15:00:00Z",
                    "endDate": "2026-11-07T16:00:00Z",
                    "calendar": "Work",
                    "isAllDay": False,
                    "notes": "",
                },
                {"id": "bad"},
            ],
        )

    client = CortexClient("http://c", client=httpx.Client(transport=httpx.MockTransport(handler)))
    [found] = CortexCalendar(client).events(at(-3600), at(3600))
    assert found.title == "Deep work" and found.start == T0


def test_cortex_calendar_asks_for_days_in_the_cortex_time_zone():
    asked = []

    def handler(request):
        asked.append((request.url.params["start"], request.url.params["end"]))
        return httpx.Response(200, json=[])

    client = CortexClient("http://c", client=httpx.Client(transport=httpx.MockTransport(handler)))
    # T0 is 15:00 UTC on Nov 7, already 05:00 on Nov 8 in UTC+14.
    CortexCalendar(client, zone=ZoneInfo("Pacific/Kiritimati")).events(at(0), at(3600))
    assert asked == [("2026-11-08", "2026-11-09")]


ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
X-WR-CALNAME:Personal
BEGIN:VEVENT
UID:weekly-focus
DTSTART:20261031T150000Z
DTEND:20261031T170000Z
RRULE:FREQ=DAILY
SUMMARY:Focus at desk
END:VEVENT
BEGIN:VEVENT
UID:holiday
DTSTART;VALUE=DATE:20261107
DTEND;VALUE=DATE:20261108
SUMMARY:Holiday
END:VEVENT
END:VCALENDAR
"""


def test_ics_expands_recurrence():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=ICS)))
    events = IcsCalendar("webcal://example.test/cal.ics", client=client).events(at(-60), at(60))
    titles = {e.title: e for e in events}
    assert titles["Focus at desk"].start == T0 and titles["Focus at desk"].calendar == "Personal"
    assert titles["Holiday"].all_day


def visitor_obs(t, others=1, owner=False):
    people = tuple(Person(Box(0, 0, 10, 10), None, True, False) for _ in range(others))
    if owner:
        people += (Person(Box(20, 0, 30, 10), "me", True, False),)
    return Observation(at(t), "me", people, (), None)


def run_visitors(watch, start, end, **kw):
    for t in range(start, end + 1, 2):
        watch.on_tick(Tick(at(t), snap("away", at(0)), visitor_obs(t, **kw)))


def test_visit_recorded_and_alerted_when_away(storage):
    recorder = Recorder()
    watch = VisitorWatch(
        storage, AlertService(storage, [recorder]), VisitorsConfig(min_seen_s=10, gone_after_s=60)
    )
    run_visitors(watch, 0, 30)
    run_visitors(watch, 32, 120, others=0)
    [visit] = storage.visits(at(-1), at(1000))
    assert (
        visit["started_at"] == "2026-11-07T15:00:00.000Z" and visit["ended_at"] == "2026-11-07T15:00:30.000Z"
    )
    assert len(recorder.sent) == 1 and "away" in recorder.sent[0][1]


def test_no_visitor_alert_when_owner_is_there(storage):
    recorder = Recorder()
    watch = VisitorWatch(storage, AlertService(storage, [recorder]), VisitorsConfig())
    run_visitors(watch, 0, 30, owner=True)
    assert len(storage.visits(at(-1), at(100))) == 1 and recorder.sent == []


def test_visitor_alert_cooldown(storage):
    recorder = Recorder()
    watch = VisitorWatch(storage, AlertService(storage, [recorder]), VisitorsConfig(cooldown_min=30))
    run_visitors(watch, 0, 20)
    run_visitors(watch, 22, 100, others=0)
    run_visitors(watch, 102, 130)
    assert len(storage.visits(at(-1), at(1000))) == 2 and len(recorder.sent) == 1


def test_failing_notifier_does_not_break_alerts(storage):
    class Broken:
        name = "broken"

        def send(self, title, body):
            raise RuntimeError("no network")

    service = AlertService(storage, [Broken()])
    assert service.raise_alert(at(0), "test", "t", "b", "k")
    assert not service.raise_alert(at(1), "test", "t", "b", "k")
