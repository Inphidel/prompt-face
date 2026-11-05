"""Read place, facing, shape, and fingers from the hand landmarker.

Finger extension uses 3D distance from the wrist. Image y grows downward and
the camera looks down, so a finger can point any direction in the picture.
A fingertip farther from the wrist than its PIP joint is up.

Place uses the face, in image space. The hand has to be closer to the camera
than the nose in the pose model. Hand-landmark z and face-landmark z are not
the same coordinate system, so depth comes from the pose points only.

Facing uses the palm normal. Image x points right and y points down, so +z
points away from the camera. For a right hand, wrist-to-index crossed with
wrist-to-pinky points toward the camera when the palm does. A left palm points
the other way, and the side accounts for that. facing_sign flips the result
if a live check reads palm and back backwards.
"""

from __future__ import annotations

import math

from face_state.pose import (
    LEFT_EYE,
    LEFT_INDEX,
    LEFT_PINKY,
    LEFT_THUMB,
    LEFT_WRIST,
    NOSE,
    RIGHT_EYE,
    RIGHT_INDEX,
    RIGHT_PINKY,
    RIGHT_THUMB,
    RIGHT_WRIST,
)

WRIST = 0
THUMB_IP = 3
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_PIP = 6
INDEX_TIP = 8
MIDDLE_PIP = 10
MIDDLE_TIP = 12
RING_PIP = 14
RING_TIP = 16
PINKY_MCP = 17
PINKY_PIP = 18
PINKY_TIP = 20

# Face mesh. 33 and 263 are the outer eye corners, 1 is the nose tip,
# 13 and 14 are the centers of the lips.
FACE_NOSE = 1
FACE_EYE_A = 33
FACE_EYE_B = 263
FACE_UPPER_LIP = 13
FACE_LOWER_LIP = 14

# A finger is up when the tip is this much farther from the wrist than the PIP.
FINGER_MARGIN = 1.05
THUMB_MARGIN = 1.05
# Pose wrist and hand wrist have to be this close, in normalized image units,
# before the pose side wins over the handedness label.
MAX_WRIST_MATCH = 0.30
ZONE_SCALE = {"brow": 0.55, "eyes": 0.62, "nose": 0.42, "mouth": 0.48}
TIPS = (
    ("thumb", THUMB_TIP),
    ("index", INDEX_TIP),
    ("middle", MIDDLE_TIP),
    ("ring", RING_TIP),
    ("pinky", PINKY_TIP),
)
FINGER_JOINTS = (
    ("index", INDEX_TIP, INDEX_PIP),
    ("middle", MIDDLE_TIP, MIDDLE_PIP),
    ("ring", RING_TIP, RING_PIP),
    ("pinky", PINKY_TIP, PINKY_PIP),
)
POSE_SIDE = {
    "l": (LEFT_WRIST, LEFT_THUMB, LEFT_INDEX, LEFT_PINKY),
    "r": (RIGHT_WRIST, RIGHT_THUMB, RIGHT_INDEX, RIGHT_PINKY),
}


def raw_label(reading: dict | None) -> str | None:
    """One short line of the frame's place, facing, shape, and raised fingers."""
    if not reading:
        return None
    raised = [name for name, _index in TIPS if reading.get(name) == "up"]
    fingers = ",".join(raised) if raised else "-"
    place = reading.get("place", "none")
    facing = reading.get("facing", "none")
    shape = reading.get("shape", "none")
    return f"{place} {facing} {shape} {fingers}"


def _xyz(point) -> tuple[float, float, float]:
    return (float(point.x), float(point.y), float(point.z or 0.0))


def _dist3(a, b) -> float:
    ax, ay, az = _xyz(a)
    bx, by, bz = _xyz(b)
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)


def _visible(point, minimum: float) -> bool:
    visibility = getattr(point, "visibility", None)
    if visibility is None:
        return True
    return float(visibility) >= minimum


def _mp_side(label: str | None) -> str | None:
    """Map a MediaPipe handedness label to this person's side.

    The model assumes a mirrored selfie. This camera is not mirrored, so its
    Left is the person's right.
    """
    folded = (label or "").strip().lower()
    if folded == "left":
        return "r"
    if folded == "right":
        return "l"
    return None


class _Seen:
    def __init__(self, mp_label: str | None, image, world, wrist: tuple[float, float]):
        self.mp_label = mp_label
        self.image = image
        self.world = world
        self.wrist = wrist


