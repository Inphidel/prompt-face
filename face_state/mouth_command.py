"""Toggle the mouth between the jaw and the microphone.

face-mouth
face-mouth jaw
face-mouth sound
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
    return f"http://{host}:{port}/api/mouth"


def send(payload: dict) -> dict:
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
        raise SystemExit(detail.strip() or f"mouth command failed ({error.code})") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"the face tracker is not answering: {error.reason}") from error


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("--toggle", "toggle"):
        payload = {"toggle": True}
    elif args[0] in ("-h", "--help"):
        raise SystemExit("usage: face-mouth\n       face-mouth jaw\n       face-mouth sound")
    elif args[0] in ("jaw", "sound"):
        payload = {"source": args[0]}
    else:
        raise SystemExit("usage: face-mouth\n       face-mouth jaw\n       face-mouth sound")
    status = send(payload)
    hearing = ""
    if status.get("source") == "sound" and not status.get("hearing"):
        hearing = " (no mic yet)"
    print(f"{status.get('source')}{hearing}")


if __name__ == "__main__":
    main()
