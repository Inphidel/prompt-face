"""Local page for the running face.

The page edits textfaces.json and rules.json. POST /api/emote replaces the
whole centered line, and an empty text or clear returns the camera face.
POST /api/mouth switches the mouth between the jaw and the microphone.
face-emote and face-mouth call those two routes. The page is served only
on the author listen address, which defaults to localhost.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from face_state.compose import VOCAB, save_rules
from face_state.textface import list_images, load_catalog, load_textfaces, mirror, safe_image, save_textfaces

PAGE = (Path(__file__).resolve().parent / "author.html").read_text()
MAX_BODY = 200_000
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def serve_author(host: str, port: int, rules_path: Path, textfaces_path: Path, board=None, mouth=None) -> None:
    """Serve the rules page on localhost. A busy port does not stop the tracker."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._bytes(200, PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/api/vocab":
                self._bytes(200, json.dumps(VOCAB).encode(), "application/json")
            elif path == "/api/rules":
                if rules_path.exists():
                    body = rules_path.read_bytes()
                else:
                    body = b'{"rules": []}\n'
                self._bytes(200, body, "application/json")
            elif path == "/api/text-faces":
                self._bytes(200, json.dumps(load_catalog(), ensure_ascii=False).encode(), "application/json")
            elif path == "/api/textfaces":
                try:
                    payload = load_textfaces(textfaces_path)
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    self._json(400, {"error": str(error)})
                    return
                self._bytes(200, json.dumps(payload, ensure_ascii=False).encode(), "application/json")
            elif path == "/api/face-images":
                query = parse_qs(urlparse(self.path).query)
                library = (query.get("library") or [None])[0]
                if library is None:
                    try:
                        library = load_textfaces(textfaces_path).get("library") or ""
                    except (OSError, ValueError, json.JSONDecodeError):
                        library = ""
                payload = {"library": library, "images": list_images(library)}
                self._bytes(200, json.dumps(payload, ensure_ascii=False).encode(), "application/json")
            elif path == "/api/mouth":
                if mouth is None:
                    self._json(503, {"error": "the face tracker is not ready"})
                    return
                query = parse_qs(urlparse(self.path).query)
                if "source" in query or "toggle" in query:
                    try:
                        status = mouth.set_source(
                            (query.get("source") or [None])[0],
                            toggle="toggle" in query,
                        )
                    except ValueError as error:
                        self._json(400, {"error": str(error)})
                        return
                    print(f"mouth source={status['source']}", flush=True)
                    self._json(200, status)
                    return
                self._json(200, mouth.status())
            elif path == "/api/emote":
                if board is None:
                    self._json(503, {"error": "the face tracker is not ready"})
                    return
                query = parse_qs(urlparse(self.path).query)
                if "text" in query or "clear" in query:
                    try:
                        text = None if "clear" in query else (query.get("text") or [""])[0]
                        status = board.set_line(text)
                    except ValueError as error:
                        self._json(400, {"error": str(error)})
                        return
                    self._json(200, status)
                    return
                self._json(200, board.line_status())
            elif path == "/api/face-file":
                query = parse_qs(urlparse(self.path).query)
                image = safe_image((query.get("path") or [""])[0])
                if image is None:
                    self._bytes(404, b"not found\n", "text/plain; charset=utf-8")
                    return
                self._bytes(200, image.read_bytes(), IMAGE_TYPES[image.suffix.lower()])
            else:
                self._bytes(404, b"not found\n", "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/mouth":
                if mouth is None:
                    self._json(503, {"error": "the face tracker is not ready"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY:
                    self._json(400, {"error": "that mouth choice did not fit"})
                    return
                try:
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise ValueError("mouth needs to be jaw or sound")
                    status = mouth.set_source(payload.get("source"), toggle=bool(payload.get("toggle")))
                except (ValueError, json.JSONDecodeError) as error:
                    self._json(400, {"error": str(error)})
                    return
                print(f"mouth source={status['source']}", flush=True)
                self._json(200, status)
                return
            if path == "/api/emote":
                if board is None:
                    self._json(503, {"error": "the face tracker is not ready"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY:
                    self._json(400, {"error": "that emote did not fit"})
                    return
                try:
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise ValueError("the emote needs to be one line")
                    if payload.get("clear"):
                        text = None
                    else:
                        text = payload.get("text")
                    status = board.set_line(text)
                except (ValueError, json.JSONDecodeError) as error:
                    self._json(400, {"error": str(error)})
                    return
                shown = status.get("text")
                print("emote cleared" if shown is None else f"emote={shown}", flush=True)
                self._json(200, status)
                return
            if path != "/api/text-faces/mirror":
                self._bytes(404, b"not found\n", "text/plain; charset=utf-8")
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                self._json(400, {"error": "that face did not fit"})
                return
            try:
                payload = json.loads(self.rfile.read(length))
                text = payload.get("text") if isinstance(payload, dict) else None
                if not isinstance(text, str) or len(text) > 240:
                    raise ValueError("the face text needs to be a short string")
            except (ValueError, json.JSONDecodeError) as error:
                self._json(400, {"error": str(error)})
                return
            cleaned = text.strip()
            self._json(200, {"center": cleaned, "left": mirror(cleaned), "right": cleaned})

        def do_PUT(self) -> None:
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                self._json(400, {"error": "that did not fit"})
                return
            try:
                payload = json.loads(self.rfile.read(length))
            except json.JSONDecodeError as error:
                self._json(400, {"error": str(error)})
                return
            if path == "/api/rules":
                try:
                    cleaned = save_rules(rules_path, payload)
                except (OSError, ValueError) as error:
                    self._json(400, {"error": str(error)})
                    return
                self._json(200, {"rules": cleaned})
                return
            if path == "/api/textfaces":
                try:
                    cleaned = save_textfaces(textfaces_path, payload)
                except (OSError, ValueError) as error:
                    self._json(400, {"error": str(error)})
                    return
                self._json(200, cleaned)
                return
            self._bytes(404, b"not found\n", "text/plain; charset=utf-8")

        def log_message(self, fmt: str, *args) -> None:
            return

        def _bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._bytes(status, json.dumps(payload).encode(), "application/json")

    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as error:
        print(f"rules page did not start on {host}:{port}: {error}", flush=True)
        return
    thread = threading.Thread(target=server.serve_forever, name="face-state-rules", daemon=True)
    thread.start()
    print(f"rules page http://{host}:{port}", flush=True)
