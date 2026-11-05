"""Measure arm height from pose landmarks.

Image y grows downward. An arm is up when the wrist sits above its shoulder.
The overlap score is how deep a palm point sits in the face circle while it
is closer to the camera than the nose. The live hand returns come from the
hand landmarker.
"""

from __future__ import annotations

import math

NOSE = 0
LEFT_EYE = 2
RIGHT_EYE = 5
LEFT_EAR = 7
RIGHT_EAR = 8
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_WRIST = 15
RIGHT_WRIST = 16
LEFT_PINKY = 17
RIGHT_PINKY = 18
LEFT_INDEX = 19
RIGHT_INDEX = 20
LEFT_THUMB = 21
RIGHT_THUMB = 22
LEFT_HIP = 23
RIGHT_HIP = 24

LEFT_HAND = (LEFT_WRIST, LEFT_THUMB, LEFT_INDEX, LEFT_PINKY)
RIGHT_HAND = (RIGHT_WRIST, RIGHT_THUMB, RIGHT_INDEX, RIGHT_PINKY)


def _dist(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _visible(point, minimum: float) -> bool:
    visibility = getattr(point, "visibility", None)
    if visibility is None:
        return True
    return float(visibility) >= minimum


class PoseMeasure:
    """Turn one pose into arm and hand scores. Remembers the face briefly."""

    def __init__(self, radius: float, depth: float, memory: int = 10):
        self.radius_scale = float(radius)
        self.depth = float(depth)
        self.memory = int(memory)
        self._anchor = None

    def measure(self, result) -> dict | None:
        landmarks = getattr(result, "pose_landmarks", None) if result is not None else None
        if not landmarks:
            self._decay()
            return None
        points = landmarks[0]
        if len(points) <= LEFT_HIP:
            self._decay()
            return None
        torso = self._torso(points)
        if torso is None:
            self._decay()
            return None
        anchor = self._anchor_for(points)
        return {
            "arm_l": self._rise(points, LEFT_SHOULDER, LEFT_WRIST, torso),
            "arm_r": self._rise(points, RIGHT_SHOULDER, RIGHT_WRIST, torso),
            "hand_l": self._hand(points, LEFT_HAND, anchor),
            "hand_r": self._hand(points, RIGHT_HAND, anchor),
        }

    def _decay(self) -> None:
        if self._anchor is None:
            return
        x, y, z, radius, left = self._anchor
        if left <= 1:
            self._anchor = None
        else:
            self._anchor = (x, y, z, radius, left - 1)

    def _torso(self, points) -> float | None:
        left_shoulder = points[LEFT_SHOULDER]
        right_shoulder = points[RIGHT_SHOULDER]
        if not (_visible(left_shoulder, 0.4) and _visible(right_shoulder, 0.4)):
            return None
        shoulder_width = _dist(left_shoulder, right_shoulder)
        left_hip = points[LEFT_HIP]
        right_hip = points[RIGHT_HIP]
        if _visible(left_hip, 0.3) and _visible(right_hip, 0.3):
            torso = math.hypot(
                (left_shoulder.x + right_shoulder.x) / 2 - (left_hip.x + right_hip.x) / 2,
                (left_shoulder.y + right_shoulder.y) / 2 - (left_hip.y + right_hip.y) / 2,
            )
            if torso > 0.03:
                return torso
        if shoulder_width > 0.03:
            return shoulder_width * 1.4
        return None

    def _rise(self, points, shoulder: int, wrist: int, torso: float) -> float | None:
        shoulder_point = points[shoulder]
        wrist_point = points[wrist]
        if not (_visible(shoulder_point, 0.4) and _visible(wrist_point, 0.4)):
            return None
        return (shoulder_point.y - wrist_point.y) / torso

    def _anchor_for(self, points):
        nose = points[NOSE]
        span = self._span(points)
        if _visible(nose, 0.35) and span is not None:
            self._anchor = (nose.x, nose.y, nose.z, span * self.radius_scale, self.memory)
            return self._anchor
        if self._anchor is None:
            return None
        x, y, z, radius, left = self._anchor
        if left <= 0:
            self._anchor = None
            return None
        self._anchor = (x, y, z, radius, left - 1)
        return self._anchor

    def _span(self, points) -> float | None:
        left_ear = points[LEFT_EAR]
        right_ear = points[RIGHT_EAR]
        if _visible(left_ear, 0.3) and _visible(right_ear, 0.3):
            span = _dist(left_ear, right_ear)
            if span > 0.02:
                return span
        left_eye = points[LEFT_EYE]
        right_eye = points[RIGHT_EYE]
        if _visible(left_eye, 0.3) and _visible(right_eye, 0.3):
            span = _dist(left_eye, right_eye) * 2.2
            if span > 0.02:
                return span
        return None

    def _hand(self, points, indexes, anchor) -> float | None:
        seen = False
        best = 0.0
        for index in indexes:
            point = points[index]
            if not _visible(point, 0.25):
                continue
            seen = True
            if anchor is None:
                continue
            ax, ay, az, radius, _left = anchor
            if radius <= 1e-6:
                continue
            if (az - point.z) < self.depth:
                continue
            overlap = (radius - math.hypot(point.x - ax, point.y - ay)) / radius
            if overlap > best:
                best = overlap
        if not seen:
            return None
        return best
