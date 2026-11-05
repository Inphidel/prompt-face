"""Hear the microphone and choose whether the mouth follows it or the jaw.

An empty device name tells pw-record to use the default PipeWire source.
Set mouth.device in config.json when that default is the webcam microphone
and the voice should come from another input. The tracker opens the
microphone only while the mouth source is sound.
"""

from __future__ import annotations

import array
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
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._warned = False
        self._thread = threading.Thread(target=self._run, name="face-state-voice", daemon=True)
        self._thread.start()

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


class MouthControl:
    """Jaw or sound, switched by the button."""

    def __init__(self, config: dict | None):
        mouth = (config or {}).get("mouth") or {}
        source = mouth.get("source") or "jaw"
        self._mode = source if source in ("jaw", "sound") else "jaw"
        self._lock = threading.Lock()
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
        if self._mode == "sound":
            self.ear.start()

    def source(self) -> str:
        with self._lock:
            return self._mode

    def status(self) -> dict:
        with self._lock:
            source = self._mode
        return {"source": source, "hearing": self.ear.hearing() if source == "sound" else False}

    def level(self) -> float | None:
        if self.source() != "sound":
            return None
        return self.ear.level()

    def set_source(self, source: str | None = None, toggle: bool = False) -> dict:
        with self._lock:
            if toggle or source in (None, ""):
                self._mode = "sound" if self._mode == "jaw" else "jaw"
            elif source in ("jaw", "sound"):
                self._mode = source
            else:
                raise ValueError("mouth needs to be jaw or sound")
            mode = self._mode
        if mode == "sound":
            self.ear.start()
        else:
            self.ear.stop()
        return self.status()

    def close(self) -> None:
        self.ear.stop()