class HandMeasure:
    """Turn one hand-landmarker result into a reading for each side."""

    def __init__(self, edge: float = 0.35, facing_sign: float = 1.0, depth: float = 0.0, memory: int = 10):
        self.edge = float(edge)
        self.facing_sign = float(facing_sign)
        self.depth = float(depth)
        self.memory = int(memory)
        self._cache = None

    def measure(self, hand_result, face_result, pose_result) -> dict:
        pose_points = _first(getattr(pose_result, "pose_landmarks", None) if pose_result is not None else None)
        zones, nose_z, fresh = self._anchors(face_result, pose_points)
        assigned = self._assign(self._detected(hand_result), pose_points)
        reading = {"l": None, "r": None}
        for side, seen in assigned.items():
            reading[side] = self._reading(seen, side, zones, nose_z, fresh, pose_points)
        return reading

    def _detected(self, result) -> list[_Seen]:
        if result is None:
            return []
        images = getattr(result, "hand_landmarks", None) or []
        worlds = getattr(result, "hand_world_landmarks", None) or []
        handed = getattr(result, "handedness", None) or []
        found = []
        for index, image in enumerate(images):
            if len(image) < 21:
                continue
            world = worlds[index] if index < len(worlds) and len(worlds[index]) >= 21 else None
            label = None
            if index < len(handed) and handed[index]:
                label = getattr(handed[index][0], "category_name", None)
            wrist = image[WRIST]
            found.append(_Seen(label, image, world, (float(wrist.x), float(wrist.y))))
        return found[:2]

    def _assign(self, found: list[_Seen], pose_points) -> dict[str, _Seen]:
        wrists = {
            "l": _pose_xy(pose_points, LEFT_WRIST),
            "r": _pose_xy(pose_points, RIGHT_WRIST),
        }
        if len(found) == 2 and wrists["l"] and wrists["r"]:
            first, second = found
            keep = _hypot(first.wrist, wrists["l"]) + _hypot(second.wrist, wrists["r"])
            swap = _hypot(first.wrist, wrists["r"]) + _hypot(second.wrist, wrists["l"])
            pair = {"l": first, "r": second} if keep <= swap else {"l": second, "r": first}
            if all(_hypot(pair[side].wrist, wrists[side]) <= MAX_WRIST_MATCH for side in pair):
                return pair
        if len(found) == 1 and (wrists["l"] or wrists["r"]):
            seen = found[0]
            options = [
                (side, _hypot(seen.wrist, wrists[side]))
                for side in ("l", "r")
                if wrists[side] is not None
            ]
            side, distance = min(options, key=lambda item: item[1])
            if distance <= MAX_WRIST_MATCH:
                return {side: seen}
        assigned = {}
        for seen in found:
            side = _mp_side(seen.mp_label)
            if side and side not in assigned:
                assigned[side] = seen
        return assigned

    def _anchors(self, face_result, pose_points):
        zones = _zones_from_face(face_result)
        fresh = zones is not None
        if zones is None:
            zones = _zones_from_pose(pose_points)
            fresh = zones is not None
        nose_z = _nose_z(pose_points)
        if zones is None and nose_z is None:
            return self._decay()
        if self._cache is not None:
            old_zones, old_nose, _left = self._cache
            if zones is None:
                zones = old_zones
            if nose_z is None:
                nose_z = old_nose
        self._cache = (zones, nose_z, self.memory)
        return zones, nose_z, fresh

    def _decay(self):
        if self._cache is None:
            return None, None, False
        zones, nose_z, left = self._cache
        if left <= 1:
            self._cache = None
            return None, None, False
        self._cache = (zones, nose_z, left - 1)
        return zones, nose_z, False

    def _reading(self, seen: _Seen, side: str, zones, nose_z, fresh: bool, pose_points) -> dict:
        space = seen.world or seen.image
        fingers = _fingers(space)
        shape = _shape(fingers)
        return {
            "place": self._place(seen.image, shape, zones, self._depth_ok(pose_points, side, nose_z), fresh),
            "facing": self._facing(space, side),
            "shape": shape,
            **fingers,
        }

    def _depth_ok(self, pose_points, side: str, nose_z) -> bool | None:
        if nose_z is None or pose_points is None:
            return None
        saw = False
        for index in POSE_SIDE[side]:
            if len(pose_points) <= index:
                continue
            point = pose_points[index]
            if not _visible(point, 0.25):
                continue
            saw = True
            if (nose_z - float(point.z or 0.0)) > self.depth:
                return True
        if not saw:
            return None
        return False

    def _place(self, image, shape: str, zones, depth_ok, fresh: bool) -> str:
        if not zones or depth_ok is False or (depth_ok is None and not fresh):
            return "none"
        if shape == "point":
            zone, _ratio = _zone_at(image[INDEX_TIP], zones)
            return zone or "none"
        votes: dict[str, int] = {}
        nearest: dict[str, float] = {}
        for _name, index in TIPS:
            zone, ratio = _zone_at(image[index], zones)
            if zone is None or ratio is None:
                continue
            votes[zone] = votes.get(zone, 0) + 1
            if zone not in nearest or ratio < nearest[zone]:
                nearest[zone] = ratio
        if not votes:
            return "none"
        return min(votes, key=lambda zone: (-votes[zone], nearest[zone]))

    def _facing(self, points, side: str) -> str:
        wrist = _xyz(points[WRIST])
        index = _xyz(points[INDEX_MCP])
        pinky = _xyz(points[PINKY_MCP])
        vx, vy, vz = _cross(_sub(index, wrist), _sub(pinky, wrist))
        magnitude = math.sqrt(vx * vx + vy * vy + vz * vz)
        if magnitude < 1e-8:
            return "none"
        if abs(vz) / magnitude < self.edge:
            return "edge"
        side_sign = -1.0 if side == "l" else 1.0
        toward = side_sign * self.facing_sign * vz
        return "palm" if toward < 0.0 else "back"


