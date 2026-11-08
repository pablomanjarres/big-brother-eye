import asyncio
import json

import pytest
from helpers import at
from mcp import Client

from bbeye.mcp_server import BearerAuth, build_server


def call(server, name, arguments=None):
    async def go():
        async with Client(server) as client:
            result = await client.call_tool(name, arguments or {})
            assert not result.is_error, result
            if result.structured_content is not None:
                return result.structured_content
            return json.loads(result.content[0].text)

    return asyncio.run(go())


def test_tools(storage):
    storage.set_meta(
        {
            "heartbeat_at": "2026-11-07T15:00:00.000Z",
            "running": "1",
            "activity": "working",
            "activity_since": "2026-11-07T14:00:00.000Z",
            "camera": "ok",
            "owner_present": "1",
            "others": "0",
        }
    )
    storage.start_work_session("s1", at(-3600))
    storage.touch_work_session("s1", at(0))
    storage.record_activity_change(at(-3600), "away", "working")
    alert = storage.add_alert(at(-60), "calendar", "Be at your desk", "Deep work started", "k1")
    server = build_server(storage, clock=lambda: at(30))

    tools = asyncio.run(_tool_names(server))
    assert tools == {"get_status", "get_work_hours", "get_timeline", "list_alerts", "acknowledge_alert"}

    status = call(server, "get_status")
    assert status["activity"] == "working" and status["unacknowledged_alerts"] == 1
    assert call(server, "get_work_hours")["total_minutes"] == 60.0
    assert call(server, "get_timeline")["activity_changes"][0]["current"] == "working"
    alerts = call(server, "list_alerts")
    alerts = alerts["result"] if isinstance(alerts, dict) and "result" in alerts else alerts
    assert alerts[0]["id"] == alert
    assert call(server, "acknowledge_alert", {"alert_id": alert})["acknowledged"] is True
    assert call(server, "get_status")["unacknowledged_alerts"] == 0


async def _tool_names(server):
    async with Client(server) as client:
        return {tool.name for tool in (await client.list_tools()).tools}


def test_bearer_auth():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    guarded = BearerAuth(app, "x" * 32)

    async def request(headers):
        sent = []

        async def send(message):
            sent.append(message)

        await guarded({"type": "http", "headers": headers}, None, send)
        return sent[0]["status"]

    assert asyncio.run(request([])) == 401
    assert asyncio.run(request([(b"authorization", b"Bearer wrong")])) == 401
    assert asyncio.run(request([(b"authorization", ("Bearer " + "x" * 32).encode())])) == 200
    with pytest.raises(ValueError):
        BearerAuth(app, "short")


def test_token_in_query_string():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    guarded = BearerAuth(app, "y" * 30)

    async def request(query):
        sent = []

        async def send(message):
            sent.append(message)

        await guarded({"type": "http", "headers": [], "query_string": query}, None, send)
        return sent[0]["status"]

    assert asyncio.run(request(b"token=" + b"y" * 30)) == 200
    assert asyncio.run(request(b"a=1&token=nope")) == 401
