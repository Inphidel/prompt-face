"""Pick the text face OBS prints for the current expression.

Slots in textfaces.json are the character. Closed eyes, a blink, a smile,
a wide mouth, and an open mouth each have a slot, and the rest use neutral.
A mute flag uses the muted slot instead, with a lowercase x for the mouth
unless that slot was edited. Looking left or right uses that side of the
slot. An empty side falls back to the center.

Arms always have a mark unless a hand is on the face. A hand on one side
clears that side only. Thumbs-up, peace, point, and open replace the arm
mark on the side that makes the gesture.

A custom emote, set from the page or from face-emote, replaces the whole
line. Clearing it returns the camera face, arms included.

A relative library path is resolved from the project directory, so the
published textfaces.json can say "images" without a home directory in it.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = Path(__file__).resolve().parent / "text_faces.json"
SLOTS = (
    "absent",
    "neutral",
    "talk",
    "shout",
    "smile",
    "blink",
    "closed",
    "muted",
    "cover_l",
    "cover_r",
    "cover_both",
)
COVER_SLOTS = ("cover_l", "cover_r", "cover_both")
ARM_MARKS = {"l_up": "\\", "l_down": "_", "r_up": "/", "r_down": "_"}
GESTURE_ORDER = ("thumbsup", "peace", "point", "open")
GESTURE_DEFAULTS = {
    "thumbsup": {"left": "👍", "right": "👍"},
    "peace": {"left": "✌", "right": "✌"},
    "point": {"left": "👈", "right": "👉"},
    "open": {"left": "✋", "right": "✋"},
}
SLOT_ORDER = (
    ("closed", "eyes", "closed"),
    ("blink", "eyes", "blink"),
    ("smile", "smile", "on"),
    ("shout", "mouth", "wide"),
    ("talk", "mouth", "open"),
)
DEFAULT_TEXT = {
    "absent": "(✖╭╮✖)",
    "neutral": "(•ω•)",
    "talk": "(°o°)",
    "shout": "ಠoಠ",
    "smile": "｡◕‿◕｡",
    "blink": "◕‿↼",
    "closed": "(︶︹︺)",
    "muted": "( •x•)",
}
RULE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_TEXT = 240
MAX_PATH = 1024
DEFAULT_HOLD_MS = 500
DEFAULT_VARY_MS = 120
MIN_HOLD_MS = 40
MAX_HOLD_MS = 10_000
MAX_VARY_MS = 5_000
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
SIDES = ("left", "center", "right")
HAT_NAMES = ("a", "b", "c", "d")

# Each pair is swapped. The map has to be its own inverse so flipping twice
# lands back on the original face.
_PAIRS = (
    ("(", ")"),
    ("[", "]"),
    ("{", "}"),
    ("<", ">"),
    ("/", "\\"),
    ("╭", "╮"),
    ("╰", "╯"),
    ("┌", "┐"),
    ("└", "┘"),
    ("├", "┤"),
    ("╔", "╗"),
    ("╚", "╝"),
    ("╠", "╣"),
    ("☜", "☞"),
    ("ノ", "ヽ"),
    ("つ", "づ"),
    ("（", "）"),
    ("＜", "＞"),
    ("／", "＼"),
    ("╱", "╲"),
    ("༼", "༽"),
    ("ʕ", "ʔ"),
    ("ᶘ", "ᶅ"),
    ("↼", "⇀"),
    ("←", "→"),
    ("⇐", "⇒"),
    ("◀", "▶"),
    ("◁", "▷"),
    ("⊂", "⊃"),
    ("ε", "3"),
    ("Ɛ", "з"),
    ("ᕕ", "ᕗ"),
    ("「", "」"),
    ("『", "』"),
    ("【", "】"),
    ("〔", "〕"),
    ("〖", "〗"),
    ("｢", "｣"),
    ("‹", "›"),
    ("«", "»"),
    ("ೕ", "೨"),
    ("👈", "👉"),
)


def _mirror_table() -> dict[str, str]:
    table = {}
    for left, right in _PAIRS:
        if left == right or left in table or right in table:
            raise RuntimeError(f"mirror map clashes on {left!r} / {right!r}")
        table[left] = right
        table[right] = left
    return table


MIRROR = _mirror_table()


def mirror(text: str) -> str:
    """Flip a text face so it looks the other way.

    Characters reverse one by one. Combining marks stay their own characters
    so a second flip returns the original text.
    """
    return "".join(MIRROR.get(char, char) for char in reversed(text))


def variants(text: str) -> dict:
    """Center and right start as the typed face. Left is the flip."""
    cleaned = text.strip()
    return {
        "center": cleaned,
        "left": mirror(cleaned),
        "right": cleaned,
        "image": {"left": "", "center": "", "right": ""},
    }


def default_bindings() -> dict:
    return {
        "library": "images",
        "slots": {name: variants(DEFAULT_TEXT.get(name, "")) for name in SLOTS},
        "arms": dict(ARM_MARKS),
        "gestures": default_gestures(),
        "hats": {name: "" for name in HAT_NAMES},
        "rules": {},
    }


def _clean_path(value, label: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_PATH or "\n" in value or "\x00" in value:
        raise ValueError(f"{label} needs to be a file path")
    return value.strip()


def _clean_entry(raw, label: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} needs a left, center, and right text")
    cleaned = {}
    for side in SIDES:
        value = raw.get(side, "")
        if not isinstance(value, str) or len(value) > MAX_TEXT:
            raise ValueError(f"{label} {side} must be a short string")
        cleaned[side] = value.strip()
    images = raw.get("image") or {}
    if not isinstance(images, dict):
        raise ValueError(f"{label} image paths need to be an object")
    cleaned["image"] = {
        side: _clean_path(images.get(side, ""), f"{label} {side} image")
        for side in SIDES
    }
    return cleaned


def _clean_ms(value, label: str, default: int, low: int, high: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} needs to be a whole number of milliseconds")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{label} needs to be a whole number of milliseconds")
    number = int(value)
    if number < low or number > high:
        raise ValueError(f"{label} needs to be from {low} to {high}")
    return number


def _clean_mark(value, label: str, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or len(value) > 4 or "\n" in value or "\x00" in value:
        raise ValueError(f"{label} needs to be a short character")
    return value


def default_gestures() -> dict:
    items = []
    for name in GESTURE_ORDER:
        base = GESTURE_DEFAULTS[name]
        items.append({
            "id": name,
            "enabled": True,
            "left": base["left"],
            "right": base["right"],
        })
    return {"enabled": True, "items": items}


def _gesture_side(item: dict, name: str, side: str) -> str:
    """Text for one side. A single older mark fills a side that was not set."""
    base = GESTURE_DEFAULTS[name][side]
    mark = item.get("mark")
    if side not in item or item.get(side) is None:
        if isinstance(mark, str) and mark:
            chosen = base if side == "right" and mark == GESTURE_DEFAULTS[name]["left"] else mark
        else:
            chosen = base
        return _clean_mark(chosen, f"{name} {side}", base)
    value = item.get(side)
    if isinstance(value, str) and value:
        return _clean_mark(value, f"{name} {side}", base)
    other = "right" if side == "left" else "left"
    if isinstance(mark, str) and mark and not item.get(other):
        chosen = base if side == "right" and mark == GESTURE_DEFAULTS[name]["left"] else mark
        return _clean_mark(chosen, f"{name} {side}", base)
    return ""


def _clean_gestures(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    found = {}
    for item in raw.get("items") or []:
        if isinstance(item, dict) and item.get("id") in GESTURE_DEFAULTS:
            found[item["id"]] = item
    items = []
    for name in GESTURE_ORDER:
        item = found.get(name) or {}
        items.append({
            "id": name,
            "enabled": bool(item.get("enabled", True)),
            "left": _gesture_side(item, name, "left"),
            "right": _gesture_side(item, name, "right"),
        })
    return {"enabled": bool(raw.get("enabled", True)), "items": items}


def _clean_arms(raw) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("arm characters need to be an object")
    return {key: _clean_mark(raw.get(key), f"arm {key}", default) for key, default in ARM_MARKS.items()}


def _clean_hats(raw) -> dict:
    """One picture for each hat slot. Empty means that slot has no picture."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("hats need to be an object")
    cleaned = {}
    for name in HAT_NAMES:
        value = raw.get(name, "")
        if not isinstance(value, str):
            raise ValueError(f"hat {name} needs to be a file path")
        cleaned[name] = _clean_path(value, f"hat {name}")
    return cleaned


