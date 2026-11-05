"""Turn reporter values into named gestures.

A rule lists the returns it cares about. When those values hold, the rule
publishes its own on/off field, and that field is what OBS hears.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

VOCAB = {
    "face": ["present", "absent"],
    "mouth": ["closed", "open", "wide"],
    "smile": ["off", "on"],
    "eyes": ["open", "blink", "closed"],
    "look": ["left", "center", "right"],
    "head": ["up", "level", "down"],
    "arm_l": ["missing", "down", "up"],
    "arm_r": ["missing", "down", "up"],
    "hand_l": ["off", "face"],
    "hand_r": ["off", "face"],
    "place_l": ["none", "brow", "eyes", "nose", "mouth"],
    "place_r": ["none", "brow", "eyes", "nose", "mouth"],
    "facing_l": ["none", "palm", "back", "edge"],
    "facing_r": ["none", "palm", "back", "edge"],
    "shape_l": ["none", "open", "fist", "point"],
    "shape_r": ["none", "open", "fist", "point"],
    "thumb_l": ["down", "up"],
    "thumb_r": ["down", "up"],
    "index_l": ["down", "up"],
    "index_r": ["down", "up"],
    "middle_l": ["down", "up"],
    "middle_r": ["down", "up"],
    "ring_l": ["down", "up"],
    "ring_r": ["down", "up"],
    "pinky_l": ["down", "up"],
    "pinky_r": ["down", "up"],
}

# Keys that are not reporter returns, so a rule cannot require them.
META = {"scores", "t", "changed", "rules", "active", "text", "text_slot", "image", "image_side"}
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,39}$")
FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def slug(name: str) -> str:
    folded = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return folded[:32]


def validate_rules(payload: dict) -> list[dict]:
    """Return a clean rule list, or raise ValueError."""
    if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
        raise ValueError("expected an object with a rules list")
    raw_rules = payload["rules"]
    if len(raw_rules) > 40:
        raise ValueError("at most 40 rules")
    cleaned = []
    seen = set(VOCAB) | META
    for raw in raw_rules:
        if not isinstance(raw, dict):
            raise ValueError("a rule must be an object")
        name = raw.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name.strip()):
            raise ValueError("a rule name must start with a letter and stay short")
        rule_id = slug(name)
        if not rule_id or rule_id in seen:
            raise ValueError(f"the name {name!r} clashes with another rule or a return")
        seen.add(rule_id)
        try:
            hold = int(raw.get("hold", 3))
        except (TypeError, ValueError) as error:
            raise ValueError("hold must be a whole number") from error
        if hold < 1 or hold > 30:
            raise ValueError("hold must be from 1 to 30 frames")
        when = raw.get("when")
        if not isinstance(when, list) or not when or len(when) > 8:
            raise ValueError(f"{name} needs between 1 and 8 conditions")
        conditions = []
        used_fields = set()
        for condition in when:
            if not isinstance(condition, dict):
                raise ValueError("a condition must be an object")
            field = condition.get("field")
            equals = condition.get("equals")
            if not isinstance(field, str) or not FIELD_RE.match(field) or field in META:
                raise ValueError(f"unknown return {field!r}")
            if field in used_fields:
                raise ValueError(f"{name} lists {field} twice")
            used_fields.add(field)
            if not isinstance(equals, str) or not equals.strip() or len(equals) > 32:
                raise ValueError("a condition needs a short value")
            conditions.append({"field": field, "equals": equals.strip()})
        cleaned.append({
            "id": rule_id,
            "name": name.strip(),
            "enabled": bool(raw.get("enabled", True)),
            "hold": hold,
            "when": conditions,
        })
    return cleaned


def load_rules(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return validate_rules(json.loads(path.read_text()))


def save_rules(path: Path, payload: dict) -> list[dict]:
    cleaned = validate_rules(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps({"rules": cleaned}, indent=2) + "\n")
    temporary.replace(path)
    return cleaned


class Composer:
    """Watch rules.json and fold matching rules into the state dict."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._mtime = None
        self._rules: list[dict] = []
        self._holds: dict[str, dict] = {}
        self._values: dict[str, str] = {}
        self._signatures: dict[str, str] = {}
        self._warned = False

    def reload(self) -> None:
        with self._lock:
            try:
                if not self.path.exists():
                    rules = []
                    mtime = None
                else:
                    mtime = self.path.stat().st_mtime
                    if mtime == self._mtime:
                        return
                    rules = load_rules(self.path)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                if not self._warned:
                    print(f"rules file was not loaded: {error}", flush=True)
                    self._warned = True
                return
            self._warned = False
            self._mtime = mtime
            self._rules = rules
            live = {rule["id"] for rule in rules}
            signatures = {}
            for rule in rules:
                signature = json.dumps(
                    {"enabled": rule["enabled"], "hold": rule["hold"], "when": rule["when"]},
                    sort_keys=True,
                )
                signatures[rule["id"]] = signature
                if self._signatures.get(rule["id"]) != signature:
                    self._holds.pop(rule["id"], None)
            self._signatures = signatures
            for gone in [rule_id for rule_id in self._holds if rule_id not in live]:
                self._holds.pop(gone, None)

    def apply(self, state: dict) -> dict:
        self.reload()
        with self._lock:
            rules = list(self._rules)
        changes = []
        debug = []
        active = []
        live_ids = set()
        for rule in rules:
            live_ids.add(rule["id"])
            checks = []
            matched = True
            for condition in rule["when"]:
                got = state.get(condition["field"])
                ok = got == condition["equals"]
                matched = matched and ok
                checks.append({
                    "field": condition["field"],
                    "equals": condition["equals"],
                    "got": got,
                    "ok": ok,
                })
            if not rule["enabled"]:
                matched = False
                self._holds[rule["id"]] = {"on": False, "count": 0}
                on = False
            else:
                on = self._advance(rule["id"], matched, rule["hold"])
            label = "on" if on else "off"
            state[rule["id"]] = label
            if on:
                active.append(rule["id"])
            if self._values.get(rule["id"]) != label:
                if rule["id"] in self._values or label == "on":
                    changes.append(rule["id"])
                self._values[rule["id"]] = label
            debug.append({
                "id": rule["id"],
                "name": rule["name"],
                "enabled": rule["enabled"],
                "hold": rule["hold"],
                "match": matched and rule["enabled"],
                "on": on,
                "when": checks,
            })
        for gone in [rule_id for rule_id in self._values if rule_id not in live_ids]:
            if self._values[gone] == "on":
                state[gone] = "off"
                changes.append(gone)
            del self._values[gone]
        state["active"] = active
        state["rules"] = debug
        state["changed"] = list(state.get("changed") or []) + changes
        return state

    def _advance(self, rule_id: str, matched: bool, hold: int) -> bool:
        entry = self._holds.setdefault(rule_id, {"on": False, "count": 0})
        if matched == entry["on"]:
            entry["count"] = 0
            return entry["on"]
        entry["count"] += 1
        if entry["count"] >= hold:
            entry["on"] = matched
            entry["count"] = 0
        return entry["on"]
