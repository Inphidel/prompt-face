"""Hear the microphone and choose whether the mouth follows it or the jaw.

An empty device name tells pw-record to use the default PipeWire source.
Set mouth.device in config.json when that default is the webcam microphone
and the voice should come from another input.

The capture starts only after the mouth source is switched to sound, and it
stops again on jaw. pw-record is one more PipeWire listener. It does not
take the device away from OBS or from any other program that is already
recording that source.

Mute is a PipeWire mute on that same source. OBS hears silence while it is
muted, and the capture is closed. Unmute clears that PipeWire mute. The
tracker also clears a mute it set itself when it exits. A mute made in
another volume control is left alone.
"""

from __future__ import annotations

import array
import json
import math
import subprocess
import threading
import time

# Empty means the PipeWire default source. A node name selects one input.
DEFAULT_DEVICE = ""
RATE = 16000
BLOCK = 320  # 20 ms


def smooth(level: float, rms: float, attack: float, release: float) -> float:
    """Rise quickly when the voice gets louder, and fall a little slower."""
    rate = attack if rms > level else release
    return level + rate * (rms - level)


def mouth_from_level(level: float, state: str, quiet: float, loud: float) -> str:
    """Closed under the quiet line, open in the middle, wide when it is loud."""
    if state == "wide":
        if level < quiet:
            return "closed"
        if level < loud * 0.65:
            return "open"
        return "wide"
    if state == "open":
        if level >= loud:
            return "wide"
        if level < quiet * 0.6:
            return "closed"
        return "open"
    if level >= loud:
        return "wide"
    if level >= quiet:
        return "open"
    return "closed"


