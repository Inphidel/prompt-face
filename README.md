# Prompt Face

Prompt is a text face driven by a webcam. A local program reads your face, hands, and pose, paints one line of text into a fixed panel, and OBS shows that panel. The letters stay one size. Stretch, rotate, or move the panel in OBS when you want the face larger, smaller, or tilted. The origin of the panel is its middle.

The microphone can blink the mouth while you talk. A button switches that mouth between the microphone and the jaw seen by the camera. Another command replaces the whole line with a custom emote, at the same size, still centered.

Everything runs on your machine. The webcam picture is not uploaded.

## System requirements

- Linux with a V4L2 webcam. `/dev/video0` is the usual device.
- Python 3.12 or newer, with `venv`. This project is tested with Python 3.14 on Ubuntu 26.04.
- PipeWire, including the `pw-record` command, when the mouth should follow the microphone. Jaw mode does not open a microphone.
- OBS Studio 28 or newer, with the built-in WebSocket server turned on. This project is tested with OBS 32.
- A desktop CPU. The face, hand, and pose models run on the CPU. A GPU is not required.
- About 25 MB for the included models, plus a few hundred MB for the Python virtual environment.

Optional:

- Advanced Scene Switcher in OBS, if you want messages such as `mouth=open` to drive macros.
- A Stream Deck, or any button that can run a shell command, for emotes and the mouth switch.
- Ghostty, or another terminal that accepts `-e`, if you want the desktop launchers to open a window.

Windows and macOS are not supported. The camera path is V4L2 and the microphone path is PipeWire.

## Install

```bash
git clone https://github.com/Inphidel/prompt-face.git
cd prompt-face
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp config.example.json config.json
mkdir -p ~/.config/face-state
cp obs.example.json ~/.config/face-state/obs.json
```

Edit the two config files before the first run. `config.json` stays in this directory and is ignored by git. The OBS file lives in your config folder, outside the project, because it holds the websocket password.

Desktop and application-menu icons:

```bash
./install-desktop.sh
```

That writes launchers only on this machine. Run it again if you move the project folder.

## Configure

### Camera

In `config.json`, `camera.device` is the webcam. Leave `/dev/video0` if that is the camera you want. `width`, `height`, `fps`, and `fourcc` are requested from V4L2. `MJPG` at `1280x720` is a reliable starting point.

`listen` is the state WebSocket, default `127.0.0.1:8770`. `author.listen` is the expression page, default `127.0.0.1:8771`. Keep both on localhost.

### Mouth

`mouth.source` is `jaw` or `sound`. `jaw` follows the mouth the camera sees. `sound` follows microphone loudness.

`mouth.device` is a PipeWire source name. Leave it empty to use the default source. If that default is the webcam microphone, set it to the input you speak into. `wpctl status` lists the sources. A node name looks like `alsa_input.usb-...`. The tracker opens the microphone only while sound mode is on.

`mouth.quiet` is the loudness that opens the talk face. `mouth.loud` is the loudness that uses the shout face. The shipped values are `0.004` and `0.03`. Raise them if room noise opens the mouth. Lower them if speech stays closed. `attack` is how fast the level rises. `release` is how fast it falls. A slower release keeps a syllable from flickering shut between peaks.

The once-a-second tracker line prints `voice=` while sound mode is on.

### OBS

In OBS, turn on Tools, WebSocket Server Settings, and set a password. Put that password in `~/.config/face-state/obs.json`:

```json
{
  "host": "127.0.0.1",
  "port": 4455,
  "password": "the password from OBS",
  "scene": "Avatar",
  "panel": "Prompt"
}
```

Create a scene with the name in `scene`. The default is `Avatar`. The bridge adds an image source with the name in `panel`. The default is `Prompt`. It also adds a hidden text source named `Face text` and copies that source's font size, outline, and colors onto the painted panel. The visible face is the image.

Drag, stretch, or rotate Prompt. The bridge leaves that transform alone once the source is centered and is not set to fit the text to a box.

`panel/` holds the two painted frames, `prompt-a.png` and `prompt-b.png`. Those files are generated and are not committed. OBS does not reload an image when the path stays the same, so the bridge alternates the two names.

### The character

`textfaces.json` is the face. Each slot has a left, center, and right line. The slots are `absent`, `neutral`, `talk`, `shout`, `smile`, `blink`, `closed`, `cover_l`, `cover_r`, and `cover_both`. The file shipped here is the Prompt character. Edit it, or edit it from the expression page, and save.

Arms have four marks: left up, left down, right up, and right down. A down or unseen arm uses the down mark, which reads as a shoulder. A hand on the face removes the arm on that side. The other arm stays. Thumbs-up, peace, point, and an open hand replace that side's arm mark with the gesture text.

`images/` is an optional folder of pictures a slot can use instead of text. Picture files in that folder are ignored by git.

`rules.json` starts empty. A rule can send `name=on` and `name=off` to Advanced Scene Switcher, and a rule can carry its own face text.

