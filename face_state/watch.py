"""Print face-state changes from the local tracker socket.

Run this in a second terminal while the tracker is already running:
python -m face_state.watch
"""

from __future__ import annotations

import argparse
import asyncio
import json

import websockets

from face_state.reduce import FIELDS


async def watch(url: str) -> None:
    async with websockets.connect(url) as websocket:
        first = True
        async for raw in websocket:
            state = json.loads(raw)
            summary = " ".join(f"{name}={state[name]}" for name in FIELDS)
            extra = [
                name for name in (state.get("changed") or [])
                if name not in FIELDS and isinstance(state.get(name), str)
            ]
            if extra:
                summary = summary + " " + " ".join(f"{name}={state[name]}" for name in extra)
            if first:
                print(f"now {summary}", flush=True)
                first = False
                continue
            if state.get("changed"):
                print(f"change {summary}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch face-state changes")
    parser.add_argument("--url", default="ws://127.0.0.1:8770/state")
    args = parser.parse_args()
    try:
        asyncio.run(watch(args.url))
    except KeyboardInterrupt:
        print("stopped", flush=True)


if __name__ == "__main__":
    main()
