"""Serve the latest state on a localhost websocket.

The default address is ws://127.0.0.1:8770/state. Each message is one JSON
object. changed lists the fields that flipped on that update. A browser
cannot open this URL by visiting it.
"""

from __future__ import annotations

import asyncio
import json

import websockets


class Hub:
    def __init__(self):
        self.latest = None
        self.version = 0
        self._cond = asyncio.Condition()

    def publish(self, state: dict) -> None:
        asyncio.get_running_loop().create_task(self._publish(state))

    async def _publish(self, state: dict) -> None:
        async with self._cond:
            self.latest = state
            self.version += 1
            self._cond.notify_all()

    async def handler(self, websocket) -> None:
        seen = -1
        try:
            while True:
                async with self._cond:
                    await self._cond.wait_for(lambda: self.version != seen and self.latest is not None)
                    seen = self.version
                    payload = json.dumps(self.latest)
                await websocket.send(payload)
        except websockets.ConnectionClosed:
            return


async def serve(hub: Hub, host: str, port: int):
    async with websockets.serve(hub.handler, host, port):
        await asyncio.Future()
