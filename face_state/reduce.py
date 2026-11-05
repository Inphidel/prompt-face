"""Turn blendshape scores into stable named states.

Each label uses an enter line, a lower exit line, and a short hold so a
value sitting on the boundary does not flicker.
"""

from __future__ import annotations

from face_state.hand import raw_label
from face_state.voice import mouth_from_level

FIELDS = ("face", "mouth", "smile", "eyes", "look", "head", "arm_l", "arm_r", "hand_l", "hand_r")
HAND_PARTS = ("place", "facing", "shape", "thumb", "index", "middle", "ring", "pinky")
DETAIL = tuple(f"{part}_{side}" for side in ("l", "r") for part in HAND_PARTS)
PART_REST = {
    "place": "none",
    "facing": "none",
    "shape": "none",
    "thumb": "down",
    "index": "down",
    "middle": "down",
    "ring": "down",
    "pinky": "down",
}
PART_VALUES = {
    "place": {"none", "brow", "eyes", "nose", "mouth"},
    "facing": {"none", "palm", "back", "edge"},
    "shape": {"none", "open", "fist", "point"},
    "thumb": {"down", "up"},
    "index": {"down", "up"},
    "middle": {"down", "up"},
    "ring": {"down", "up"},
    "pinky": {"down", "up"},
}
REST = {
    "face": "absent",
    "mouth": "closed",
    "smile": "off",
    "eyes": "open",
    "look": "center",
    "head": "level",
    "arm_l": "missing",
    "arm_r": "missing",
    "hand_l": "off",
    "hand_r": "off",
}


class _Hold:
    """Count consecutive frames that want a new label before accepting it."""

    def __init__(self, initial: str, hold: int):
        self.value = initial
        self.hold = hold
        self._pending = None
        self._count = 0

    def set_now(self, value: str) -> None:
        self.value = value
        self._pending = None
        self._count = 0

    def propose(self, target: str) -> str:
        if target == self.value:
            self._pending = None
            self._count = 0
            return self.value
        if target == self._pending:
            self._count += 1
        else:
            self._pending = target
            self._count = 1
        if self._count >= self.hold:
            self.value = target
            self._pending = None
            self._count = 0
        return self.value


