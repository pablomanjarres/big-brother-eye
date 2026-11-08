"""`bbeye idle-serve`: tells the Eye on another computer how long this one has been idle.

Answers ``GET /idle`` with ``{"idle_s": <seconds or null>}``, only to this computer and to
Tailscale addresses. It reveals nothing but a number of seconds.
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_TAILSCALE = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))


def allowed_client(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or any(ip in network for network in _TAILSCALE)


def make_server(host: str, port: int, read_idle: Callable[[], float | None]) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            if not allowed_client(self.client_address[0]):
                self._reply(403, {"error": "forbidden"})
            elif self.path.split("?")[0] != "/idle":
                self._reply(404, {"error": "not found"})
            else:
                self._reply(200, {"idle_s": read_idle()})

        def _reply(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: object) -> None:
            pass  # one request every two seconds is not worth a log line

    return ThreadingHTTPServer((host, port), Handler)