def _clean_library(value) -> str:
    if value is None:
        return ""
    return _clean_path(value, "image folder")


def validate_textfaces(payload: dict) -> dict:
    """Return cleaned face text, or raise ValueError."""
    if not isinstance(payload, dict):
        raise ValueError("expected the face text object")
    slots = payload.get("slots")
    if not isinstance(slots, dict):
        raise ValueError("expected a slots object")
    cleaned_slots = {}
    for name in SLOTS:
        if name not in slots:
            if name in COVER_SLOTS:
                cleaned_slots[name] = _clean_entry({}, name)
                continue
            if name == "muted":
                cleaned_slots[name] = _clean_entry(variants(DEFAULT_TEXT["muted"]), name)
                continue
            raise ValueError(f"missing the {name} face")
        cleaned = _clean_entry(slots[name], name)
        if name == "absent":
            cleaned["hold_ms"] = _clean_ms(
                slots[name].get("hold_ms"), "absent hold", DEFAULT_HOLD_MS, MIN_HOLD_MS, MAX_HOLD_MS
            )
            cleaned["vary_ms"] = _clean_ms(
                slots[name].get("vary_ms"), "absent variation", DEFAULT_VARY_MS, 0, MAX_VARY_MS
            )
        cleaned_slots[name] = cleaned
    rules = payload.get("rules") or {}
    if not isinstance(rules, dict) or len(rules) > 40:
        raise ValueError("rule faces must be a short object")
    cleaned_rules = {}
    for rule_id, raw in rules.items():
        if not isinstance(rule_id, str) or not RULE_RE.match(rule_id):
            raise ValueError(f"unknown rule face {rule_id!r}")
        cleaned_rules[rule_id] = _clean_entry(raw, rule_id)
    library = payload.get("library", default_bindings()["library"])
    return {
        "library": _clean_library(library),
        "arms": _clean_arms(payload.get("arms")),
        "gestures": _clean_gestures(payload.get("gestures")),
        "hats": _clean_hats(payload.get("hats")),
        "slots": cleaned_slots,
        "rules": cleaned_rules,
    }


