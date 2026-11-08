"""Builds the judge chosen in the config. Model judges always fall back to the offline rules."""

from __future__ import annotations

import logging

from bbeye.config import Config
from bbeye.judges.base import Judge, JudgeUnavailable, ThrottledJudge
from bbeye.judges.rules import RulesJudge

log = logging.getLogger(__name__)

# The secret each remote judge needs.
SECRET_FOR = {"jev": "TYPESAFE_API_KEY", "openrouter": "OPENROUTER_API_KEY"}


def build_judge(config: Config, secrets: dict[str, str]) -> Judge:
    rules = RulesJudge(config.signals)
    try:
        if config.judge.kind == "jev":
            from bbeye.judges.jev import JevJudge

            jev = JevJudge(secrets.get(SECRET_FOR["jev"], ""), config.judge.jev_model)
            return ThrottledJudge(jev, rules, config.judge.jev_min_interval_s)
        if config.judge.kind == "openrouter":
            from bbeye.judges.openrouter import OpenRouterJudge

            router = OpenRouterJudge(
                secrets.get(SECRET_FOR["openrouter"], ""),
                config.judge.openrouter_model,
                config.judge.send_images,
            )
            return ThrottledJudge(router, rules, config.judge.openrouter_min_interval_s)
    except JudgeUnavailable as error:
        log.warning("Judge %r unavailable (%s); using offline rules", config.judge.kind, error)
    return rules
