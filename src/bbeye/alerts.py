"""One place that raises alerts: stores them (the agents' inbox) and pushes them to notifiers."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

from bbeye.storage import Storage

log = logging.getLogger(__name__)


class Notifier(Protocol):
    name: str

    def send(self, title: str, body: str) -> None: ...


class AlertService:
    def __init__(self, storage: Storage, notifiers: list[Notifier]) -> None:
        self.storage = storage
        self.notifiers = notifiers

    def raise_alert(self, now: datetime, kind: str, title: str, body: str, dedupe_key: str) -> bool:
        """Store and deliver an alert. Returns False if an alert with this key already exists."""
        alert_id = self.storage.add_alert(now, kind, title, body, dedupe_key)
        if alert_id is None:
            return False
        delivery: dict[str, str] = {}
        for notifier in self.notifiers:
            try:
                notifier.send(title, body)
                delivery[notifier.name] = "sent"
            except Exception as error:  # a broken notifier must never stop the Eye
                log.warning("Notifier %s failed: %s", notifier.name, error)
                delivery[notifier.name] = f"failed: {error}"[:200]
        self.storage.set_alert_delivery(alert_id, delivery)
        return True
