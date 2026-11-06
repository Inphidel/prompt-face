"""Open a V4L2 webcam as a Motion-JPEG capture.

The device path, size, frame rate, and fourcc come from config.json.
A buffer of one frame keeps the face close to the present moment.

camera.device may be /dev/videoN or a /dev/v4l/by-id link that ends in
-video-index0. The by-id link is the capture node for one physical
camera, so two identical webcams stay distinct when Linux renumbers
the video nodes.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2

BY_ID = Path("/dev/v4l/by-id")


def capture_links() -> list[Path]:
    if not BY_ID.is_dir():
        return []
    return sorted(path for path in BY_ID.iterdir() if path.name.endswith("-video-index0"))


def describe_cameras() -> str:
    links = capture_links()
    if not links:
        return "No capture cameras are connected."
    lines = ["Connected capture cameras:"]
    lines.extend(f"  {path}" for path in links)
    return "\n".join(lines)


def resolve_device(device: str) -> str:
    text = (device or "").strip()
    if text and os.path.exists(text):
        return text
    links = [str(path) for path in capture_links()]
    if text:
        matches = [path for path in links if text in path]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(
                f"Camera {text!r} matches more than one device.\n{describe_cameras()}"
            )
    shown = text or "(empty)"
    raise RuntimeError(
        f"Camera {shown} is not connected.\n{describe_cameras()}\n"
        "Set camera.device in config.json to one path from that list."
    )


class Camera:
    def __init__(self, device: str, width: int, height: int, fps: int, fourcc: str):
        self.device = resolve_device(device)
        self._cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not self._cap.isOpened():
            raise RuntimeError(
                f"Could not open camera {self.device}.\n{describe_cameras()}"
            )
        self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_FPS, fps)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = float(self._cap.get(cv2.CAP_PROP_FPS)) or float(fps)
        raw = int(self._cap.get(cv2.CAP_PROP_FOURCC))
        self.fourcc = "".join(chr((raw >> (8 * i)) & 0xFF) for i in range(4))

    def read(self):
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        return frame

    def close(self) -> None:
        self._cap.release()