def _first(groups):
    if not groups:
        return None
    return groups[0]


def _sub(a, b) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _hypot(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _pose_xy(points, index: int):
    if points is None or len(points) <= index:
        return None
    point = points[index]
    if not _visible(point, 0.3):
        return None
    return (float(point.x), float(point.y))


def _nose_z(points):
    if points is None or len(points) <= NOSE:
        return None
    point = points[NOSE]
    if not _visible(point, 0.35):
        return None
    return float(point.z or 0.0)


def _fingers(points) -> dict[str, str]:
    found = {}
    for name, tip, pip in FINGER_JOINTS:
        extended = _dist3(points[tip], points[WRIST]) > _dist3(points[pip], points[WRIST]) * FINGER_MARGIN
        found[name] = "up" if extended else "down"
    thumb = _dist3(points[THUMB_TIP], points[INDEX_MCP]) > _dist3(points[THUMB_IP], points[INDEX_MCP]) * THUMB_MARGIN
    found["thumb"] = "up" if thumb else "down"
    return found


def _shape(fingers: dict[str, str]) -> str:
    raised = [fingers[name] == "up" for name in ("index", "middle", "ring", "pinky")]
    if not any(raised):
        return "fist"
    if raised == [True, False, False, False]:
        return "point"
    if sum(raised) >= 3:
        return "open"
    return "none"


def _zone_at(point, zones):
    best = None
    best_ratio = None
    for name, (x, y, radius) in zones.items():
        if radius <= 1e-6:
            continue
        ratio = math.hypot(float(point.x) - x, float(point.y) - y) / radius
        if ratio <= 1.0 and (best_ratio is None or ratio < best_ratio):
            best = name
            best_ratio = ratio
    return best, best_ratio


def _span_zones(ax, ay, bx, by, nose_x, nose_y, mouth_x, mouth_y):
    span = math.hypot(ax - bx, ay - by)
    if span < 0.02 or abs(ay - by) > abs(ax - bx):
        return None
    eyes_x = (ax + bx) / 2
    eyes_y = (ay + by) / 2
    brow_x = eyes_x + (eyes_x - nose_x)
    brow_y = eyes_y + (eyes_y - nose_y)
    centers = {
        "brow": (brow_x, brow_y),
        "eyes": (eyes_x, eyes_y),
        "nose": (nose_x, nose_y),
        "mouth": (mouth_x, mouth_y),
    }
    return {name: (x, y, span * ZONE_SCALE[name]) for name, (x, y) in centers.items()}


def _zones_from_face(face_result):
    points = _first(getattr(face_result, "face_landmarks", None) if face_result is not None else None)
    if points is None or len(points) <= FACE_EYE_B:
        return None
    mouth_a = points[FACE_UPPER_LIP]
    mouth_b = points[FACE_LOWER_LIP]
    return _span_zones(
        points[FACE_EYE_A].x, points[FACE_EYE_A].y,
        points[FACE_EYE_B].x, points[FACE_EYE_B].y,
        points[FACE_NOSE].x, points[FACE_NOSE].y,
        (mouth_a.x + mouth_b.x) / 2, (mouth_a.y + mouth_b.y) / 2,
    )


def _zones_from_pose(points):
    if points is None or len(points) <= 5:
        return None
    # The mouth is not a pose landmark, so it sits below the nose by most of
    # the gap that separates the eyes from the nose.
    if not (_visible(points[LEFT_EYE], 0.3) and _visible(points[RIGHT_EYE], 0.3) and _visible(points[NOSE], 0.35)):
        return None
    nose = points[NOSE]
    eye_x = (points[LEFT_EYE].x + points[RIGHT_EYE].x) / 2
    eye_y = (points[LEFT_EYE].y + points[RIGHT_EYE].y) / 2
    mouth_x = nose.x + (nose.x - eye_x) * 0.85
    mouth_y = nose.y + (nose.y - eye_y) * 0.85
    return _span_zones(
        points[LEFT_EYE].x, points[LEFT_EYE].y,
        points[RIGHT_EYE].x, points[RIGHT_EYE].y,
        nose.x, nose.y,
        mouth_x, mouth_y,
    )
