"""Jev (TypeSafe AI): a cheap model that returns typed decisions with probabilities.

API: POST https://api.typesafe.ai/v1/systemone with {state, model, questions}.
Jev is text-only, so it receives a factual description of the scene, never an image.
"""

from __future__ import annotations

import time

import httpx

from bbeye.judges.base import CRITERIA, INSTRUCTIONS, JudgeUnavailable
from bbeye.observation import ACTIVITIES, Observation, Verdict

JEV_URL = "https://api.typesafe.ai/v1/systemone"


class JevJudge:
    name = "jev"

    def __init__(self, api_key: str, model: str = "jev-latest", client: httpx.Client | None = None) -> None:
        if not api_key:
            raise JudgeUnavailable("TYPESAFE_API_KEY is not set")
        self.model = model
        self._client = client or httpx.Client(timeout=10)
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._rate_limited_until = 0.0

    def judge(self, observation: Observation) -> Verdict:
        if time.monotonic() < self._rate_limited_until:
            raise JudgeUnavailable("rate limited")
        body = {
            "state": observation.describe(),
            "model": self.model,
            "questions": {"activity": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": CRITERIA}},
        }
        try:
            response = self._client.post(JEV_URL, headers=self._headers, json=body)
        except httpx.HTTPError as error:
            raise JudgeUnavailable(f"request failed: {type(error).__name__}") from error
        if response.status_code in (429, 529):
            self._rate_limited_until = time.monotonic() + 60
            raise JudgeUnavailable(f"HTTP {response.status_code}")
        if response.status_code != 200:
            raise JudgeUnavailable(f"HTTP {response.status_code}")
        return parse_answer(response.json())


def parse_answer(payload: object) -> Verdict:
    answer = payload.get("answers", {}).get("activity") if isinstance(payload, dict) else None
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise JudgeUnavailable("unexpected response shape")
    choice = answer.get("choice")
    probabilities = answer.get("probabilities")
    if choice not in ACTIVITIES or not isinstance(probabilities, dict):
        raise JudgeUnavailable("unexpected choice")
    probability = probabilities.get(choice)
    if not isinstance(probability, int | float) or not 0 <= probability <= 1:
        raise JudgeUnavailable("invalid probability")
    return Verdict(choice, float(probability), "jev")
