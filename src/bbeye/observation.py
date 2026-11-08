"""Plain data types shared by every stage of the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Activity = Literal["working", "phone", "break", "away"]
ACTIVITIES: tuple[Activity, ...] = ("working", "phone", "break", "away")

WORKSTATION_LABELS = frozenset({"laptop", "keyboard", "mouse", "tv"})


@dataclass(frozen=True)
class Box:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    def contains(self, point: tuple[float, float]) -> bool:
        x, y = point
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2

    def intersection(self, other: Box) -> float:
        width = min(self.x2, other.x2) - max(self.x1, other.x1)
        height = min(self.y2, other.y2) - max(self.y1, other.y1)
        return max(0.0, width) * max(0.0, height)

    def iou(self, other: Box) -> float:
        overlap = self.intersection(other)
        union = self.area + other.area - overlap
        return overlap / union if union > 0 else 0.0


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    box: Box


@dataclass(frozen=True)
class Face:
    box: Box
    name: str | None  # enrolled name, or None for an unknown face
    similarity: float


@dataclass(frozen=True)
class Person:
    box: Box
    name: str | None  # None = not recognized
    face_visible: bool
    holding_phone: bool


@dataclass(frozen=True)
class Observation:
    at: datetime
    owner: str
    people: tuple[Person, ...]
    objects: tuple[Detection, ...]
    input_idle_s: float | None
    frame: Any = field(default=None, compare=False, repr=False)  # never persisted

    @property
    def owner_person(self) -> Person | None:
        return next((person for person in self.people if person.name == self.owner), None)

    @property
    def owner_present(self) -> bool:
        return self.owner_person is not None

    @property
    def others(self) -> tuple[Person, ...]:
        return tuple(person for person in self.people if person.name != self.owner)

    @property
    def object_labels(self) -> list[str]:
        return sorted({detection.label for detection in self.objects})

    def describe(self) -> str:
        """A short, factual text description used as model input. Contains no image data."""
        lines = [f"Time: {self.at.astimezone().strftime('%A %H:%M')}"]
        owner = self.owner_person
        if owner is None:
            lines.append("The owner is not visible.")
        else:
            face = "face visible" if owner.face_visible else "face not visible (recognized by position)"
            lines.append(f"The owner is visible ({face}).")
            lines.append(
                "The owner is holding a phone." if owner.holding_phone else "No phone in the owner's hands."
            )
        if self.others:
            lines.append(f"Other people visible: {len(self.others)}.")
        labels = self.object_labels
        lines.append(f"Objects on camera: {', '.join(labels) if labels else 'none detected'}.")
        if self.input_idle_s is None:
            lines.append("Keyboard/mouse activity: unknown.")
        else:
            lines.append(
                f"Seconds since last keyboard/mouse input on the work computer: {round(self.input_idle_s)}."
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class Verdict:
    activity: Activity
    confidence: float
    source: str
    note: str = ""
