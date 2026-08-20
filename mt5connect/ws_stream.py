"""WebSocket tick-stream client for the remote MT5 backend."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

import websockets

logger = logging.getLogger(__name__)

MessageHandler = Callable[[dict], None]


class WSStreamClient:
    """Connects to the server WS hub, pushes subscribe state, and feeds
    incoming tick messages to ``message_handler``. Auto-reconnects with
    exponential backoff and flushes pending messages on (re)connect."""

    def __init__(self, url: str, message_handler: MessageHandler,
                 initial_delay_s: float = 1.0, max_delay_s: float = 60.0) -> None:
        self._url = url
        self._handler = message_handler
        self._initial_delay_s = initial_delay_s
        self._max_delay_s = max_delay_s
        self._pending: list[dict] = []
        self._task: asyncio.Task | None = None
        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._stop = False

    async def start(self) -> None:
        self._task = asyncio.get_event_loop().create_task(
            self._run(), name="WSStreamClient._run")

    async def stop(self) -> None:
        self._stop = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._ws = None

    async def send(self, payload: dict) -> None:
        """Queue a state message; sent immediately if connected, else
        flushed on the next (re)connect. Replaces a pending message of the
        same ``type``."""
        self._pending = [p for p in self._pending
                         if p.get("type") != payload.get("type")]
        self._pending.append(payload)
        if self._ws is not None:
            await self._flush(self._ws)

    async def _run(self) -> None:
        delay = self._initial_delay_s
        while not self._stop:
            try:
                async with websockets.connect(self._url) as ws:
                    self._ws = ws
                    delay = self._initial_delay_s
                    await self._flush(ws)
                    await self.send({"type": "hello", "role": "adapter"})
                    async for raw in ws:
                        if self._stop:
                            break
                        try:
                            self._handler(json.loads(raw))
                        except Exception:
                            logger.exception("WSStreamClient handler failed")
            except Exception as exc:
                logger.warning("WSStreamClient connect failed: %s", exc)
            finally:
                self._ws = None
            if self._stop:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._max_delay_s)

    async def _flush(self, ws) -> None:
        for payload in self._pending:
            await ws.send(json.dumps(payload))
        self._pending.clear()
