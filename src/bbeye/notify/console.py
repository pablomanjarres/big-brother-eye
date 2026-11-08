from __future__ import annotations

import logging

log = logging.getLogger("bbeye.alert")


class ConsoleNotifier:
    name = "console"

    def send(self, title: str, body: str) -> None:
        log.warning("ALERT %s: %s", title, body)
