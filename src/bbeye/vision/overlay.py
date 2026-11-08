"""Draws what the Eye sees, for the optional preview window."""

from __future__ import annotations

import cv2
import numpy as np

from bbeye.observation import Observation, Verdict

GREEN, ORANGE, GREY = (80, 200, 80), (0, 160, 255), (180, 180, 180)


def draw(frame: np.ndarray, observation: Observation, verdict: Verdict) -> np.ndarray:
    canvas = frame.copy()
    for detection in observation.objects:
        b = detection.box
        cv2.rectangle(canvas, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), GREY, 1)
        cv2.putText(
            canvas, detection.label, (int(b.x1), int(b.y1) - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, GREY, 1
        )
    others = observation.others
    for person in observation.people:
        b = person.box
        if person is observation.owner_person:
            color, label = GREEN, person.name or ""
        elif person in others:
            color, label = ORANGE, person.name or "unknown"
        else:  # a person shape with no face evidence; not counted as anyone
            color, label = GREY, "no face"
        if person.holding_phone:
            label += " (phone)"
        cv2.rectangle(canvas, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), color, 2)
        cv2.putText(canvas, label, (int(b.x1), int(b.y1) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    banner = f"{verdict.activity} ({verdict.confidence:.0%}, {verdict.source}) {verdict.note}"
    cv2.putText(canvas, banner, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 3)
    cv2.putText(canvas, banner, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 30, 30), 1)
    return canvas
