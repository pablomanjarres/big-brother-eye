from __future__ import annotations

from bbeye.alerts import Notifier
from bbeye.notify.console import ConsoleNotifier
from bbeye.notify.pushcut import PushcutNotifier


def pushcut_configured(secrets: dict[str, str]) -> bool:
    return bool(secrets.get("PUSHCUT_API_KEY") and secrets.get("PUSHCUT_NOTIFICATION_ID"))


def build_notifiers(secrets: dict[str, str]) -> list[Notifier]:
    notifiers: list[Notifier] = [ConsoleNotifier()]
    if pushcut_configured(secrets):
        notifiers.append(PushcutNotifier(secrets["PUSHCUT_API_KEY"], secrets["PUSHCUT_NOTIFICATION_ID"]))
    return notifiers