class Reducer:
    def __init__(
        self,
        thresholds: dict,
        face_hold: int,
        pose: dict | None = None,
        hands: dict | None = None,
        voice: dict | None = None,
    ):
        t = thresholds
        self.open = t["mouth_open"]
        self.wide = t["mouth_wide"]
        voice = voice or {}
        self.voice_quiet = float(voice.get("quiet", 0.004))
        self.voice_loud = float(voice.get("loud", 0.03))
        self.smile = t["smile"]
        self.blink = t["blink"]
        self.eyes_closed_hold = int(t["eyes_closed_hold"])
        self.look = t["look"]
        self.head = t["head"]
        pose = pose or {}
        hands = hands or {}
        self.arm = pose.get("arm", {"enter": 0.22, "exit": 0.05, "hold": 3})
        self.hand = pose.get("hand", {"enter": 0.20, "exit": 0.0, "hold": 3})
        self.pose_hold = int(pose.get("hold", 5))
        self.hands_hold = int(hands.get("hold", 5))
        self.label_hold = int(hands.get("label_hold", 3))
        self._face = _Hold("absent", face_hold)
        self._mouth = _Hold("closed", int(self.open["hold"]))
        self._smile = _Hold("off", int(self.smile["hold"]))
        self._eyes = _Hold("open", 1)
        self._look = _Hold("center", int(self.look["hold"]))
        self._head = _Hold("level", int(self.head["hold"]))
        self._arm_l = _Hold("missing", int(self.arm["hold"]))
        self._arm_r = _Hold("missing", int(self.arm["hold"]))
        self._hand_l = _Hold("off", int(self.hand["hold"]))
        self._hand_r = _Hold("off", int(self.hand["hold"]))
        self._detail = {
            f"{part}_{side}": _Hold(PART_REST[part], self.label_hold)
            for side in ("l", "r")
            for part in HAND_PARTS
        }
        self._shut_frames = 0
        self._blink_left = 0
        self._pose_missing = 0
        self._hands_mode = False
        self._hand_missing = {"hand_l": 0, "hand_r": 0}
        self._side_missing = {"l": 0, "r": 0}
        self._published = dict(REST)
        for name, hold in self._detail.items():
            self._published[name] = hold.value

    def _mouth_target(self, jaw: float) -> str:
        state = self._mouth.value
        if state == "closed":
            if jaw >= self.wide["enter"]:
                return "wide"
            if jaw >= self.open["enter"]:
                return "open"
            return "closed"
        if state == "open":
            if jaw >= self.wide["enter"]:
                return "wide"
            if jaw <= self.open["exit"]:
                return "closed"
            return "open"
        if jaw <= self.open["exit"]:
            return "closed"
        if jaw <= self.wide["exit"]:
            return "open"
        return "wide"

    def _axis_target(self, state: str, value: float, enter: float, exit: float, low: str, neutral: str, high: str) -> str:
        if state == neutral:
            if value >= enter:
                return high
            if value <= -enter:
                return low
            return neutral
        if state == high:
            if value <= -enter:
                return low
            if value < exit:
                return neutral
            return high
        if value >= enter:
            return high
        if value > -exit:
            return neutral
        return low

    def _rest_face(self) -> None:
        self._mouth.set_now("closed")
        self._smile.set_now("off")
        self._eyes.set_now("open")
        self._look.set_now("center")
        self._head.set_now("level")
        self._shut_frames = 0
        self._blink_left = 0

    def _level_target(self, state: str, value: float, band: dict, on: str, off: str) -> str:
        """Turn a score into on or off. The exit line is below the enter line."""
        if state == on:
            # A score of 0 must be able to leave. Exit 0 with ">=" never would.
            return on if value > float(band["exit"]) else off
        return on if value >= float(band["enter"]) else off

    def _apply_pose(self, pose: dict | None) -> dict:
        if pose is None:
            self._pose_missing += 1
            if self._pose_missing >= self.pose_hold:
                self._arm_l.set_now("missing")
                self._arm_r.set_now("missing")
                if not self._hands_mode:
                    self._hand_l.set_now("off")
                    self._hand_r.set_now("off")
            return {}
        self._pose_missing = 0
        arms = (("arm_l", self._arm_l, "up", "down"), ("arm_r", self._arm_r, "up", "down"))
        scores = {}
        for name, hold, on, off in arms:
            value = pose.get(name)
            scores[name] = None if value is None else round(float(value), 2)
            if value is None:
                hold.propose("missing")
            else:
                seen = hold.value if hold.value in (on, off) else off
                hold.propose(self._level_target(seen, value, self.arm, on, off))
        if self._hands_mode:
            return scores
        hands = (("hand_l", self._hand_l, "face", "off"), ("hand_r", self._hand_r, "face", "off"))
        for name, hold, on, off in hands:
            value = pose.get(name)
            scores[name] = None if value is None else round(float(value), 2)
            if value is None:
                self._hand_missing[name] += 1
                if self._hand_missing[name] >= self.pose_hold:
                    hold.set_now(off)
                continue
            self._hand_missing[name] = 0
            hold.propose(self._level_target(hold.value, value, self.hand, on, off))
        return scores

    def _rest_side(self, side: str) -> None:
        for part in HAND_PARTS:
            self._detail[f"{part}_{side}"].set_now(PART_REST[part])
        hold = self._hand_l if side == "l" else self._hand_r
        hold.set_now("off")

    def _apply_hands(self, hands) -> dict:
        """Hold place, facing, shape, and fingers. hand_l is face while place is on the face."""
        if not isinstance(hands, dict):
            hands = {}
        raw = {}
        for side in ("l", "r"):
            reading = hands.get(side)
            raw[f"raw{side.upper()}"] = raw_label(reading)
            if not reading:
                self._side_missing[side] += 1
                if self._side_missing[side] >= self.hands_hold:
                    self._rest_side(side)
                continue
            self._side_missing[side] = 0
            for part in HAND_PARTS:
                value = reading.get(part)
                if value not in PART_VALUES[part]:
                    value = PART_REST[part]
                self._detail[f"{part}_{side}"].propose(value)
            place = self._detail[f"place_{side}"].value
            hold = self._hand_l if side == "l" else self._hand_r
            hold.set_now("face" if place != "none" else "off")
        return raw

    def _eyes_target(self, blink_score: float) -> str:
        """A short close becomes blink when the eyes open. A long close is closed, with no blink after it."""
        if self._blink_left > 0 and blink_score < self.blink["enter"]:
            self._blink_left -= 1
            return "blink" if self._blink_left > 0 else "open"
        if blink_score >= self.blink["enter"]:
            self._blink_left = 0
            self._shut_frames += 1
            if self._shut_frames >= self.eyes_closed_hold:
                return "closed"
            return "closed" if self._eyes.value == "closed" else "open"
        if blink_score <= self.blink["exit"]:
            streak = self._shut_frames
            self._shut_frames = 0
            if self._eyes.value == "closed":
                return "open"
            if streak >= int(self.blink["hold"]):
                self._blink_left = 3
                return "blink"
            return "open"
        if self._eyes.value == "closed":
            return "closed"
        return "open"

    def update(self, sample: dict | None, mouth_source: str = "jaw", voice: float | None = None) -> dict:
        sample = sample or {}
        use_hands = isinstance(sample, dict) and "hands" in sample
        if use_hands:
            self._hands_mode = True
        face_sample = sample.get("face") if isinstance(sample, dict) else None
        # A bare face sample from older callers has jawOpen at the top.
        if face_sample is None and isinstance(sample, dict) and "jawOpen" in sample:
            face_sample = sample
            pose_sample = None
        else:
            pose_sample = sample.get("pose") if isinstance(sample, dict) else None

        face = self._face.propose("absent" if face_sample is None else "present")
        if face == "absent":
            self._rest_face()
            current = {
                "face": "absent",
                "mouth": "closed",
                "smile": "off",
                "eyes": "open",
                "look": "center",
                "head": "level",
            }
            scores = {}
        elif face_sample is None:
            current = {name: self._published[name] for name in ("face", "mouth", "smile", "eyes", "look", "head")}
            scores = {}
        else:
            jaw = face_sample["jawOpen"]
            smile = max(face_sample["mouthSmileLeft"], face_sample["mouthSmileRight"])
            blink = max(face_sample["eyeBlinkLeft"], face_sample["eyeBlinkRight"])
            yaw = face_sample["yaw"] * float(self.look["yaw_sign"])
            pitch = face_sample["pitch"] * float(self.head["pitch_sign"])
            # Sound mode blinks the mouth from microphone loudness.
            # Until a level arrives, the jaw blendshape is used instead.
            if mouth_source == "sound" and voice is not None:
                self._mouth.hold = 1
                self._mouth.propose(mouth_from_level(voice, self._mouth.value, self.voice_quiet, self.voice_loud))
            else:
                self._mouth.hold = int(self.wide["hold"] if self._mouth_target(jaw) == "wide" else self.open["hold"])
                self._mouth.propose(self._mouth_target(jaw))
            smile_on = smile >= (self.smile["exit"] if self._smile.value == "on" else self.smile["enter"])
            self._smile.propose("on" if smile_on else "off")
            self._eyes.propose(self._eyes_target(blink))
            self._look.propose(self._axis_target(
                self._look.value, yaw, self.look["enter"], self.look["exit"], "right", "center", "left",
            ))
            self._head.propose(self._axis_target(
                self._head.value, pitch, self.head["enter"], self.head["exit"], "down", "level", "up",
            ))
            current = {
                "face": "present",
                "mouth": self._mouth.value,
                "smile": self._smile.value,
                "eyes": self._eyes.value,
                "look": self._look.value,
                "head": self._head.value,
            }
            scores = {
                "jawOpen": round(jaw, 3),
                "voice": None if voice is None else round(float(voice), 4),
                "smile": round(smile, 3),
                "blinkLeft": round(face_sample["eyeBlinkLeft"], 3),
                "blinkRight": round(face_sample["eyeBlinkRight"], 3),
                "yaw": round(yaw, 1),
                "pitch": round(pitch, 1),
            }

        pose_scores = self._apply_pose(pose_sample)
        raw_scores = self._apply_hands(sample.get("hands")) if use_hands else {}
        current["arm_l"] = self._arm_l.value
        current["arm_r"] = self._arm_r.value
        current["hand_l"] = self._hand_l.value
        current["hand_r"] = self._hand_r.value
        for name, hold in self._detail.items():
            current[name] = hold.value
        if pose_scores:
            scores["armL"] = pose_scores["arm_l"]
            scores["armR"] = pose_scores["arm_r"]
            if "hand_l" in pose_scores:
                scores["handL"] = pose_scores["hand_l"]
                scores["handR"] = pose_scores["hand_r"]
        if raw_scores:
            scores.update(raw_scores)
        current["scores"] = scores or None

        tracked = (*FIELDS, *DETAIL)
        changed = [name for name in tracked if current[name] != self._published[name]]
        self._published = {name: current[name] for name in tracked}
        current["changed"] = changed
        return current
