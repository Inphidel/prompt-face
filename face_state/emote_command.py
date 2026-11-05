"""Replace the whole face line from a Stream Deck button.

The text after the command is the entire emote. It is drawn at the same size
as the live face and stays in the center of the panel.

face-emote '¯\\_(ツ)_/¯'
face-emote --clear
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"


def author_url() -> str:
    host, port = "127.0.0.1", "8771"
    try:
        config = json.loads(CONFIG.read_text())
        listen = str((config.get("author") or {}).get("listen") or "")
        if ":" in listen:
            host, port = listen.rsplit(":", 1)
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return f"http://{host}:{port}/api/emote"


def send(text: str | None) -> dict:
    payload = {"clear": True} if text is None else {"text": text}
    request = urllib.request.Request(
        author_url(),
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise SystemExit(detail.strip() or f"emote command failed ({error.code})") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"the face tracker is not answering: {error.reason}") from error


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        raise SystemExit("usage: face-emote TEXT\n       face-emote --clear")
    text = None if args[0] in ("--clear", "-c") else " ".join(args)
    result = send(text)
    shown = result.get("text")
    print("live" if shown is None else shown)


if __name__ == "__main__":
    main()
