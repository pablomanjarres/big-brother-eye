from helpers import at

from bbeye.observation import Box, Detection, Face
from bbeye.perception import IdentityMemory, build_observation

PERSON = Detection("person", 0.9, Box(100, 100, 400, 700))
OWNER_FACE = Face(Box(200, 150, 300, 260), "me", 0.8)
PHONE = Detection("cell phone", 0.8, Box(250, 400, 300, 480))


def build(detections, faces, memory, t=0, idle=None):
    return build_observation(at(t), "me", detections, faces, idle, memory)


def test_recognized_owner():
    obs = build([PERSON], [OWNER_FACE], IdentityMemory(45))
    assert obs.owner_present and obs.owner_person.face_visible and not obs.others


def test_phone_in_hand():
    obs = build([PERSON, PHONE], [OWNER_FACE], IdentityMemory(45))
    assert obs.owner_person.holding_phone
    assert obs.object_labels == ["cell phone"]


def test_identity_held_while_face_turned_away_then_expires():
    memory = IdentityMemory(45)
    build([PERSON], [OWNER_FACE], memory, t=0)
    turned = Detection("person", 0.9, Box(110, 100, 410, 700))
    assert build([turned], [], memory, t=30).owner_present
    assert build([turned], [], memory, t=70).owner_present  # recall refreshed the memory at t=30
    assert not build([turned], [], memory, t=200).owner_present


def test_unknown_visible_face_is_never_given_the_owner_name():
    memory = IdentityMemory(45)
    build([PERSON], [OWNER_FACE], memory, t=0)
    stranger = Face(Box(200, 150, 300, 260), None, 0.1)
    obs = build([PERSON], [stranger], memory, t=10)
    assert not obs.owner_present and len(obs.others) == 1


def test_two_people_one_owner():
    guest = Detection("person", 0.9, Box(800, 100, 1100, 700))
    guest_face = Face(Box(900, 150, 1000, 260), None, 0.05)
    obs = build([PERSON, guest], [OWNER_FACE, guest_face], IdentityMemory(45))
    assert obs.owner_present and len(obs.others) == 1


def test_a_faceless_person_shape_is_not_a_visitor():
    # Seen on a real laptop: a thin box fixed at the image edge, confidence 0.16 to 0.49, never a face.
    fixture = Detection("person", 0.4, Box(0, 0, 160, 660))
    obs = build([fixture], [], IdentityMemory(45))
    assert len(obs.people) == 1 and obs.others == () and not obs.owner_present


def test_face_without_person_box_counts_as_person():
    obs = build([], [OWNER_FACE], IdentityMemory(45))
    assert obs.owner_present


def test_describe_has_no_surprises():
    obs = build([PERSON, PHONE], [OWNER_FACE], IdentityMemory(45), idle=12.4)
    text = obs.describe()
    assert "holding a phone" in text and "12" in text


def test_preview_draws_faceless_shapes_without_counting_them():
    import numpy as np

    from bbeye.observation import Verdict
    from bbeye.vision.overlay import draw

    fixture = Detection("person", 0.4, Box(0, 0, 160, 660))
    obs = build([PERSON, fixture], [OWNER_FACE], IdentityMemory(45))
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    out = draw(frame, obs, Verdict("working", 0.9, "rules"))
    assert out.shape == frame.shape and out.any() and not frame.any()
