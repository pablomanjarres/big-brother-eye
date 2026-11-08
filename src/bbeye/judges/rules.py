"""Offline judge: simple, explainable rules. The default, and the fallback for model judges."""

from __future__ import annotations

from bbeye.config import SignalsConfig
from bbeye.observation import WORKSTATION_LABELS, Observation, Verdict


class RulesJudge:
    name = "rules"

    def __init__(self, signals: SignalsConfig) -> None:
        self.signals = signals

    def judge(self, observation: Observation) -> Verdict:
        owner = observation.owner_person
        if owner is None:
            return Verdict("away", 0.9, self.name, "owner not visible")
        if owner.holding_phone:
            return Verdict("phone", 0.8, self.name, "phone in hand")

        labels = set(observation.object_labels)
        idle = observation.input_idle_s
        if idle is not None:
            if idle <= self.signals.input_active_s:
                return Verdict("working", 0.9, self.name, "keyboard/mouse in use")
            if "book" in labels:
                return Verdict("working", 0.6, self.name, "reading, no recent input")
            return Verdict("break", 0.6, self.name, f"no input for {round(idle)} s")
        if labels & WORKSTATION_LABELS:
            return Verdict("working", 0.55, self.name, "at the workstation")
        return Verdict("break", 0.5, self.name, "at the desk, no workstation visible")