def load_textfaces(path: Path) -> dict:
    if not path.exists():
        return default_bindings()
    return validate_textfaces(json.loads(path.read_text()))


def save_textfaces(path: Path, payload: dict) -> dict:
    cleaned = validate_textfaces(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)
    return cleaned


def load_catalog() -> dict:
    if not CATALOG_PATH.exists():
        return {"faces": []}
    return json.loads(CATALOG_PATH.read_text())


def direction_of(state: dict) -> str:
    look = state.get("look")
    if look in ("left", "right"):
        return look
    return "center"


def _images(entry: dict | None) -> dict:
    images = (entry or {}).get("image")
    return images if isinstance(images, dict) else {}


def _pick(entry: dict | None, direction: str, library: str) -> tuple[str, str, str]:
    """Return the text, image path, and side for this look direction.

    A side is usable when it has text or an image file. The image on that
    side replaces the text. An empty side falls through to center, then the
    other side.
    """
    entry = entry or {}
    images = _images(entry)
    order = [direction]
    for side in SIDES:
        if side not in order:
            order.append(side)
    for side in order:
        text = entry.get(side) if isinstance(entry.get(side), str) else ""
        image = images.get(side) if isinstance(images.get(side), str) else ""
        text = (text or "").strip()
        image = (image or "").strip()
        if text or resolve_image(image, library):
            return text, image, side
    return "", "", direction


def _has_face(entry: dict | None, library: str) -> bool:
    text, image, _side = _pick(entry, "center", library)
    return bool(text or resolve_image(image, library))


def library_path(library: str) -> Path:
    """Resolve the image folder. A relative folder lives in the project."""
    path = Path(library or "").expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path


def resolve_image(path: str, library: str) -> str:
    """Return an absolute image path when the file exists."""
    text = (path or "").strip()
    if not text:
        return ""
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = library_path(library) / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return ""
    if resolved.suffix.lower() not in IMAGE_EXT or not resolved.is_file():
        return ""
    return str(resolved)


def list_images(library: str) -> list[dict]:
    """Image files in the library folder. The folder has to sit inside the home directory."""
    if not library:
        return []
    home = Path.home().resolve()
    try:
        root = library_path(library).resolve(strict=True)
    except OSError:
        return []
    if root != home and home not in root.parents:
        return []
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.iterdir()):
        if path.name.startswith(".") or not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_EXT:
            continue
        found.append({"name": path.name, "path": str(path.resolve())})
        if len(found) >= 400:
            break
    return found


def safe_image(path: str) -> Path | None:
    """An image file inside the home directory, or nothing."""
    if not path or "\x00" in path or len(path) > MAX_PATH:
        return None
    home = Path.home().resolve()
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file() or resolved.suffix.lower() not in IMAGE_EXT:
        return None
    if resolved != home and home not in resolved.parents:
        return None
    try:
        if resolved.stat().st_size > 20_000_000:
            return None
    except OSError:
        return None
    return resolved


