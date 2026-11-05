"""Read the webcam, reduce it to states, and publish them.

Each frame goes camera, face landmarker, pose landmarker, hand landmarker,
then the reducer. The reducer names the expression. The text board turns
that into the line OBS will paint. The hub publishes the JSON on the state
socket. The author page is served beside it so emotes and the mouth switch
can reach the running tracker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import threading
import time
from pathlib import Path

os.environ.setdefault("GLOG_minloglevel", "2")

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import (
    FaceLandmarker,
    FaceLandmarkerOptions,
    HandLandmarker,
    HandLandmarkerOptions,
    PoseLandmarker,
    PoseLandmarkerOptions,
    RunningMode,
)

from face_state.author import serve_author
from face_state.command_server import serve_commands
from face_state.camera import Camera
from face_state.compose import Composer
from face_state.hand import HandMeasure
from face_state.pose import PoseMeasure
from face_state.reduce import FIELDS, Reducer
from face_state.server import Hub, serve
from face_state.textface import TextBoard
from face_state.voice import MouthControl

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path) -> dict:
    with path.open() as handle:
        return json.load(handle)


def yaw_pitch_deg(matrix) -> tuple[float, float]:
    """Yaw and pitch, in degrees, from the landmarker 4x4 face matrix."""
    rotation = np.asarray(matrix, dtype=float).reshape(4, 4)[:3, :3]
    sy = math.sqrt(rotation[0, 0] ** 2 + rotation[1, 0] ** 2)
    if sy < 1e-6:
        pitch = math.atan2(-rotation[1, 2], rotation[1, 1])
        yaw = math.atan2(-rotation[2, 0], sy)
    else:
        pitch = math.atan2(rotation[2, 1], rotation[2, 2])
        yaw = math.atan2(-rotation[2, 0], sy)
    return math.degrees(yaw), math.degrees(pitch)


def blendshape_map(categories) -> dict[str, float]:
    return {item.category_name: float(item.score) for item in categories}


def sample_from(result) -> dict | None:
    if not result.face_blendshapes:
        return None
    scores = blendshape_map(result.face_blendshapes[0])
    yaw, pitch = 0.0, 0.0
    if result.facial_transformation_matrixes:
        yaw, pitch = yaw_pitch_deg(result.facial_transformation_matrixes[0])
    return {
        "jawOpen": scores.get("jawOpen", 0.0),
        "mouthSmileLeft": scores.get("mouthSmileLeft", 0.0),
        "mouthSmileRight": scores.get("mouthSmileRight", 0.0),
        "eyeBlinkLeft": scores.get("eyeBlinkLeft", 0.0),
        "eyeBlinkRight": scores.get("eyeBlinkRight", 0.0),
        "yaw": yaw,
        "pitch": pitch,
    }


def open_landmarker(model_path: Path) -> FaceLandmarker:
    options = FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=RunningMode.VIDEO,
        num_faces=1,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
    )
    return FaceLandmarker.create_from_options(options)


def open_pose(model_path: Path) -> PoseLandmarker:
    options = PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.4,
        min_pose_presence_confidence=0.4,
        min_tracking_confidence=0.4,
    )
    return PoseLandmarker.create_from_options(options)


def open_hands(model_path: Path, hands: dict) -> HandLandmarker:
    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=float(hands.get("min_detection_confidence", 0.5)),
        min_hand_presence_confidence=float(hands.get("min_presence_confidence", 0.5)),
        min_tracking_confidence=float(hands.get("min_tracking_confidence", 0.5)),
    )
    return HandLandmarker.create_from_options(options)


def resolve_path(path: str) -> Path:
    model_path = Path(path)
    if not model_path.is_absolute():
        model_path = ROOT / model_path
    return model_path


def compose_sample(face_result, pose_result, hand_result, measure: PoseMeasure, hand_measure: HandMeasure | None, swap_sides: bool) -> dict:
    pose = measure.measure(pose_result)
    if hand_measure is None:
        hands = {"l": None, "r": None}
    else:
        hands = hand_measure.measure(hand_result, face_result, pose_result)
    if swap_sides:
        if pose:
            pose = {
                "arm_l": pose["arm_r"],
                "arm_r": pose["arm_l"],
                "hand_l": pose["hand_r"],
                "hand_r": pose["hand_l"],
            }
        hands = {"l": hands.get("r"), "r": hands.get("l")}
    return {"face": sample_from(face_result), "pose": pose, "hands": hands}


def _fmt(value, spec: str) -> str:
    if value is None:
        return "--"
    return format(value, spec)


def loop(config: dict, hub: Hub, loop: asyncio.AbstractEventLoop, stop: threading.Event, seconds: float | None, composer: Composer, board: TextBoard, mouth: MouthControl) -> None:
    camera_cfg = config["camera"]
    camera = Camera(
        camera_cfg["device"],
        int(camera_cfg["width"]),
        int(camera_cfg["height"]),
        int(camera_cfg["fps"]),
        camera_cfg["fourcc"],
    )
    print(
        f"camera {camera.device} {camera.width}x{camera.height} {camera.fourcc} "
        f"at {camera.fps:.0f} fps",
        flush=True,
    )
    landmarker = open_landmarker(resolve_path(config["model"]))
    pose_cfg = config.get("pose") or {}
    pose_landmarker = open_pose(resolve_path(pose_cfg["model"]))
    measure = PoseMeasure(float(pose_cfg["hand"]["radius"]), float(pose_cfg["hand"]["depth"]))
    hands_cfg = config.get("hands") or {}
    hand_landmarker = None
    hand_measure = None
    try:
        hand_path = resolve_path(hands_cfg.get("model", "models/hand_landmarker.task"))
        if not hand_path.exists():
            print(f"hand model missing at {hand_path}", flush=True)
        else:
            hand_landmarker = open_hands(hand_path, hands_cfg)
            hand_measure = HandMeasure(
                edge=float(hands_cfg.get("facing_edge", 0.35)),
                facing_sign=float(hands_cfg.get("facing_sign", 1)),
                depth=float(hands_cfg.get("depth", 0.0)),
                memory=int(hands_cfg.get("memory", 10)),
            )
            print(f"hands {hand_path.name}", flush=True)
    except Exception as error:
        print(f"hand landmarker did not start: {error}", flush=True)
        hand_landmarker = None
        hand_measure = None
    swap_sides = bool(pose_cfg.get("swap_sides", False))
    reducer = Reducer(
        config["thresholds"],
        int(config["face_hold"]),
        pose_cfg,
        hands_cfg,
        config.get("mouth"),
    )
    mouth_was = mouth.source()
    deadline = time.monotonic() + seconds if seconds else None
    last_print = 0.0
    last_stamp = 0
    infer_ms = 0.0
    frames = 0
    seen = 0
    warned_dark = False
    hand_warned = False
    # The microphone level is read only while the mouth source is sound.
    # Jaw mode leaves the capture closed.
    try:
        while not stop.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                break
            frame = camera.read()
            if frame is None:
                print("camera read failed", flush=True)
                time.sleep(0.05)
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            stamp = int(time.monotonic() * 1000)
            if stamp <= last_stamp:
                stamp = last_stamp + 1
            last_stamp = stamp
            started = time.perf_counter()
            face_result = landmarker.detect_for_video(image, stamp)
            pose_result = pose_landmarker.detect_for_video(image, stamp)
            hand_result = None
            if hand_landmarker is not None:
                try:
                    hand_result = hand_landmarker.detect_for_video(image, stamp)
                except Exception as error:
                    if not hand_warned:
                        hand_warned = True
                        print(f"hand landmarker failed: {error}", flush=True)
                    hand_result = None
            infer_ms += (time.perf_counter() - started) * 1000
            frames += 1
            seen += 1
            if not warned_dark and seen == 30 and float(frame.mean()) < 12:
                warned_dark = True
                print(
                    "picture is almost black. Open the camera privacy shutter "
                    "and point it at your face.",
                    flush=True,
                )
            source = mouth.source()
            state = reducer.update(
                compose_sample(
                    face_result, pose_result, hand_result, measure, hand_measure, swap_sides,
                ),
                mouth_source=source,
                voice=mouth.level(),
            )
            if source != mouth_was:
                state["changed"] = [*(state.get("changed") or []), "mouth_source"]
                print(f"mouth source={source}", flush=True)
                mouth_was = source
            state["mouth_source"] = source
            composer.apply(state)
            board.apply(state)
            state["t"] = round(time.time(), 3)
            loop.call_soon_threadsafe(hub.publish, state)
            now = time.monotonic()
            if state["changed"]:
                bits = " ".join(f"{name}={state[name]}" for name in state["changed"])
                print(f"change {bits}", flush=True)
            every = float(config["print_scores_every"])
            if every > 0 and now - last_print >= every:
                scores = state["scores"] or {}
                if "jawOpen" not in scores:
                    face_detail = "no face"
                else:
                    face_detail = (
                        f"jaw={scores['jawOpen']:.2f} "
                        f"blinkL={scores['blinkLeft']:.2f} blinkR={scores['blinkRight']:.2f} "
                        f"yaw={scores['yaw']:.1f} pitch={scores['pitch']:.1f}"
                    )
                if "armL" not in scores and "rawL" not in scores:
                    pose_detail = "no pose"
                else:
                    arms = f"armL={_fmt(scores.get('armL'), '+.2f')} armR={_fmt(scores.get('armR'), '+.2f')}"
                    if "rawL" in scores or "rawR" in scores:
                        hands_line = f"L={scores.get('rawL') or '--'} R={scores.get('rawR') or '--'}"
                    else:
                        hands_line = (
                            f"handL={_fmt(scores.get('handL'), '.2f')} handR={_fmt(scores.get('handR'), '.2f')}"
                        )
                    pose_detail = f"{arms} {hands_line}"
                labels = " ".join(f"{name}={state[name]}" for name in FIELDS)
                if state.get("mouth_source") == "sound":
                    heard = scores.get("voice")
                    labels = f"{labels} voice={heard if heard is not None else '--'}"
                active = " ".join(state.get("active") or [])
                if active:
                    labels = f"{labels} rules={active}"
                avg = infer_ms / frames if frames else 0
                print(
                    f"{face_detail} {pose_detail} | {labels} face={state.get('text', '')}"
                    f"{(' image=' + Path(state['image']).name) if state.get('image') else ''} infer={avg:.1f}ms",
                    flush=True,
                )
                last_print = now
                infer_ms = 0.0
                frames = 0
    finally:
        landmarker.close()
        pose_landmarker.close()
        if hand_landmarker is not None:
            hand_landmarker.close()
        camera.close()
        mouth.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish face states from the webcam")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--seconds", type=float, default=None, help="stop after this many seconds")
    args = parser.parse_args()
    config_path = args.config if args.config is not None else ROOT / "config.json"
    if args.config is None and not config_path.exists():
        example = ROOT / "config.example.json"
        if example.exists():
            config_path = example
            print(
                "using config.example.json. Copy it to config.json to set your camera and microphone.",
                flush=True,
            )
    config = load_config(config_path)
    host, port = config["listen"].rsplit(":", 1)
    author = config.get("author") or {}
    rules_path = Path(author.get("rules", "rules.json"))
    if not rules_path.is_absolute():
        rules_path = ROOT / rules_path
    textfaces_path = Path(author.get("textfaces", "textfaces.json"))
    if not textfaces_path.is_absolute():
        textfaces_path = ROOT / textfaces_path
    composer = Composer(rules_path)
    board = TextBoard(textfaces_path)
    mouth = MouthControl(config)
    page_host, page_port = str(author.get("listen", "127.0.0.1:8771")).rsplit(":", 1)
    serve_author(page_host, int(page_port), rules_path, textfaces_path, board, mouth)
    # The page stays on localhost. This port is the one another computer uses.
    serve_commands(config, board, mouth)
    hub = Hub()
    stop = threading.Event()

    async def run() -> None:
        worker = threading.Thread(
            target=loop,
            args=(config, hub, asyncio.get_running_loop(), stop, args.seconds, composer, board, mouth),
            daemon=True,
        )
        worker.start()
        server = asyncio.create_task(serve(hub, host, int(port)))
        while worker.is_alive():
            await asyncio.sleep(0.2)
        server.cancel()
        stop.set()

    print(f"state socket ws://{host}:{port}/state", flush=True)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        stop.set()
        print("stopped", flush=True)
