"""Client for Cortex's local HTTP API (https://github.com/pablomanjarres/cortex).

Cortex accepts requests only from localhost or Tailscale addresses.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

SPRINT_KEY = "cortex-active-sprint"
NO_CHANGE = object()  # returned by an update's change function to skip the write
WORK_HOURS_KEY = "cortex-project-time"


class CortexError(RuntimeError):
    def __init__(self, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class CortexConflict(CortexError):
    def __init__(self) -> None:
        super().__init__("value changed since it was read", retryable=True)


@dataclass(frozen=True)
class StoredValue:
    data: Any
    rev: str | None


class CortexClient:
    def __init__(self, base_url: str, client: httpx.Client | None = None, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.request(method, self.base_url + path, **kwargs)
        except httpx.HTTPError as error:
            raise CortexError(f"Cortex unreachable ({type(error).__name__})", retryable=True) from error

    def work_hours(self, command: dict[str, Any]) -> None:
        """Send a work-hours command. Validation errors are final; transport errors can be retried."""
        response = self._request("POST", "/api/work-hours/command", json=command)
        if response.status_code == 200:
            return
        try:
            message = str(response.json().get("error", ""))
        except ValueError:
            message = response.text[:200]
        if response.status_code == 400:
            # Worth retrying later: clocks a few seconds apart ("in the future"), or another timer
            # running right now ("already active"). Everything else (overlaps, bad project) is final.
            retryable = "in the future" in message or "already active" in message
            raise CortexError(message or "rejected", retryable=retryable)
        raise CortexError(f"HTTP {response.status_code}: {message}", retryable=response.status_code >= 500)

    def work_hours_state(self) -> dict[str, Any]:
        """Cortex's work-hours ledger: projects, the active timer and saved sessions (read only)."""
        state = self.read(WORK_HOURS_KEY).data
        return state if isinstance(state, dict) else {}

    def read(self, key: str) -> StoredValue:
        response = self._request("GET", "/api/data", params={"key": key})
        if response.status_code != 200:
            raise CortexError(
                f"read {key}: HTTP {response.status_code}", retryable=response.status_code >= 500
            )
        return StoredValue(response.json(), response.headers.get("X-Cortex-Rev"))

    def write(self, key: str, data: Any, base_rev: str | None) -> str | None:
        response = self._request("POST", "/api/data", json={"key": key, "data": data, "baseRev": base_rev})
        if response.status_code == 409:
            raise CortexConflict()
        if response.status_code != 200:
            raise CortexError(
                f"write {key}: HTTP {response.status_code}", retryable=response.status_code >= 500
            )
        return response.json().get("rev")

    def update(self, key: str, change: Callable[[Any], Any]) -> Any:
        """Read a key, apply ``change``, and write it back with Cortex's revision check.

        ``change`` returns the new value, or ``NO_CHANGE`` to leave the key alone. A conflict
        re-reads once. Returns the value written, or ``NO_CHANGE``.
        """
        for attempt in range(2):
            current = self.read(key)
            new = change(current.data)
            if new is NO_CHANGE:
                return NO_CHANGE
            try:
                self.write(key, new, current.rev)
                return new
            except CortexConflict:
                if attempt:
                    raise
        return NO_CHANGE  # unreachable; keeps type checkers calm

    def calendar_events(self, start: date, end: date) -> list[dict[str, Any]]:
        response = self._request(
            "GET", "/api/calendar/events", params={"start": start.isoformat(), "end": end.isoformat()}
        )
        if response.status_code != 200:
            raise CortexError(f"calendar: HTTP {response.status_code}", retryable=True)
        events = response.json()
        if not isinstance(events, list):
            raise CortexError("calendar: unexpected response", retryable=True)
        return events