### Buttons

From this directory, with the tracker running:

```bash
./face-emote '¯\_(ツ)_/¯'
./face-emote --clear
./face-mouth
./face-mouth jaw
./face-mouth sound
```

`face-emote` replaces the whole line. The text is the entire emote. It is drawn at the same size and stays in the center. `--clear` returns the camera face. `face-mouth` with no argument toggles jaw and sound.

A Stream Deck button can run those commands. Use the full path to the script in the button, because the deck does not start in this directory. The scripts find their own folder.

The expression page has the same mouth switch. Open it from the Prompt page icon, or at http://127.0.0.1:8771 while the tracker is running.

## Run

Two programs. Start the tracker first, then the bridge while OBS is open.

```bash
./prompt-face
./prompt-obs
```

Leave both terminals open. Closing a terminal stops that program.

The tracker prints a line about once a second. `jaw` rises when the mouth opens. `blinkL` and `blinkR` rise when the eyes close. `look` follows head yaw. `armL` and `armR` are how far each wrist sits above its shoulder, in torso lengths. Positive is up. The label turns `up` at 0.22 and returns to `down` at 0.05.

A second terminal can print only the changes:

```bash
./venv/bin/python -m face_state.watch
```

The state socket is `ws://127.0.0.1:8770/state`. Each message is one JSON object. `changed` lists the fields that flipped. A held expression does not keep filling `changed`.

On a desktop, the icons from `./install-desktop.sh` do the same three jobs: Prompt starts the tracker, Prompt OBS starts the bridge, and Prompt page opens the expression page.

## How it works

The package is `face_state`. Comments at the top of each module describe that step.

1. `camera.py` opens the V4L2 webcam.
2. `tracker.py` runs the MediaPipe face, hand, and pose landmarkers on each frame.
3. `pose.py` and `hand.py` measure arms, where a hand sits on the face, which way the palm points, and what the fingers are doing.
4. `reduce.py` turns scores into stable names. Each label has an enter line, a lower exit line, and a short hold so a value on the boundary does not flicker.
5. `textface.py` picks the face string, the arm marks, or a whole-line emote.
6. `paint.py` draws that line in the middle of a 1400 by 180 panel. The font size stays put. Arms grow outward and clip at the edge.
7. `obs_bridge.py` writes the panel into the OBS image source and can message Advanced Scene Switcher.
8. `voice.py` reads the microphone with `pw-record` when the mouth source is sound.
9. `author.py` serves the expression page and the emote and mouth commands.
10. `server.py` publishes the state JSON on the WebSocket.

`compose.py` is the rule list. A rule names the returns it cares about. While they hold, OBS can hear `name=on`. When they let go, it hears `name=off`.

## States

| Field | Values |
| --- | --- |
| face | present, absent |
| mouth | closed, open, wide |
| smile | off, on |
| eyes | open, blink, closed |
| look | left, center, right |
| head | up, level, down |
| arm_l, arm_r | down, up, missing |
| hand_l, hand_r | off, face |
| place_l, place_r | none, brow, eyes, nose, mouth |
| facing_l, facing_r | none, palm, back, edge |
| shape_l, shape_r | none, open, fist, point |
| thumb, index, middle, ring, pinky, each side | down, up |

`arm_l` is your left arm. `hand_l` is `face` while that hand's place is on the brow, the eyes, the nose, or the mouth. Arms and hands keep updating when the face itself is covered.

`open` means at least three of the four fingers are out. `fist` means those four are curled. `point` means only the index is out. The thumb is its own return, so a thumbs-up is a fist with the thumb up.

A short close of the eyes becomes `blink` when they open. If the eyes stay shut, the state is `closed`, and opening them does not also report a blink.

No face in frame plays the three absent lines as frames. Each frame stays up for that slot's hold, plus or minus a random amount up to the variation.

## Tune

Thresholds live in `config.json`. `enter` is the score that turns a state on. `exit` is the lower score that turns it off. `hold` is how many frames the new value has to last.

If left and right are swapped, set `thresholds.look.yaw_sign` to `-1`. The same switch for up and down is `thresholds.head.pitch_sign`. If the arms come out on the wrong sides, set `pose.swap_sides` to `true`. If a palm toward the camera reads as `back`, set `hands.facing_sign` to `-1`.

## What stays off the public project

Keep these out of git. The ignore rules already do.

- `config.json`, with your camera device and microphone name.
- `~/.config/face-state/obs.json`, with the OBS websocket password.
- `panel/`, the frames painted while the bridge is running.
- Picture files in `images/`.
- `venv/`.

Review `textfaces.json` and `rules.json` before you commit your own changes. The face text is the character. A rule name is sent to OBS, so a private scene name would be published with it.

## License

A license for this project has not been chosen. Until one is added, reuse needs the author's permission. The models and the font pieces keep their own licenses. See `THIRD_PARTY.md`.