def absent_frames(entry: dict | None) -> list[tuple[str, str, str]]:
    """The three absent lines, in order, skipping blanks."""
    entry = entry or {}
    images = _images(entry)
    frames = []
    for side in SIDES:
        text = entry.get(side) if isinstance(entry.get(side), str) else ""
        image = images.get(side) if isinstance(images.get(side), str) else ""
        text = (text or "").strip()
        image = (image or "").strip()
        if text or image:
            frames.append((text, image, side))
    return frames


def frame_hold_ms(hold_ms: int, vary_ms: int) -> int:
    """How long this frame stays up, within hold ± variation."""
    low = max(MIN_HOLD_MS, hold_ms - max(vary_ms, 0))
    high = max(low, hold_ms + max(vary_ms, 0))
    if low == high:
        return low
    return random.randint(low, high)


def cover_name(state: dict) -> str:
    """The painted face for a hand on the face, or nothing."""
    left = state.get("hand_l") == "face"
    right = state.get("hand_r") == "face"
    if left and right:
        return "cover_both"
    if left:
        return "cover_l"
    if right:
        return "cover_r"
    return ""


def arm_glyph(value: str, arms: dict, side: str) -> str:
    """The character for one arm. Anything that is not up is the shoulder."""
    if value == "up":
        raised = arms.get(f"{side}_up") or ""
        if raised:
            return raised
    shoulder = arms.get(f"{side}_down") or ""
    return shoulder or "_"


def _matches_gesture(name: str, state: dict, side: str) -> bool:
    shape = state.get(f"shape_{side}")
    thumb = state.get(f"thumb_{side}")
    index = state.get(f"index_{side}")
    middle = state.get(f"middle_{side}")
    ring = state.get(f"ring_{side}")
    pinky = state.get(f"pinky_{side}")
    if name == "thumbsup":
        return shape == "fist" and thumb == "up"
    if name == "peace":
        return index == "up" and middle == "up" and ring == "down" and pinky == "down"
    if name == "point":
        return shape == "point"
    if name == "open":
        return shape == "open"
    return False


def gesture_glyph(state: dict, gestures: dict, side: str) -> str:
    """The hand picture for this side, when gestures are on and one matches."""
    if not gestures or not gestures.get("enabled", True):
        return ""
    for item in gestures.get("items") or []:
        if not item.get("enabled", True):
            continue
        if not _matches_gesture(item.get("id"), state, side):
            continue
        return item.get("left" if side == "l" else "right") or ""
    return ""


def clean_line(value) -> str | None:
    """A custom emote. Empty, or None, returns the face to the camera."""
    if value is None:
        return None
    if not isinstance(value, str) or "\n" in value or "\x00" in value or len(value) > MAX_TEXT:
        raise ValueError("the emote needs to be one line")
    if value == "":
        return None
    return value


def side_mark(state: dict, arms: dict, gestures: dict, side: str, slot: str) -> str:
    """Hand picture, or the arm. A hand on the face clears that side."""
    del slot
    if state.get(f"hand_{side}") == "face":
        return ""
    picture = gesture_glyph(state, gestures, side)
    if picture:
        return picture
    return arm_glyph(state.get(f"arm_{side}"), arms, side)


def wrap_arms(text: str, state: dict, arms: dict, gestures: dict, slot: str) -> tuple[str, str, str]:
    """Return the full line, the left mark, and the right mark."""
    left = side_mark(state, arms, gestures, "l", slot)
    right = side_mark(state, arms, gestures, "r", slot)
    return f"{left}{text}{right}", left, right


def choose(state: dict, bindings: dict) -> tuple[str, str, str, str]:
    """Return the face text, slot, image path, and side."""
    slots = bindings.get("slots") or {}
    library = bindings.get("library") or ""
    if state.get("muted") == "on":
        direction = "center" if state.get("face") == "absent" else direction_of(state)
        text, image, side = _pick(slots.get("muted"), direction, library)
        return text, "muted", resolve_image(image, library), side
    cover = cover_name(state)
    if cover and _has_face(slots.get(cover), library):
        direction = "center" if state.get("face") == "absent" else direction_of(state)
        text, image, side = _pick(slots.get(cover), direction, library)
        return text, cover, resolve_image(image, library), side
    if state.get("face") == "absent":
        text, image, side = _pick(slots.get("absent"), "center", library)
        return text, "absent", resolve_image(image, library), side
    rules = bindings.get("rules") or {}
    for rule_id in state.get("active") or []:
        entry = rules.get(rule_id)
        if _has_face(entry, library):
            text, image, side = _pick(entry, direction_of(state), library)
            return text, f"rule:{rule_id}", resolve_image(image, library), side
    for slot, field, value in SLOT_ORDER:
        if state.get(field) == value:
            text, image, side = _pick(slots.get(slot), direction_of(state), library)
            return text, slot, resolve_image(image, library), side
    text, image, side = _pick(slots.get("neutral"), direction_of(state), library)
    return text, "neutral", resolve_image(image, library), side


