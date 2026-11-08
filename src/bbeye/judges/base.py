"""A judge looks at one Observation and says what the owner is doing."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Protocol

from bbeye.observation import Observation, Verdict
from bbeye.timeutil import backoff

log = logging.getLogger(__name__)

# The question every model judge answers. The four keys are the activities.
CRITERIA = {
    "working": "The owner is at the desk doing focused work: typing, using the computer, reading or writing.",
    "phone": "The owner is using a phone.",
    "break": "The owner is at the desk but not working: idle, eating, resting or chatting.",
    "away": "The owner is not at the desk.",
}
INSTRUCTIONS = (
    "A desk camera and the work computer produced the facts below. Decide what the owner is doing right now. "
    "Recent keyboard or mouse input on the work computer is strong evidence of work."
)


class JudgeUnavailable(RuntimeError):
    """The judge could not answer right now (no key, timeout, rate limit, bad response)."""


class Judge(Protocol):
    name: str

    def judge(self, observation: Observation) -> Verdict: ...


class ThrottledJudge:
    """Calls a (paid, remote) judge at most every ``min_interval_s`` and only when it can add something.

    Absence is decided locally: no model is asked whether an empty room is empty. Between
    calls the last model verdict stands. Failed calls count against the limit too, and
    repeated failures back off (doubling, up to 10 minutes), so a broken model never turns
    into a call on every frame. Meanwhile the fallback judge answers.
    """

    MAX_BACKOFF_S = 600.0

    def __init__(self, primary: Judge, fallback: Judge, min_interval_s: float) -> None:
        self.primary, self.fallback = primary, fallback
        self.min_interval_s = min_interval_s
        self.name = primary.name
        self._last: Verdict | None = None
        self._next_call_at: datetime | None = None
        self._failures = 0

    def judge(self, observation: Observation) -> Verdict:
        if not observation.owner_present:
            self._last = None  # a stale verdict must not outlive an absence
            return self.fallback.judge(observation)
        if self._next_call_at is not None and observation.at < self._next_call_at:
            return self._last or self.fallback.judge(observation)
        try:
            verdict = self.primary.judge(observation)
        except JudgeUnavailable as error:
            self._failures += 1
            self._next_call_at = observation.at + backoff(
                self.min_interval_s, self._failures, self.MAX_BACKOFF_S
            )
            self._last = None
            if self._failures == 1:
                log.warning("%s unavailable (%s); using %s", self.primary.name, error, self.fallback.name)
            return self.fallback.judge(observation)
        if self._failures:
            log.info("%s is answering again", self.primary.name)
        self._failures = 0
        self._next_call_at = observation.at + timedelta(seconds=self.min_interval_s)
        self._last = verdict
        return verdict
