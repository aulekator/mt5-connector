"""Tick relay hub: the MQL5 EA publishes ticks, adapters subscribe."""
import asyncio
import json
import logging
import os

from websockets.asyncio.server import serve

TICK_FLAG_BID = 2
TICK_FLAG_ASK = 4

logger = logging.getLogger(__name__)


class TickHub:
    def __init__(self):
        self._eas = set()
        self._adapters = set()
        self._symbols = set()

    async def handler(self, ws):
        try:
            async for raw in ws:
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "hello" and msg.get("role") == "ea":
                    self._eas.add(ws)
                    logger.info("ea connected")
                elif kind == "hello" and msg.get("role") == "adapter":
                    self._adapters.add(ws)
                    logger.info("adapter connected")
                elif kind == "subscribe":
                    self._symbols.update(msg.get("symbols", []))
                    logger.info("subscribe")
                elif kind == "unsubscribe":
                    self._symbols.difference_update(msg.get("symbols", []))
                    logger.info("unsubscribe")
                elif self._is_tick(msg):
                    flags = int(msg.get("flags"))
                    # we only need to send the ticks if the bid or the ask price has changed
                    if bool(flags & TICK_FLAG_BID) or bool( flags & TICK_FLAG_ASK):
                        await self._broadcast(raw)
                else:
                    logger.info(f"unkown kind {kind}")
        except Exception:
            pass
        finally:
            self._adapters.discard(ws)
            self._eas.discard(ws)

    @staticmethod
    def _is_tick(msg: dict) -> bool:
        return "symbol" in msg and (
            "bid" in msg or "ask" in msg or "time_msec" in msg)

    async def _broadcast(self, raw):
        for a in list(self._adapters):
            try:
                await a.send(raw)
            except Exception:
                self._adapters.discard(a)


async def main():
    port = int(os.environ.get("WS_PORT", "9000"))
    hub = TickHub()
    async with serve(hub.handler, "0.0.0.0", port):
        await asyncio.Future()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting WSHUB")
    asyncio.run(main())
