"""Pushcut (https://www.pushcut.io) push notifications to your phone."""

from __future__ import annotations

from urllib.parse import quote

import httpx


class PushcutNotifier:
    name = "pushcut"

    def __init__(self, api_key: str, notification_id: str, client: httpx.Client | None = None) -> None:
        self.url = f"https://api.pushcut.io/v1/notifications/{quote(notification_id, safe='')}"
        self._headers = {"API-Key": api_key, "Content-Type": "application/json"}
        self._client = client or httpx.Client(timeout=15)

    def send(self, title: str, body: str) -> None:
        response = self._client.post(self.url, headers=self._headers, json={"title": title, "text": body})
        if response.status_code >= 300:
            raise RuntimeError(f"Pushcut HTTP {response.status_code}")
