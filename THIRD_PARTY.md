# Third-party pieces

Prompt Face itself does not have a chosen license yet. The pieces below keep the licenses they already have.

## Models

`models/face_landmarker.task`, `models/hand_landmarker.task`, and `models/pose_landmarker.task` are MediaPipe task bundles from Google. MediaPipe is released under the Apache License 2.0.

https://github.com/google-ai-edge/mediapipe

## Font

`fonts/FaceText-Regular.ttf` is built by `face_state/build_face_font.py`. It is one file so the painted line can use a single font.

- Gallery and symbol shapes come from the Noto fonts, which are under the SIL Open Font License 1.1. https://fonts.google.com/noto
- The CP437 ANSI-art alphabet, including the VGA smiles and box drawing, comes from GNU FreeFont FreeMono. GNU FreeFont is under the GNU General Public License, version 3 or later, with its font embedding exception. https://www.gnu.org/software/freefont/

A rebuild needs those families installed and visible to fontconfig. The file already in `fonts/` is enough to run the painter.

## Python packages

`requirements.txt` installs MediaPipe, OpenCV, NumPy, and websockets. Each package keeps its own license. They are installed into `venv/`, which is not part of this repository.
