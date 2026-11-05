"""TCP commands for a button on another computer.

The expression page and the state socket stay on localhost. This port is
the one a Stream Deck on another machine opens. One line is one command,
and the reply is one line.

Jaw mode does not start a capture. ``mouth sound`` is what opens the
microphone, and ``mouth jaw`` closes it again. PipeWire shares that
capture, so OBS can keep the same source.

    mouth
    mouth jaw
    mouth sound
    mouth on
    mouth off
    mute
    mute on
    mute off
    emote TEXT
    emote --clear
    clear
    status
    ping

``mouth`` with no word toggles. ``mouth on`` is sound and ``mouth off`` is
jaw. When ``command.token`` is set, the line starts with that word:

    secret mouth sound
"""

from __future__ import annotations

import hmac
import socket
import threading

from face_state.textface import clean_line

DEFAULT_LISTEN = "0.0.0.0:8772"
MAX_LINE = 4096


def parse_listen(config: dict | None) -> tuple[str, int]:
    """Host and port for the command socket. The default is every interface."""
    command = (config or {}).get("command") or {}
    listen = str(command.get("listen") or DEFAULT_LISTEN)
    if ":" not in listen:
        raise ValueError("command.listen needs a host and a port")
    host, port = listen.rsplit(":", 1)
    return host or "0.0.0.0", int(port)


def token_of(config: dict | None) -> str:
    command = (config or {}).get("command") or {}
    token = command.get("token") or ""
    return str(token)


def _mouth_reply(status: dict) -> str:
    hearing = "yes" if status.get("hearing") else "no"
    muted = "yes" if status.get("muted") else "no"
    return f"ok mouth={status.get('source')} hearing={hearing} muted={muted}"


def status_line(mouth, board) -> str:
    """One status line. Everything after ``line=`` is the face text."""
    mouth_status = mouth.status()
    face = board.line_status()
    mode = "emote" if face.get("text") is not None else "live"
    expression = face.get("expression") if face.get("expression") in ("closed", "open", "wide") else "closed"
    line = str(face.get("line") or "").replace("\n", " ").replace("\r", " ")
    return f"{_mouth_reply(mouth_status)} mode={mode} expression={expression} line={line}"


def _emote_reply(status: dict) -> str:
    shown = status.get("text")
    return "ok emote=live" if shown is None else f"ok emote={shown}"


def handle_line(line: str, board, mouth, token: str = "") -> str | None:
    """Run one command. A blank line has no reply."""
    text = line.strip()
    if not text:
        return None
    if token:
        given, _, rest = text.partition(" ")
        if not hmac.compare_digest(given.encode(), token.encode()):
            return "err unauthorized"
        text = rest
    if not text:
        return "err unauthorized"
    command, _, argument = text.partition(" ")
    command = command.lower()
    if command == "ping":
        return "ok pong"
    if command == "status":
        return status_line(mouth, board)
    if command == "mute":
        word = argument.strip().lower()
        try:
            if word in ("", "toggle"):
                status = mouth.set_mute(toggle=True)
            elif word in ("on", "mute", "yes"):
                status = mouth.set_mute(True)
            elif word in ("off", "unmute", "no"):
                status = mouth.set_mute(False)
            else:
                return "err mute needs on, off, or toggle"
        except (OSError, RuntimeError) as error:
            return f"err {error}"
        return _mouth_reply(status)
    if command == "mouth":
        word = argument.strip().lower()
        if word in ("", "toggle"):
            status = mouth.set_source(toggle=True)
        elif word in ("jaw", "off"):
            status = mouth.set_source("jaw")
        elif word in ("sound", "on"):
            status = mouth.set_source("sound")
        else:
            return "err mouth needs jaw, sound, on, off, or toggle"
        return _mouth_reply(status)
    if command == "clear":
        return _emote_reply(board.set_line(None))
    if command == "emote":
        try:
            if argument in ("--clear", "-c"):
                return _emote_reply(board.set_line(None))
            if argument == "":
                return "err emote needs text or --clear"
            return _emote_reply(board.set_line(clean_line(argument)))
        except ValueError as error:
            return f"err {error}"
    return "err unknown command"


def _client(conn: socket.socket, peer: str, board, mouth, token: str) -> None:
    pending = b""
    try:
        conn.settimeout(8)
        while True:
            if b"\n" not in pending:
                chunk = conn.recv(1024)
                if not chunk:
                    if pending.strip():
                        reply = handle_line(pending.decode("utf-8"), board, mouth, token)
                        if reply:
                            conn.sendall((reply + "\n").encode())
                    return
                pending += chunk
                if len(pending) > MAX_LINE and b"\n" not in pending:
                    conn.sendall(b"err the command is too long\n")
                    return
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                if len(raw) > MAX_LINE:
                    conn.sendall(b"err the command is too long\n")
                    continue
                reply = handle_line(raw.decode("utf-8"), board, mouth, token)
                if reply:
                    print(f"command {peer} {reply}", flush=True)
                    conn.sendall((reply + "\n").encode())
    except (TimeoutError, UnicodeError, ValueError, OSError):
        try:
            conn.sendall(b"err the command was not accepted\n")
        except OSError:
            pass
    finally:
        conn.close()


def serve_commands(config: dict | None, board, mouth) -> None:
    """Listen until the tracker exits. A busy port does not stop the face."""
    try:
        host, port = parse_listen(config)
    except (TypeError, ValueError) as error:
        print(f"command port was not opened: {error}", flush=True)
        return
    token = token_of(config)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((host, port))
    except OSError as error:
        print(f"command port {host}:{port} did not open: {error}", flush=True)
        server.close()
        return
    server.listen(8)
    server.settimeout(0.5)
    print(f"command port tcp://{host}:{port}", flush=True)
    if host in ("0.0.0.0", ""):
        print(
            f"allow TCP {port} through the firewall so another computer can connect",
            flush=True,
        )

    def accept_loop() -> None:
        while True:
            try:
                conn, address = server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            peer = address[0]
            threading.Thread(
                target=_client,
                args=(conn, peer, board, mouth, token),
                name="face-state-command",
                daemon=True,
            ).start()

    threading.Thread(target=accept_loop, name="face-state-command-accept", daemon=True).start()