class TextBoard:
    """Reload textfaces.json and stamp the chosen face onto each state."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._mtime = None
        self._bindings = default_bindings()
        self._warned = False
        self._absent_playing = False
        self._absent_index = 0
        self._absent_until = 0.0
        self._emote = None
        self._shown = ""
        self._expression = "closed"
        self._hat = "none"
        self.reload()

    def set_line(self, text: str | None) -> dict:
        """Replace the whole line until it is cleared. The camera face stays underneath."""
        cleaned = clean_line(text)
        with self._lock:
            if cleaned is None and self._shown == self._emote:
                self._shown = ""
            self._emote = cleaned
            if cleaned is not None:
                self._shown = cleaned
            return {"text": self._emote}

    def set_hat(self, slot: str) -> str:
        """Choose hat a, b, c, d, or none. The text face is left alone."""
        word = (slot or "").strip().lower()
        if word in ("", "none", "off", "clear"):
            word = "none"
        elif word not in HAT_NAMES:
            raise ValueError("hat needs a, b, c, d, or none")
        with self._lock:
            self._hat = word
            return word

    def hat_view(self) -> dict:
        """The selected slot and the picture file, when that file exists."""
        with self._lock:
            slot = self._hat
            library = self._bindings.get("library") or ""
            stored = ""
            if slot in HAT_NAMES:
                stored = str((self._bindings.get("hats") or {}).get(slot) or "")
        picture = resolve_image(stored, library) if slot in HAT_NAMES else ""
        return {"slot": slot, "file": picture}

    def line_status(self) -> dict:
        """The custom emote, the line last drawn, and the camera mouth."""
        with self._lock:
            shown = self._shown if self._emote is None else self._emote
            return {"text": self._emote, "line": shown, "expression": self._expression}

    def reload(self) -> None:
        with self._lock:
            try:
                if not self.path.exists():
                    bindings = default_bindings()
                    mtime = None
                else:
                    mtime = self.path.stat().st_mtime
                    if mtime == self._mtime:
                        return
                    bindings = load_textfaces(self.path)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                if not self._warned:
                    print(f"text faces were not loaded: {error}", flush=True)
                    self._warned = True
                return
            self._warned = False
            self._mtime = mtime
            self._bindings = bindings

    def _absent_frame(self, bindings: dict) -> tuple[str, str, str]:
        entry = (bindings.get("slots") or {}).get("absent") or {}
        library = bindings.get("library") or ""
        frames = absent_frames(entry)
        if not frames:
            self._absent_playing = False
            return "", "", "left"
        now = time.monotonic()
        if not self._absent_playing or now >= self._absent_until or self._absent_index >= len(frames):
            if self._absent_playing and self._absent_index < len(frames):
                self._absent_index = (self._absent_index + 1) % len(frames)
            else:
                self._absent_index = 0
            self._absent_playing = True
            hold = frame_hold_ms(int(entry.get("hold_ms") or DEFAULT_HOLD_MS), int(entry.get("vary_ms") or 0))
            self._absent_until = now + hold / 1000
        text, image, side = frames[self._absent_index]
        return text, resolve_image(image, library), side

    def apply(self, state: dict) -> dict:
        self.reload()
        with self._lock:
            bindings = self._bindings
            emote = self._emote
        if emote is not None:
            self._absent_playing = False
            state["text"] = emote
            state["face_text"] = emote
            state["arm_l_text"] = ""
            state["arm_r_text"] = ""
            state["emote"] = emote
            state["text_slot"] = "emote"
            state["image"] = ""
            state["image_side"] = "center"
            self._remember(state)
            return state
        text, slot, image, side = choose(state, bindings)
        if slot == "absent":
            text, image, side = self._absent_frame(bindings)
        else:
            self._absent_playing = False
        line, left, right = wrap_arms(
            text,
            state,
            bindings.get("arms") or ARM_MARKS,
            bindings.get("gestures") or default_gestures(),
            slot,
        )
        state["text"] = line
        state["face_text"] = text
        state["arm_l_text"] = left
        state["arm_r_text"] = right
        state["emote"] = ""
        state["text_slot"] = slot
        state["image"] = image
        state["image_side"] = side
        self._remember(state)
        return state

    def _remember(self, state: dict) -> None:
        """Keep the latest painted line for the command port."""
        expression = state.get("mouth") if state.get("mouth") in ("closed", "open", "wide") else "closed"
        with self._lock:
            self._shown = state.get("text") or ""
            self._expression = expression