class VoiceEar:
    """Read one PipeWire source and keep a smoothed loudness."""

    def __init__(self, device: str, attack: float = 0.55, release: float = 0.22):
        # Keep an explicit empty string. It means the default source.
        self.device = DEFAULT_DEVICE if device is None else device
        self.attack = float(attack)
        self.release = float(release)
        self._level = 0.0
        self._hearing = False
        self._proc = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._warned = False

    def level(self) -> float | None:
        with self._lock:
            if not self._hearing:
                return None
            return self._level

    def hearing(self) -> bool:
        with self._lock:
            return self._hearing

    def start(self) -> None:
        """Open the shared capture and wait until it is up, or has failed."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._warned = False
        self._thread = threading.Thread(target=self._run, name="face-state-voice", daemon=True)
        self._thread.start()
        # The command reply should say whether the microphone actually opened.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if self.hearing() or self._warned:
                return
            time.sleep(0.02)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        if self._thread is not None:
            self._thread.join(timeout=1)
            with self._lock:
                proc = self._proc
            if self._thread.is_alive() and proc is not None and proc.poll() is None:
                proc.kill()
                self._thread.join(timeout=1)
        with self._lock:
            self._hearing = False

    def _command(self) -> list[str]:
        """pw-record arguments. --target is omitted for the default source."""
        command = [
            "pw-record",
            "--rate",
            str(RATE),
            "--channels",
            "1",
            "--format",
            "f32",
            "--latency",
            "20ms",
            "-a",
            "-",
        ]
        if self.device:
            command[1:1] = ["--target", self.device]
        return command

    def _run(self) -> None:
        command = self._command()
        while not self._stop.is_set():
            try:
                proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except OSError as error:
                self._fail(error)
                return
            with self._lock:
                self._proc = proc
                self._hearing = True
            assert proc.stdout is not None
            try:
                while not self._stop.is_set():
                    data = proc.stdout.read(BLOCK * 4)
                    if not data:
                        break
                    usable = len(data) - (len(data) % 4)
                    if usable < 4:
                        continue
                    samples = array.array("f")
                    samples.frombytes(data[:usable])
                    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
                    with self._lock:
                        self._level = smooth(self._level, rms, self.attack, self.release)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                with self._lock:
                    self._proc = None
                    self._hearing = False
            if not self._stop.is_set():
                self._fail("the microphone stopped")
                time.sleep(1)

    def _fail(self, error) -> None:
        if not self._warned:
            self._warned = True
            print(f"microphone did not start: {error}", flush=True)


def _source_nodes() -> list[dict]:
    raw = subprocess.check_output(["pw-dump"], text=True)
    found = []
    for node in json.loads(raw):
        if node.get("type") != "PipeWire:Interface:Node":
            continue
        props = (node.get("info") or {}).get("props") or {}
        if props.get("media.class") == "Audio/Source" and node.get("id") is not None:
            found.append({"id": str(node["id"]), "name": str(props.get("node.name") or "")})
    return found


def source_id(device: str) -> str:
    """PipeWire id for the named source. An empty name uses the default source."""
    nodes = _source_nodes()
    if device:
        for node in nodes:
            if node["name"] == device:
                return node["id"]
        raise RuntimeError("the chosen microphone was not found")
    status = subprocess.check_output(["wpctl", "status"], text=True, errors="replace")
    in_sources = False
    for line in status.splitlines():
        if "Sources:" in line:
            in_sources = True
            continue
        if in_sources and line.endswith(":") and "Sources:" not in line:
            break
        if in_sources and "*" in line:
            number = "".join(ch for ch in line if ch.isdigit())
            if number:
                return str(int(number))
    if len(nodes) == 1:
        return nodes[0]["id"]
    raise RuntimeError("the default microphone was not found")


def hardware_muted(device: str) -> bool:
    info = subprocess.check_output(["wpctl", "get-volume", source_id(device)], text=True, errors="replace")
    return "MUTED" in info


def set_hardware_mute(device: str, muted: bool) -> None:
    """Mute or unmute the source OBS is sharing. This does not open the device."""
    result = subprocess.run(
        ["wpctl", "set-mute", source_id(device), "1" if muted else "0"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or "the microphone mute did not change")


class MouthControl:
    """Jaw or sound, plus a mute that silences the shared microphone."""

    def __init__(self, config: dict | None):
        mouth = (config or {}).get("mouth") or {}
        source = mouth.get("source") or "jaw"
        self._mode = source if source in ("jaw", "sound") else "jaw"
        self._lock = threading.Lock()
        self._muted = False
        self._we_muted = False
        device = mouth.get("device")
        if not isinstance(device, str):
            device = DEFAULT_DEVICE
        self.ear = VoiceEar(
            device,
            attack=float(mouth.get("attack", 0.55)),
            release=float(mouth.get("release", 0.22)),
        )
        self.quiet = float(mouth.get("quiet", 0.004))
        self.loud = float(mouth.get("loud", 0.03))
        try:
            self._muted = hardware_muted(self.ear.device)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            print(f"microphone mute was not read: {error}", flush=True)
        if self._mode == "sound" and not self._muted:
            self.ear.start()

    def source(self) -> str:
        with self._lock:
            return self._mode

    def muted(self) -> bool:
        with self._lock:
            return self._muted

    def status(self) -> dict:
        with self._lock:
            source = self._mode
            muted = self._muted
        hearing = bool(source == "sound" and not muted and self.ear.hearing())
        return {"source": source, "hearing": hearing, "muted": muted}

    def level(self) -> float | None:
        if self.muted() or self.source() != "sound":
            return None
        return self.ear.level()

    def _listen(self) -> None:
        """Open the capture only while sound mode is on and the mic is not muted."""
        with self._lock:
            listen = self._mode == "sound" and not self._muted
        if listen:
            self.ear.start()
        else:
            self.ear.stop()

    def set_source(self, source: str | None = None, toggle: bool = False) -> dict:
        with self._lock:
            if toggle or source in (None, ""):
                self._mode = "sound" if self._mode == "jaw" else "jaw"
            elif source in ("jaw", "sound"):
                self._mode = source
            else:
                raise ValueError("mouth needs to be jaw or sound")
        self._listen()
        return self.status()

    def set_mute(self, muted: bool | None = None, toggle: bool = False) -> dict:
        """Mute the shared source, or clear a mute. The capture stays closed while muted."""
        with self._lock:
            if toggle or muted is None:
                desired = not self._muted
            elif muted:
                desired = True
            else:
                desired = False
        set_hardware_mute(self.ear.device, desired)
        with self._lock:
            self._muted = desired
            self._we_muted = desired
        self._listen()
        return self.status()

    def close(self) -> None:
        self.ear.stop()
        if self._we_muted:
            try:
                set_hardware_mute(self.ear.device, False)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                print(f"microphone mute was left on: {error}", flush=True)
            self._we_muted = False
            self._muted = False
