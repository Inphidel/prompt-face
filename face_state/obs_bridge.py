"""Push face states into OBS.

The scene named in obs.json (Avatar, unless that file says otherwise) gets
one image source, Prompt. The face is painted in the middle of that image
at a fixed font size. Once the source is centered and has no fit-to-box
bounds, later connections leave its position, scale, and rotation alone.
Stretch or rotate it in OBS. That is the only size change.

A second image, Hat, lives in its own scene, Prompt Hat. It is painted
the same way as the face, from the left, center, or right text of the
chosen hat. Only the look picks that side. Arms stay on Prompt. The
bridge does not move a hat that has already been placed.

OBS does not reload an image when the path stays the same, so the bridge
alternates panel/prompt-a.png and panel/prompt-b.png, and the hat uses
panel/hat-a.png and panel/hat-b.png.

Each changed state is also sent to Advanced Scene Switcher as a message
such as "mouth=open" or "muted=on", which a macro can match. The websocket
password stays in ~/.config/face-state/obs.json, outside this project.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import uuid
from pathlib import Path

import websockets

from face_state.paint import paint_panel
from face_state.reduce import DETAIL, FIELDS
from face_state.textface import HAT_NAMES

SCENE = "Avatar"
FACE_TEXT = "Face text"
PANEL = "Prompt"
HAT_SCENE = "Prompt Hat"
HAT = "Hat"
FACE_FONT = "Face Text"
# A 1 pixel clear PNG, so a new hat source has a file before a picture is chosen.
BLANK_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
ROOT = Path(__file__).resolve().parent.parent
PANEL_DIR = ROOT / "panel"
OBS_CONFIG = Path.home() / ".config" / "face-state" / "obs.json"
TRACKER_URL = "ws://127.0.0.1:8770/state"
ALIGN_LEFT = 1
ALIGN_RIGHT = 2
ALIGN_TOP = 4
ALIGN_BOTTOM = 8


def load_obs_config() -> dict:
    """Read the OBS websocket settings. The password is never printed."""
    if not OBS_CONFIG.exists():
        raise SystemExit(
            "OBS settings are missing. Copy obs.example.json to "
            "~/.config/face-state/obs.json and set the websocket password "
            "from Tools, WebSocket Server Settings."
        )
    with OBS_CONFIG.open() as handle:
        return json.load(handle)


def configure_names(config: dict) -> None:
    """Scene and panel names. Defaults keep an existing Avatar / Prompt setup."""
    global SCENE, PANEL
    scene = config.get("scene")
    panel = config.get("panel")
    if isinstance(scene, str) and scene.strip():
        SCENE = scene.strip()
    if isinstance(panel, str) and panel.strip():
        PANEL = panel.strip()


def obs_rgba(value, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """OBS packs color as AABBGGRR in one integer."""
    try:
        color = int(value)
    except (TypeError, ValueError):
        return default
    return (color & 255, (color >> 8) & 255, (color >> 16) & 255, (color >> 24) & 255)


def visual_center(transform: dict) -> tuple[float, float]:
    """The middle of the box that is on screen."""
    align = int(transform.get("alignment") or 0)
    use_bounds = (transform.get("boundsType") or "OBS_BOUNDS_NONE") != "OBS_BOUNDS_NONE"
    if use_bounds:
        width = float(transform.get("boundsWidth") or 0)
        height = float(transform.get("boundsHeight") or 0)
    else:
        width = float(transform.get("width") or transform.get("sourceWidth") or 0)
        height = float(transform.get("height") or transform.get("sourceHeight") or 0)
    x = float(transform.get("positionX") or 0)
    y = float(transform.get("positionY") or 0)
    if align & ALIGN_RIGHT:
        cx = x - width / 2
    elif align & ALIGN_LEFT:
        cx = x + width / 2
    else:
        cx = x
    if align & ALIGN_BOTTOM:
        cy = y - height / 2
    elif align & ALIGN_TOP:
        cy = y + height / 2
    else:
        cy = y
    return cx, cy


def upright_scale(scale_x) -> float | None:
    """Undo a leftover left-look mirror. A positive scale is left alone."""
    try:
        value = float(scale_x)
    except (TypeError, ValueError):
        return None
    if value >= -0.0001:
        return None
    magnitude = abs(value)
    if magnitude < 0.01:
        magnitude = 1.0
    return magnitude


def content_scale(transform: dict) -> tuple[float, float]:
    """How big the glyphs are, after a fit-to-box scale."""
    sx = float(transform.get("scaleX") or 1)
    sy = float(transform.get("scaleY") or 1)
    bounds = transform.get("boundsType") or "OBS_BOUNDS_NONE"
    if bounds == "OBS_BOUNDS_NONE":
        return sx, sy
    sw = float(transform.get("sourceWidth") or 0)
    sh = float(transform.get("sourceHeight") or 0)
    bw = float(transform.get("boundsWidth") or 0)
    bh = float(transform.get("boundsHeight") or 0)
    if sw < 1 or sh < 1 or bw < 1 or bh < 1:
        return sx, sy
    if bounds == "OBS_BOUNDS_STRETCH":
        return sx * bw / sw, sy * bh / sh
    if bounds == "OBS_BOUNDS_SCALE_OUTER":
        fit = max(bw / sw, bh / sh)
    elif bounds == "OBS_BOUNDS_SCALE_TO_WIDTH":
        fit = bw / sw
    elif bounds == "OBS_BOUNDS_SCALE_TO_HEIGHT":
        fit = bh / sh
    else:
        fit = min(bw / sw, bh / sh)
    return sx * fit, sy * fit


def auth_string(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest())
    return base64.b64encode(hashlib.sha256(secret + challenge.encode()).digest()).decode()


class Obs:
    def __init__(self, url: str, password: str):
        self.url = url
        self.password = password
        self.ws = None
        self.item_ids: dict[str, int] = {}
        self.item_scenes: dict[str, str] = {}
        self.face_text = False
        self.panel = False
        self.font_px = 80
        self.outline_px = 4
        self.fill = (255, 255, 255, 255)
        self.stroke = (0, 0, 0, 255)
        # Start on the other file. OBS keeps the old picture when the path
        # does not change, and the live face is often still prompt-a.png.
        self._flip = True
        self._paint_key = None
        self._hat_ready = False
        self._hat_note = None
        self._hat_flip = False
        self._hat_paint_key = None

    async def connect(self) -> None:
        self.ws = await websockets.connect(self.url)
        hello = json.loads(await self.ws.recv())
        if hello.get("op") != 0:
            raise RuntimeError(f"unexpected OBS hello: {hello}")
        identify = {"op": 1, "d": {"rpcVersion": 1, "eventSubscriptions": 0}}
        auth = hello["d"].get("authentication")
        if auth:
            identify["d"]["authentication"] = auth_string(
                self.password, auth["salt"], auth["challenge"]
            )
        await self.ws.send(json.dumps(identify))
        identified = json.loads(await self.ws.recv())
        if identified.get("op") != 2:
            raise RuntimeError(f"OBS refused the connection: {identified}")
        self.item_ids = {}
        self.item_scenes = {}
        try:
            await self.ensure_face_text()
            await self.ensure_panel()
        except (OSError, websockets.ConnectionClosed, RuntimeError, KeyError) as error:
            self.face_text = False
            print(f"face text source was not created: {error}", flush=True)
        print("OBS connected", flush=True)

    async def ensure_face_text(self) -> None:
        listed = await self.request("GetInputList", {})
        if not listed["requestStatus"]["result"]:
            self.face_text = False
            print(f"could not list inputs: {listed['requestStatus']}", flush=True)
            return
        names = {item.get("inputName") for item in listed["responseData"].get("inputs", [])}
        if FACE_TEXT in names:
            self.face_text = True
            await self.remember_item(FACE_TEXT)
            await self.use_face_font()
            item_id = self.item_ids.get(FACE_TEXT)
            if item_id is not None:
                await self.request("SetSceneItemEnabled", {
                    "sceneName": self.item_scenes.get(FACE_TEXT, SCENE),
                    "sceneItemId": item_id,
                    "sceneItemEnabled": True,
                })
            print("face text source already present", flush=True)
            return
        created = await self.request("CreateInput", {
            "sceneName": SCENE,
            "inputName": FACE_TEXT,
            "inputKind": "text_ft2_source_v2",
            "inputSettings": {
                "text": "",
                "font": {"face": FACE_FONT, "style": "Regular", "size": 80, "flags": 0},
                "outline": True,
                "outline_size": 4,
                "outline_color": 4278190080,
                "color1": 4294967295,
                "color2": 4294967295,
            },
            "sceneItemEnabled": True,
        })
        if not created["requestStatus"]["result"]:
            self.face_text = False
            print(f"face text source was not created: {created['requestStatus']}", flush=True)
            return
        self.face_text = True
        found = await self.request("GetSceneItemId", {"sceneName": SCENE, "sourceName": FACE_TEXT})
        if not found["requestStatus"]["result"]:
            print(f"face text has no scene item: {found['requestStatus']}", flush=True)
            return
        item_id = found["responseData"]["sceneItemId"]
        self.item_ids[FACE_TEXT] = item_id
        current = await self.request("GetSceneItemTransform", {
            "sceneName": SCENE,
            "sceneItemId": item_id,
        })
        if not current["requestStatus"]["result"]:
            print(f"face text transform was not read: {current['requestStatus']}", flush=True)
            return
        transform = current["responseData"]["sceneItemTransform"]
        transform["positionX"] = 640.0
        transform["positionY"] = 180.0
        # A new text item can report a zero bounds box, which OBS rejects.
        if float(transform.get("boundsWidth") or 0) < 1:
            transform["boundsWidth"] = 1200.0
        if float(transform.get("boundsHeight") or 0) < 1:
            transform["boundsHeight"] = 200.0
        for key in ("scaleX", "scaleY"):
            if abs(float(transform.get(key) or 0)) < 0.01:
                transform[key] = 1.0
        moved = await self.request("SetSceneItemTransform", {
            "sceneName": SCENE,
            "sceneItemId": item_id,
            "sceneItemTransform": transform,
        })
        if not moved["requestStatus"]["result"]:
            print(f"face text was not positioned: {moved['requestStatus']}", flush=True)
            return
        print("face text source created", flush=True)

    async def read_font(self) -> None:
        settings = await self.request("GetInputSettings", {"inputName": FACE_TEXT})
        if not settings["requestStatus"]["result"]:
            return
        current = (settings.get("responseData") or {}).get("inputSettings") or {}
        font = current.get("font") or {}
        try:
            self.font_px = max(int(font.get("size") or 80), 1)
        except (TypeError, ValueError):
            self.font_px = 80
        if current.get("outline"):
            try:
                self.outline_px = max(int(current.get("outline_size") or 0), 0)
            except (TypeError, ValueError):
                self.outline_px = 4
        else:
            self.outline_px = 0
        self.fill = obs_rgba(current.get("color1"), self.fill)
        self.stroke = obs_rgba(current.get("outline_color"), self.stroke)

    async def ensure_panel(self) -> None:
        """One fixed image, centered on the old text, with no fit-to-text scale."""
        await self.read_font()
        listed = await self.request("GetInputList", {})
        if not listed["requestStatus"]["result"]:
            print(f"could not list inputs: {listed['requestStatus']}", flush=True)
            return
        names = {item.get("inputName") for item in listed["responseData"].get("inputs", [])}
        if PANEL not in names:
            # Only a new source needs a starter file. Writing this over the
            # live panel blanks the face until the next paint.
            placeholder = PANEL_DIR / "prompt-a.png"
            paint_panel("", "_", "_", placeholder, font_px=self.font_px, outline=self.outline_px, fill=self.fill, stroke=self.stroke)
            created = await self.request("CreateInput", {
                "sceneName": SCENE,
                "inputName": PANEL,
                "inputKind": "image_source",
                "inputSettings": {"file": str(placeholder), "unload": False, "linear_alpha": False},
                "sceneItemEnabled": True,
            })
            if not created["requestStatus"]["result"]:
                print(f"prompt panel was not created: {created['requestStatus']}", flush=True)
                return
        # The file update does not need the scene item. Prompt may live in
        # a scene other than Avatar after it has been placed.
        self.panel = True
        item_id = await self.remember_item(PANEL)
        if item_id is None:
            print("prompt image is not in a scene yet; the file will still update", flush=True)
            return
        current = await self.request("GetSceneItemTransform", {
            "sceneName": self.item_scenes.get(PANEL, SCENE),
            "sceneItemId": item_id,
        })
        if not current["requestStatus"]["result"]:
            print(f"prompt transform was not read: {current['requestStatus']}", flush=True)
            return
        transform = current["responseData"]["sceneItemTransform"]
        # Alignment 0 is the center of the panel. Bounds none means the OBS
        # scale and rotation are the only size change. A panel that is already
        # set up stays where it was dragged.
        already = (
            int(transform.get("alignment") or 0) == 0
            and (transform.get("boundsType") or "OBS_BOUNDS_NONE") == "OBS_BOUNDS_NONE"
            and PANEL in names
        )
        if not already:
            center, scale = await self._placed_like_text()
            transform["alignment"] = 0
            transform["boundsAlignment"] = 0
            transform["boundsType"] = "OBS_BOUNDS_NONE"
            transform["positionX"] = center[0]
            transform["positionY"] = center[1]
            transform["scaleX"] = scale[0]
            transform["scaleY"] = scale[1]
            if float(transform.get("boundsWidth") or 0) < 1:
                transform["boundsWidth"] = 1.0
            if float(transform.get("boundsHeight") or 0) < 1:
                transform["boundsHeight"] = 1.0
            moved = await self.request("SetSceneItemTransform", {
                "sceneName": self.item_scenes.get(PANEL, SCENE),
                "sceneItemId": item_id,
                "sceneItemTransform": transform,
            })
            if not moved["requestStatus"]["result"]:
                print(f"prompt panel was not placed: {moved['requestStatus']}", flush=True)
                return
            print(
                f"prompt panel centered at {center[0]:.0f},{center[1]:.0f} scale {scale[0]:.2f}",
                flush=True,
            )
        else:
            print("prompt panel already centered", flush=True)
        text_id = self.item_ids.get(FACE_TEXT)
        if text_id is None:
            text_id = await self.remember_item(FACE_TEXT)
        if text_id is not None:
            await self.request("SetSceneItemEnabled", {
                "sceneName": self.item_scenes.get(FACE_TEXT, SCENE),
                "sceneItemId": text_id,
                "sceneItemEnabled": False,
            })

    async def _placed_like_text(self) -> tuple[tuple[float, float], tuple[float, float]]:
        text_id = self.item_ids.get(FACE_TEXT)
        if text_id is None:
            return (960.0, 540.0), (1.0, 1.0)
        current = await self.request("GetSceneItemTransform", {
            "sceneName": self.item_scenes.get(FACE_TEXT, SCENE),
            "sceneItemId": text_id,
        })
        if not current["requestStatus"]["result"]:
            return (960.0, 540.0), (1.0, 1.0)
        transform = current["responseData"]["sceneItemTransform"]
        return visual_center(transform), content_scale(transform)

    async def show_line(self, state: dict) -> None:
        if not self.panel:
            return
        face = state.get("face_text")
        if face is None:
            face = state.get("text") or ""
            left = ""
            right = ""
        else:
            left = state.get("arm_l_text") or ""
            right = state.get("arm_r_text") or ""
        key = (face, left, right, self.font_px, self.outline_px, self.fill, self.stroke)
        if key == self._paint_key:
            return
        # Two file names, because OBS keeps the previous picture when the
        # path does not change.
        self._flip = not self._flip
        path = PANEL_DIR / ("prompt-a.png" if self._flip else "prompt-b.png")
        paint_panel(
            face,
            left,
            right,
            path,
            font_px=self.font_px,
            outline=self.outline_px,
            fill=self.fill,
            stroke=self.stroke,
        )
        updated = await self.request("SetInputSettings", {
            "inputName": PANEL,
            "inputSettings": {"file": str(path)},
            "overlay": True,
        })
        if not updated["requestStatus"]["result"]:
            print(f"prompt panel was not updated: {updated['requestStatus']}", flush=True)
            return
        self._paint_key = key

    async def use_face_font(self) -> None:
        """Point the expression line at Face Text without changing its size."""
        settings = await self.request("GetInputSettings", {"inputName": FACE_TEXT})
        if not settings["requestStatus"]["result"]:
            print(f"face text settings were not read: {settings['requestStatus']}", flush=True)
            return
        current = (settings.get("responseData") or {}).get("inputSettings") or {}
        font = dict(current.get("font") or {})
        if font.get("face") == FACE_FONT:
            return
        font["face"] = FACE_FONT
        font["style"] = "Regular"
        font.setdefault("size", 80)
        font.setdefault("flags", 0)
        updated = await self.request("SetInputSettings", {
            "inputName": FACE_TEXT,
            "inputSettings": {"font": font},
            "overlay": True,
        })
        if not updated["requestStatus"]["result"]:
            print(f"face text font was not changed: {updated['requestStatus']}", flush=True)
        else:
            print(f"face text font set to {FACE_FONT}", flush=True)

    async def remember_item(self, name: str) -> int | None:
        """Find a source in any scene. A placed face is not required to stay in Avatar."""
        found = await self.source_items(name)
        if not found:
            return None
        scene, item_id = found[0]
        self.item_ids[name] = item_id
        self.item_scenes[name] = scene
        return item_id

    async def request(self, request_type: str, data: dict) -> dict:
        request_id = uuid.uuid4().hex
        await self.ws.send(json.dumps({
            "op": 6,
            "d": {
                "requestType": request_type,
                "requestId": request_id,
                "requestData": data,
            },
        }))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("op") == 7 and message["d"].get("requestId") == request_id:
                return message["d"]

    def _blank_hat(self) -> Path:
        PANEL_DIR.mkdir(parents=True, exist_ok=True)
        path = PANEL_DIR / "hat-blank.png"
        if not path.exists():
            path.write_bytes(BLANK_PNG)
        return path

    async def _scene_names(self) -> list[str]:
        listed = await self.request("GetSceneList", {})
        if not listed["requestStatus"]["result"]:
            return []
        return [
            item.get("sceneName")
            for item in (listed.get("responseData") or {}).get("scenes") or []
            if item.get("sceneName")
        ]

    async def _children(self, scene: str, grouped: bool) -> list[dict]:
        request = "GetGroupSceneItemList" if grouped else "GetSceneItemList"
        listed = await self.request(request, {"sceneName": scene})
        if not listed["requestStatus"]["result"]:
            return []
        return (listed.get("responseData") or {}).get("sceneItems") or []

    async def source_items(self, source_name: str) -> list[tuple[str, int]]:
        """Every copy of a source, including one nested in a group."""
        found = []
        pending = [(name, False) for name in await self._scene_names()]
        seen = set()
        while pending:
            scene, grouped = pending.pop()
            if scene in seen:
                continue
            seen.add(scene)
            for item in await self._children(scene, grouped):
                if item.get("sourceName") == source_name and item.get("sceneItemId") is not None:
                    found.append((scene, int(item["sceneItemId"])))
                if item.get("isGroup") and item.get("sourceName"):
                    pending.append((item["sourceName"], True))
        return found

    async def hat_items(self) -> list[tuple[str, int]]:
        """Every copy of the hat image, including one nested in a group."""
        return await self.source_items(HAT)

    async def ensure_hat(self) -> None:
        """Create the hat scene and image once. Later runs leave the placement alone."""
        scenes = await self._scene_names()
        if HAT_SCENE not in scenes:
            created = await self.request("CreateScene", {"sceneName": HAT_SCENE})
            if not created["requestStatus"]["result"]:
                print(f"hat scene was not created: {created['requestStatus']}", flush=True)
                return
        listed = await self.request("GetInputList", {})
        if not listed["requestStatus"]["result"]:
            print(f"could not list inputs: {listed['requestStatus']}", flush=True)
            return
        names = {item.get("inputName") for item in listed["responseData"].get("inputs", [])}
        if HAT in names:
            self._hat_ready = True
            return
        created = await self.request("CreateInput", {
            "sceneName": HAT_SCENE,
            "inputName": HAT,
            "inputKind": "image_source",
            "inputSettings": {"file": str(self._blank_hat()), "unload": False},
            "sceneItemEnabled": False,
        })
        if not created["requestStatus"]["result"]:
            print(f"hat image was not created: {created['requestStatus']}", flush=True)
            return
        found = await self.request("GetSceneItemId", {"sceneName": HAT_SCENE, "sourceName": HAT})
        if not found["requestStatus"]["result"]:
            print(f"hat image has no scene item: {found['requestStatus']}", flush=True)
            return
        item_id = found["responseData"]["sceneItemId"]
        current = await self.request("GetSceneItemTransform", {
            "sceneName": HAT_SCENE,
            "sceneItemId": item_id,
        })
        if current["requestStatus"]["result"]:
            transform = current["responseData"]["sceneItemTransform"]
            # Center alignment keeps the painted text in place. Later
            # connections do not write this transform again.
            transform["alignment"] = 0
            transform["boundsType"] = "OBS_BOUNDS_NONE"
            transform["boundsAlignment"] = 0
            transform["scaleX"] = 1.0
            transform["scaleY"] = 1.0
            if float(transform.get("boundsWidth") or 0) < 1:
                transform["boundsWidth"] = 1.0
            if float(transform.get("boundsHeight") or 0) < 1:
                transform["boundsHeight"] = 1.0
            await self.request("SetSceneItemTransform", {
                "sceneName": HAT_SCENE,
                "sceneItemId": item_id,
                "sceneItemTransform": transform,
            })
        self._hat_ready = True
        print(f"hat image created in scene {HAT_SCENE}", flush=True)

    async def show_hat(self, state: dict) -> None:
        """Paint the hat text for the current look. Arms stay on the face."""
        await self.ensure_hat()
        if not self._hat_ready:
            return
        slot = state.get("hat") if isinstance(state.get("hat"), str) else "none"
        if slot not in (*HAT_NAMES, "none"):
            slot = "none"
        text = state.get("hat_text") if isinstance(state.get("hat_text"), str) else ""
        text = text.replace("\n", " ").replace("\r", "")
        visible = slot in HAT_NAMES and bool(text.strip())
        if slot != self._hat_note:
            print(f"hat {slot}", flush=True)
            self._hat_note = slot
        key = (text, self.font_px, self.outline_px, self.fill, self.stroke) if visible else None
        if visible and key != self._hat_paint_key:
            self._hat_flip = not self._hat_flip
            path = PANEL_DIR / ("hat-a.png" if self._hat_flip else "hat-b.png")
            paint_panel(
                text,
                "",
                "",
                path,
                font_px=self.font_px,
                outline=self.outline_px,
                fill=self.fill,
                stroke=self.stroke,
            )
            updated = await self.request("SetInputSettings", {
                "inputName": HAT,
                "inputSettings": {"file": str(path)},
                "overlay": True,
            })
            if not updated["requestStatus"]["result"]:
                print(f"hat was not painted: {updated['requestStatus']}", flush=True)
            else:
                self._hat_paint_key = key
        elif not visible:
            self._hat_paint_key = None
        for scene, item_id in await self.hat_items():
            current = await self.request("GetSceneItemTransform", {
                "sceneName": scene,
                "sceneItemId": item_id,
            })
            if current["requestStatus"]["result"]:
                transform = current["responseData"]["sceneItemTransform"]
                scale_x = upright_scale(transform.get("scaleX"))
                # Send only the horizontal scale. Writing the whole transform
                # back fails when OBS reports a bounds size of zero, and it
                # would also move a hat the user had placed.
                if scale_x is not None:
                    moved = await self.request("SetSceneItemTransform", {
                        "sceneName": scene,
                        "sceneItemId": item_id,
                        "sceneItemTransform": {"scaleX": scale_x},
                    })
                    if not moved["requestStatus"]["result"]:
                        print(f"hat scale was not set: {moved['requestStatus']}", flush=True)
            await self.request("SetSceneItemEnabled", {
                "sceneName": scene,
                "sceneItemId": item_id,
                "sceneItemEnabled": visible,
            })

    async def apply(self, state: dict, fields: list[str]) -> None:
        await self.show_line(state)
        if "hat" in fields or "look" in fields or not self._hat_ready:
            try:
                await self.show_hat(state)
            except (OSError, RuntimeError, KeyError, TypeError) as error:
                print(f"hat was not updated: {error}", flush=True)
        for name in fields:
            sent = await self.request("CallVendorRequest", {
                "vendorName": "AdvancedSceneSwitcher",
                "requestType": "AdvancedSceneSwitcherMessage",
                "requestData": {"message": f"{name}={state[name]}"},
            })
            if not sent["requestStatus"]["result"]:
                print(f"scene switcher message failed: {sent['requestStatus']}", flush=True)

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
            self.ws = None


async def run() -> None:
    config = load_obs_config()
    configure_names(config)
    obs_url = f"ws://{config['host']}:{config['port']}"
    obs = Obs(obs_url, config["password"])
    while True:
        try:
            await obs.connect()
            async with websockets.connect(TRACKER_URL) as tracker:
                print(f"tracker {TRACKER_URL}", flush=True)
                first = True
                last_text = 0.0
                last_line = None
                async for raw in tracker:
                    state = json.loads(raw)
                    names = [name for name in (*FIELDS, *DETAIL) if name in state]
                    if first:
                        changed = list(names)
                        if isinstance(state.get("muted"), str) and "muted" not in changed:
                            changed.append("muted")
                        if isinstance(state.get("hat"), str) and "hat" not in changed:
                            changed.append("hat")
                    else:
                        changed = [
                            name for name in (state.get("changed") or [])
                            if name not in ("text", "text_slot", "image", "image_side", "hat_text") and isinstance(state.get(name), str)
                        ]
                    first = False
                    now = time.monotonic()
                    line = state.get("text")
                    refresh = bool(changed) or line != last_line or now - last_text >= 0.3
                    if not refresh:
                        continue
                    if changed:
                        print(" ".join(f"{name}={state[name]}" for name in changed), flush=True)
                    await obs.apply(state, changed)
                    last_text = now
                    last_line = line
        except (OSError, websockets.ConnectionClosed, RuntimeError, KeyError) as error:
            print(f"waiting: {error}", flush=True)
            await obs.close()
            await asyncio.sleep(2)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("stopped", flush=True)


if __name__ == "__main__":
    main()
