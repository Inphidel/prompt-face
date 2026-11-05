# Prompt Face

Prompt is a text face driven by a webcam. A local program reads your face, hands, and pose, paints one line of text into a fixed panel, and OBS shows that panel. The letters stay one size. Stretch, rotate, or move the panel in OBS when you want the face larger, smaller, or tilted. The origin of the panel is its middle.

The microphone can blink the mouth while you talk. It stays closed until a command turns sound on. OBS can use that same microphone the whole time. A button, on this computer or on another one, switches the mouth between the microphone and the jaw, and another command replaces the whole line with a custom emote. A mute command changes the face and tells OBS. It does not silence the microphone.

Everything runs on your machine. The webcam picture is not uploaded.

## System requirements

- Linux with a V4L2 webcam. `/dev/video0` is the usual device.
- Python 3.12 or newer, with `venv`. This project is tested with Python 3.14 on Ubuntu 26.04.
- PipeWire, including the `pw-record` command, when the mouth should follow the microphone. Jaw mode does not open a microphone. Sound mode adds a shared listener. OBS can keep recording the same source.
- OBS Studio 28 or newer, with the built-in WebSocket server turned on. This project is tested with OBS 32.
- A desktop CPU. The face, hand, and pose models run on the CPU. A GPU is not required.
- About 25 MB for the included models, plus a few hundred MB for the Python virtual environment.

Optional:

- Advanced Scene Switcher in OBS, if you want messages such as `mouth=open` or `muted=on` to drive macros.
- A Stream Deck, or any button that can open a TCP connection. The tracker listens on port 8772. A deck attached to another computer sends one line to that port. The firewall on the face computer has to allow it.
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

`listen` is the state WebSocket, default `127.0.0.1:8770`. `author.listen` is the expression page, default `127.0.0.1:8771`. Keep both on localhost. `command.listen` is the network command port, default `0.0.0.0:8772`. That is the port another computer uses.

### Mouth

`mouth.source` starts as `jaw`. Jaw follows the mouth the camera sees, and the tracker does not open the microphone. `sound` follows microphone loudness. The capture process is `pw-record`. It starts when a command selects sound, and it exits when a command selects jaw.

PipeWire lets more than one program record a source. Prompt Face does not take the microphone away from OBS. In jaw mode there is no capture at all. In sound mode OBS and Prompt Face both listen, and OBS keeps the audio for the stream.

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

### Commands

The tracker has to be running. The expression page on this computer has the same mouth switch, at http://127.0.0.1:8771 .

From this computer:

```bash
./face-emote '¯\_(ツ)_/¯'
./face-emote --clear
./face-mouth
./face-mouth jaw
./face-mouth sound
./prompt-command mouth toggle
./prompt-command emote '¯\_(ツ)_/¯'
./prompt-command clear
```

`face-emote` replaces the whole line. The text is the entire emote. It is drawn at the same size and stays in the center. `--clear` returns the camera face. `emote toggle TEXT` shows that face and leaves it there. Sending the same line again clears it. A different face replaces the one that is up. `face-mouth` with no argument toggles jaw and sound. Those two scripts talk to the page on localhost, so they only work on the face computer.

A Stream Deck on another computer uses the command port. The tracker prints `command port tcp://0.0.0.0:8772` when that port is open. Point the button at the face computer's address, port 8772, and send one line of text followed by a newline:

```text
mouth toggle
mouth jaw
mouth sound
mouth on
mouth off
mute
mute on
mute off
emote ¯\_(ツ)_/¯
emote toggle ¯\_(ツ)_/¯
emote --clear
clear
status
ping
```

`mouth` alone toggles. `mouth on` and `mouth sound` open the capture. `mouth off` and `mouth jaw` close the capture and return the mouth to the camera. That does not mute the stream. OBS keeps hearing the microphone.

`mute`, `mute on`, and `mute off` set a face mute. They do not silence the microphone, and they do not close the capture. While the mute is on, the painted face uses the Muted slot. That slot has a left, center, and right, like the other faces, and the expression page is where you edit it. The shipped mouth is a lowercase `x`. The page shows `Face mute: on` or `Face mute: off`. It does not switch the mute.

The same change goes to Advanced Scene Switcher as the message `muted=on` or `muted=off`. A macro there can mute the OBS audio source. Match those words exactly. Prompt Face leaves the microphone alone so OBS can keep hearing it. The reply includes `muted=on` or `muted=off`. A custom emote still replaces the whole line until it is cleared. The mute flag, the page report, and the OBS message stay on either way.

`status` answers with the mouth, the mute, and the face that is on screen. The face text is everything after `line=`:

```text
ok mouth=jaw hearing=no muted=off mode=live expression=closed line=( •_•)
```

`mode=emote` means a custom face is holding. `mode=live` means the camera face is showing. `expression` is `closed`, `open`, or `wide`.

`hearing=yes` means the capture is running. `hearing=no` means the capture is closed. Mute does not change that.

The Companion module for these buttons is https://github.com/Inphidel/companion-module-prompt-face . Its presets replace a raw TCP button: mute, a key that shows the live face, a hold-to-show emote, and a toggle emote that stays until the next press.

```bash
printf 'mouth toggle\n' | nc 192.168.1.20 8772
```

Replace `192.168.1.20` with the address of the face computer. A Stream Deck program that can send TCP, such as Bitfocus Companion's Generic TCP action, sends the same line. The stock Elgato button that only opens a web page does not send this. The button has to be a raw TCP send, or a shell command that runs the `nc` line above.

On the face computer, allow the port through the firewall:

```bash
sudo ufw allow 8772/tcp comment 'prompt-face commands'
```

Devices on the same network can then connect. A router port forward is not required for a Stream Deck on that network. Anyone who can reach the port can change the face. Set `command.token` in `config.json` to a word of your own, and put that word first on every line:

```text
secret mouth sound
secret emote hello
```

Leave the token empty if the network is already private enough. The token stays in `config.json`, which is not published.

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
5. `textface.py` picks the face string, the arm marks, or a whole-line emote. A mute flag picks the muted slot before the camera expression.
6. `paint.py` draws that line in the middle of a 1400 by 180 panel. The font size stays put. Arms grow outward and clip at the edge.
7. `obs_bridge.py` writes the panel into the OBS image source and can message Advanced Scene Switcher.
8. `voice.py` reads the microphone with `pw-record` only while the mouth source is sound. Closing sound mode stops that process. Its mute flag does not call a volume control.
9. `command_server.py` listens on TCP port 8772 for mouth, mute, and emote lines from another computer.
10. `author.py` serves the expression page on localhost.
11. `server.py` publishes the state JSON on the localhost WebSocket.

`compose.py` is the rule list. A rule names the returns it cares about. While they hold, OBS can hear `name=on`. When they let go, it hears `name=off`.

## States

| Field | Values |
| --- | --- |
| face | present, absent |
| mouth | closed, open, wide |
| muted | off, on |
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
