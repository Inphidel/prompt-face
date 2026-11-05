"""Open a V4L2 webcam as a Motion-JPEG capture.

The device path, size, frame rate, and fourcc come from config.json.
A buffer of one frame keeps the face close to the present moment.
"""

from __future__ import annotations

import cv2


class Camera:
    def __init__(self, device: str, width: int, height: int, fps: int, fourcc: str):
        self.device = device
        self._cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        if not self._cap.isOpened():
            raise RuntimeError(f"could not open camera {device}")
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
