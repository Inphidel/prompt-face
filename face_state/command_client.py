"""Send one TCP command to a running Prompt Face.

On the face computer:

    python -m face_state.command_client mouth toggle
    python -m face_state.command_client emote hello
    python -m face_state.command_client emote --clear

From another computer, pass the face computer's address:

    python -m face_state.command_client --host 192.168.1.20 mouth sound
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def default_target() -> tuple[str, int]:
    host, port = "127.0.0.1", 8772
    try:
        config = json.loads((ROOT / "config.json").read_text())
        listen = str((config.get("command") or {}).get("listen") or "")
        if ":" in listen:
            configured, port_text = listen.rsplit(":", 1)
            port = int(port_text)
            if configured and configured != "0.0.0.0":
                host = configured
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return host, port


def send(host: str, port: int, line: str, timeout: float = 3) -> str:
    """Open a connection, send one line, and return the reply."""
    with socket.create_connection((host, port), timeout=timeout) as conn:
        conn.sendall((line + "\n").encode())
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
    return data.decode("utf-8", errors="replace").strip()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Send one Prompt Face command")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("words", nargs=argparse.REMAINDER)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    words = args.words
    if words[:1] == ["--"]:
        words = words[1:]
    if not words:
        raise SystemExit(
            "usage: prompt-command [--host HOST] [--port PORT] mouth toggle\n"
            "       prompt-command emote TEXT\n"
            "       prompt-command emote toggle TEXT\n"
            "       prompt-command emote --clear"
        )
    host, port = default_target()
    if args.host:
        host = args.host
    if args.port:
        port = args.port
    try:
        print(send(host, port, " ".join(words)))
    except OSError as error:
        raise SystemExit(f"command port {host}:{port} did not answer: {error}") from error


if __name__ == "__main__":
    main()
