"""Any chat model on OpenRouter (https://openrouter.ai), asked for a JSON verdict.

By default only the text description is sent. The camera frame is attached only
when ``judge.send_images = true`` is set in the config.
"""

from __future__ import annotations

import base64
import json

import cv2
import httpx

from bbeye.judges.base import CRITERIA, INSTRUCTIONS, JudgeUnavailable
from bbeye.observation import ACTIVITIES, Observation, Verdict

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SCHEMA = {
    "type": "object",
    "properties": {
        "activity": {"type": "string", "enum": list(ACTIVITIES)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "note": {"type": "string"},
    },
    "required": ["activity", "confidence", "note"],
    "additionalProperties": False,
}


class OpenRouterJudge:
    name = "openrouter"

    def __init__(
        self, api_key: str, model: str, send_images: bool = False, client: httpx.Client | None = None
    ) -> None:
        if not api_key:
            raise JudgeUnavailable("OPENROUTER_API_KEY is not set")
        self.model, self.send_images = model, send_images
        self._client = client or httpx.Client(timeout=30)
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "Big Brother Eye",
        }

    def judge(self, observation: Observation) -> Verdict:
        options = "\n".join(f"- {name}: {meaning}" for name, meaning in CRITERIA.items())
        text = f"{INSTRUCTIONS}\n\nOptions:\n{options}\n\nFacts:\n{observation.describe()}"
        content: list[dict] = [{"type": "text", "text": text}]
        if self.send_images and observation.frame is not None:
            content.append({"type": "image_url", "image_url": {"url": _jpeg_data_url(observation.frame)}})
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "verdict", "strict": True, "schema": SCHEMA},
            },
            "temperature": 0,
            "max_tokens": 200,
        }
        try:
            response = self._client.post(OPENROUTER_URL, headers=self._headers, json=body)
        except httpx.HTTPError as error:
            raise JudgeUnavailable(f"request failed: {type(error).__name__}") from error
        if response.status_code != 200:
            raise JudgeUnavailable(f"HTTP {response.status_code}")
        return parse_completion(response.json())


def parse_completion(payload: object) -> Verdict:
    try:
        text = payload["choices"][0]["message"]["content"]  # type: ignore[index]
        data = json.loads(_strip_fence(text))
        activity, confidence = data["activity"], float(data["confidence"])
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise JudgeUnavailable("unexpected response shape") from error
    if activity not in ACTIVITIES or not 0 <= confidence <= 1:
        raise JudgeUnavailable("invalid verdict")
    return Verdict(activity, confidence, "openrouter", str(data.get("note", ""))[:200])


def _strip_fence(text: str) -> str:
    """Some models wrap JSON in a Markdown code fence even when asked not to."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text


def _jpeg_data_url(frame, max_side: int = 640) -> str:
    height, width = frame.shape[:2]
    scale = min(1.0, max_side / max(height, width))
    if scale < 1:
        frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise JudgeUnavailable("could not encode frame")
    return "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode()
