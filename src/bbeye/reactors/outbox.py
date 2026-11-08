"""The one retry loop for outbox items sent to services that may be offline."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from bbeye.integrations.cortex import CortexError
from bbeye.storage import Storage
from bbeye.timeutil import backoff

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 200  # with the 10-minute cap this is more than a day of retrying


def flush_outbox(
    storage: Storage, target: str, now: datetime, send: Callable[[dict[str, Any]], str], limit: int = 10
) -> None:
    """Send due items for ``target`` in order. ``send`` returns a log line or raises CortexError.

    A retryable error keeps the item (and everything after it) for later with back-off; a final
    error marks it rejected so it never blocks the queue.
    """
    for _ in range(limit):
        item = storage.next_outbox_item(target, now)
        if item is None:
            return
        try:
            message = send(item.payload)
        except CortexError as error:
            if error.retryable and item.attempts + 1 < MAX_ATTEMPTS:
                storage.retry_outbox_item(item.id, str(error), now + backoff(5.0, item.attempts, 600.0))
                return  # keep order: nothing after this item may overtake it
            log.warning("%s: giving up on %s: %s", target, item.payload.get("id"), error)
            storage.finish_outbox_item(item.id, "rejected", str(error))
            continue
        log.info("%s: %s", target, message)
        storage.finish_outbox_item(item.id, "done")
