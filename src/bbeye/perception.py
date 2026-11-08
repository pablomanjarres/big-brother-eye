"""Turn raw detections and faces into an Observation of who is where."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bbeye.observation import Box, Detection, Face, Observation, Person

PHONE_LABEL = "cell phone"
PERSON_LABEL = "person"


@dataclass
class _Sighting:
    box: Box
    at: datetime
    confirmed_at: datetime  # last clear face match, as opposed to a carried-over sighting


class IdentityMemory:
    """Keeps a recognized name on a person for a while after their face turns away.

    A person box inherits a recent name when it overlaps the box where that name was
    last confirmed and either shows no face or shows a face that is unclear but still
    somewhat similar (``continuity_threshold``). A clearly different face never does, and
    an unclear face can carry a name only for ``unclear_hold_s`` after the last clear match,
    so a lookalike who sits down in the same place cannot keep it.
    """

    def __init__(
        self,
        hold_s: float,
        min_iou: float = 0.3,
        continuity_threshold: float = 0.2,
        unclear_hold_s: float = 300.0,
    ) -> None:
        self.hold_s = hold_s
        self.min_iou = min_iou
        self.continuity_threshold = continuity_threshold
        self.unclear_hold_s = unclear_hold_s
        self._last: dict[str, _Sighting] = {}

    def remember(self, name: str, box: Box, at: datetime, confirmed: bool = True) -> None:
        previous = self._last.get(name)
        confirmed_at = at if confirmed or previous is None else previous.confirmed_at
        self._last[name] = _Sighting(box, at, confirmed_at)

    def recall(self, box: Box, at: datetime, taken: set[str], unclear_face: bool = False) -> str | None:
        best_name, best_iou = None, self.min_iou
        for name, sighting in self._last.items():
            if name in taken or (at - sighting.at).total_seconds() > self.hold_s:
                continue
            if unclear_face and (at - sighting.confirmed_at).total_seconds() > self.unclear_hold_s:
                continue
            overlap = box.iou(sighting.box)
            if overlap >= best_iou:
                best_name, best_iou = name, overlap
        return best_name


def build_observation(
    at: datetime,
    owner: str,
    detections: list[Detection],
    faces: list[Face],
    input_idle_s: float | None,
    memory: IdentityMemory,
    frame: object = None,
) -> Observation:
    person_boxes = [d.box for d in detections if d.label == PERSON_LABEL]
    phones = [d.box for d in detections if d.label == PHONE_LABEL]
    objects = tuple(d for d in detections if d.label != PERSON_LABEL)

    # Each face goes to the person box that contains its center (smallest such box wins).
    face_for: dict[int, Face] = {}
    for face in faces:
        holders = [i for i, box in enumerate(person_boxes) if box.contains(face.box.center)]
        if holders:
            index = min(holders, key=lambda i: person_boxes[i].area)
            current = face_for.get(index)
            if current is None or face.similarity > current.similarity:
                face_for[index] = face
        else:
            # A close-up face can fill the frame without a person detection; count the face as the person.
            person_boxes.append(face.box)
            face_for[len(person_boxes) - 1] = face

    people: list[Person] = []
    taken: set[str] = set()
    # Recognized faces first, so recall never steals a name that is visibly confirmed.
    order = sorted(
        range(len(person_boxes)), key=lambda i: face_for.get(i) is None or face_for[i].name is None
    )
    for index in order:
        box = person_boxes[index]
        face = face_for.get(index)
        name = face.name if face and face.name not in taken else None
        confirmed = name is not None
        # Only a face that matched nobody may borrow a name; one that matched someone else never does.
        unclear = face is not None and face.name is None and face.similarity >= memory.continuity_threshold
        if not name and (face is None or unclear):
            name = memory.recall(box, at, taken, unclear_face=unclear)
        if name:
            taken.add(name)
            memory.remember(name, box, at, confirmed=confirmed)
        holding = any(box.intersection(phone) > 0.5 * phone.area for phone in phones)
        people.append(Person(box=box, name=name, face_visible=face is not None, holding_phone=holding))

    return Observation(
        at=at,
        owner=owner,
        people=tuple(people),
        objects=objects,
        input_idle_s=input_idle_s,
        frame=frame,
    )
