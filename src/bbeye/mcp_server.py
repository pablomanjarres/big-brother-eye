"""MCP server: lets agents (ChatGPT Dots, Claude, others) read what the Eye knows.

It only reads the database (plus acknowledging alerts). It never exposes camera frames.
Run over stdio for local agents, or over HTTP with a bearer token for remote ones.
"""

from __future__ import annotations

import hmac
import logging
import threading
from datetime import timedelta
from typing import Any
from urllib.parse import unquote_to_bytes

from mcp.server.mcpserver import MCPServer

from bbeye import status
from bbeye.storage import Storage
from bbeye.timeutil import now_utc

INSTRUCTIONS = """\
Big Brother Eye watches the owner's desk camera and knows whether the owner is working, on the phone,
on a break or away, whether other people are in the room, and how long the owner worked today.
Alerts (for example "a calendar event says you should be at your desk") wait in an inbox:
call list_alerts, tell the owner about each new alert, then call acknowledge_alert with its id.
If eye_online is false the camera loop is not running and the activity is unknown.
"""


def build_server(storage: Storage, clock=now_utc) -> MCPServer:
    server = MCPServer(name="big-brother-eye", title="Big Brother Eye", instructions=INSTRUCTIONS)

    @server.tool()
    def get_status() -> dict[str, Any]:
        """What the owner is doing right now, since when, who else is in the room, and today's work time."""
        return status.eye_status(storage, clock())

    @server.tool()
    def get_work_hours(day: str = "") -> dict[str, Any]:
        """Automatically tracked work sessions for a local day (YYYY-MM-DD). Empty means today."""
        now = clock()
        return status.work_summary(storage, status.parse_moment(day, now), now)

    @server.tool()
    def get_timeline(start: str = "", end: str = "") -> dict[str, Any]:
        """Activity changes and visits between two ISO dates/datetimes. Defaults to the last 24 hours."""
        now = clock()
        begin = status.parse_moment(start, now - timedelta(hours=24))
        finish = status.parse_moment(end, now)
        if finish - begin > timedelta(days=31):
            raise ValueError("Ask for at most 31 days at a time.")
        return status.timeline(storage, begin, finish)

    @server.tool()
    def list_alerts(include_acknowledged: bool = False, limit: int = 20) -> list[dict[str, Any]]:
        """Alerts for the owner, newest first. By default only the ones nobody has acknowledged yet."""
        return status.alert_list(storage, include_acknowledged, max(1, min(limit, 100)))

    @server.tool()
    def acknowledge_alert(alert_id: int) -> dict[str, Any]:
        """Mark an alert as handled after you told the owner about it."""
        return {"alert_id": alert_id, "acknowledged": storage.acknowledge_alert(alert_id, clock())}

    return server


MIN_TOKEN_LENGTH = 24


def token_error(token: str) -> str | None:
    """Why a BBEYE_MCP_TOKEN cannot be used, or None if it is fine."""
    if len(token) < MIN_TOKEN_LENGTH:
        return f"BBEYE_MCP_TOKEN must be set to at least {MIN_TOKEN_LENGTH} random characters"
    return None


class BearerAuth:
    """Minimal ASGI guard. Every HTTP request must carry the token.

    Preferred: ``Authorization: Bearer <token>``. Clients that cannot set headers (ChatGPT's
    connector form accepts only OAuth or no auth) may put it in the URL as ``?token=<token>``.
    """

    def __init__(self, app, token: str) -> None:
        problem = token_error(token)
        if problem:
            raise ValueError(problem)
        self.app, self._token = app, token.encode()

    def _supplied(self, scope) -> bytes:
        header = dict(scope.get("headers") or []).get(b"authorization", b"")
        if header.startswith(b"Bearer "):
            return header[7:]
        for pair in (scope.get("query_string") or b"").split(b"&"):
            name, _, value = pair.partition(b"=")
            if name == b"token":
                return unquote_to_bytes(value)
        return b""

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and not hmac.compare_digest(self._supplied(scope), self._token):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")],
                }
            )
            await send({"type": "http.response.body", "body": b"Unauthorized"})
            return
        await self.app(scope, receive, send)


def http_app(server: MCPServer, token: str, host: str, allowed_hosts: tuple[str, ...]):
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", *allowed_hosts, *(f"{h}:*" for h in allowed_hosts)]
    origins = ["http://127.0.0.1:*", "http://localhost:*", *(f"https://{h}" for h in allowed_hosts)]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
    )
    app = server.streamable_http_app(
        host=host, transport_security=security, stateless_http=True, json_response=True
    )
    return BearerAuth(app, token)


def serve_http_in_background(
    storage: Storage, token: str, host: str, port: int, allowed_hosts: tuple[str, ...]
):
    """Run the HTTP MCP server on a daemon thread next to the capture loop."""
    import uvicorn

    app = http_app(build_server(storage), token, host, allowed_hosts)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="bbeye-mcp-http", daemon=True)
    thread.start()
    logging.getLogger(__name__).info("MCP over HTTP at http://%s:%d/mcp", host, port)
    return server
