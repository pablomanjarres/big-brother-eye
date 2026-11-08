import json

import httpx
import numpy as np
import pytest
from helpers import at

from bbeye.config import Config, JudgeConfig, SignalsConfig
from bbeye.judges.base import JudgeUnavailable, ThrottledJudge
from bbeye.judges.factory import build_judge
from bbeye.judges.jev import JEV_URL, JevJudge, parse_answer
from bbeye.judges.openrouter import OpenRouterJudge, parse_completion
from bbeye.judges.rules import RulesJudge
from bbeye.observation import Box, Detection, Observation, Person, Verdict

OWNER = Person(Box(0, 0, 100, 200), "me", True, False)
LAPTOP = Detection("laptop", 0.9, Box(10, 150, 90, 190))


def obs(t=0, people=(OWNER,), objects=(LAPTOP,), idle=5.0, frame=None):
    return Observation(at(t), "me", people, objects, idle, frame)


RULES = RulesJudge(SignalsConfig())


@pytest.mark.parametrize(
    "observation, activity",
    [
        (obs(people=()), "away"),
        (obs(people=(Person(OWNER.box, "me", True, True),)), "phone"),
        (obs(idle=5), "working"),
        (obs(idle=600), "break"),
        (obs(idle=600, objects=(Detection("book", 0.9, OWNER.box),)), "working"),
        (obs(idle=None), "working"),
        (obs(idle=None, objects=()), "break"),
        (obs(people=(Person(OWNER.box, None, True, False),)), "away"),  # a stranger is not the owner
    ],
)
def test_rules(observation, activity):
    assert RULES.judge(observation).activity == activity


def test_rules_can_ignore_input_idle():
    judge = RulesJudge(SignalsConfig(use_input_idle=False))
    assert judge.judge(obs(idle=9999)).activity == "working"


def jev_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_jev_request_and_answer():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "answers": {
                    "activity": {
                        "type": "choice",
                        "choice": "working",
                        "probabilities": {"working": 0.87, "phone": 0.1, "break": 0.02, "away": 0.01},
                    }
                }
            },
        )

    verdict = JevJudge("key", client=jev_client(handler)).judge(obs())
    assert verdict == Verdict("working", 0.87, "jev")
    assert seen["url"] == JEV_URL and seen["auth"] == "Bearer key"
    assert seen["body"]["model"] == "jev-latest"
    assert set(seen["body"]["questions"]["activity"]["criteria"]) == {"working", "phone", "break", "away"}
    assert "keyboard/mouse" in seen["body"]["state"]


def test_jev_rate_limit_backs_off():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429)

    judge = JevJudge("key", client=jev_client(handler))
    for _ in range(3):
        with pytest.raises(JudgeUnavailable):
            judge.judge(obs())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"answers": {"activity": {"type": "noul"}}},
        {"answers": {"activity": {"type": "choice", "choice": "sleep", "probabilities": {}}}},
        {"answers": {"activity": {"type": "choice", "choice": "away", "probabilities": {"away": 3}}}},
    ],
)
def test_jev_rejects_bad_answers(payload):
    with pytest.raises(JudgeUnavailable):
        parse_answer(payload)


def test_jev_needs_key():
    with pytest.raises(JudgeUnavailable):
        JevJudge("")


def completion(content):
    return {"choices": [{"message": {"content": content}}]}


def test_openrouter_text_only_by_default():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200, json=completion('{"activity":"phone","confidence":0.7,"note":"scrolling"}')
        )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    judge = OpenRouterJudge("key", "some/model", client=jev_client(handler))
    assert judge.judge(obs(frame=frame)) == Verdict("phone", 0.7, "openrouter", "scrolling")
    assert [part["type"] for part in bodies[0]["messages"][0]["content"]] == ["text"]

    judge = OpenRouterJudge("key", "some/model", send_images=True, client=jev_client(handler))
    judge.judge(obs(frame=frame))
    assert bodies[1]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_openrouter_accepts_fenced_json():
    fenced = '```json\n{"activity":"working","confidence":0.8,"note":""}\n```'
    assert parse_completion(completion(fenced)).activity == "working"


def test_openrouter_bad_json():
    with pytest.raises(JudgeUnavailable):
        parse_completion(completion("not json"))


class FakeJudge:
    name = "fake"

    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def judge(self, observation):
        self.calls += 1
        if self.fail:
            raise JudgeUnavailable("down")
        return Verdict("working", 0.99, "fake")


def test_throttle_reuses_verdict_and_never_asks_about_an_empty_room():
    fake = FakeJudge()
    judge = ThrottledJudge(fake, RULES, min_interval_s=10)
    assert judge.judge(obs(0)).source == "fake"
    assert judge.judge(obs(5)).source == "fake"
    assert fake.calls == 1
    assert judge.judge(obs(11)).source == "fake" and fake.calls == 2
    assert judge.judge(obs(12, people=())).activity == "away" and fake.calls == 2


def test_throttle_falls_back_on_failure_and_backs_off():
    fake = FakeJudge(fail=True)
    judge = ThrottledJudge(fake, RULES, min_interval_s=60)
    for t in range(0, 600, 2):  # ten minutes of frames
        assert judge.judge(obs(t)).source == "rules"
    assert fake.calls <= 4  # 60 s, 120 s, 240 s, 480 s back-off, not one call per frame
    fake.fail = False
    assert judge.judge(obs(2000)).source == "fake"


def test_throttle_survives_flickering_absence():
    fake = FakeJudge()
    judge = ThrottledJudge(fake, RULES, min_interval_s=60)
    for t in range(0, 60, 2):
        judge.judge(obs(t, people=() if t % 4 else (OWNER,)))
    assert fake.calls == 1


def test_factory_falls_back_to_rules_without_keys():
    config = Config(judge=JudgeConfig(kind="jev"))
    assert build_judge(config, {}).name == "rules"
    assert build_judge(config, {"TYPESAFE_API_KEY": "k"}).name == "jev"
