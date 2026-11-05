"""Prompt Face: a webcam text face for OBS.

One frame moves through four steps, all on this machine:

1. The tracker reads the webcam with MediaPipe face, hand, and pose
   landmarkers and turns the scores into named states (mouth, eyes, look,
   arms, hands).
2. The text board picks a face string from textfaces.json. Arms sit beside
   that string. A hand on the face clears the arm on that side. A custom
   emote replaces the whole line.
3. The painter draws the line in the middle of a fixed-size panel. A longer
   line does not change the letter size.
4. The OBS bridge puts that panel in an image source. Stretch and rotate
   the source in OBS when the face should grow, shrink, or tilt.

The microphone stays closed until a command selects sound. That command
can arrive on the TCP command port from another computer. The expression
page itself stays on localhost.

Run the tracker with ``python -m face_state``. Run the bridge, with OBS
open, with ``python -m face_state.obs_bridge``.
"""
