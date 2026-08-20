import asyncio
import json

import pytest
import websockets
from websockets.asyncio.server import serve

from ws_server import TickHub


async def _run_hub():
    hub = TickHub()
    server = await serve(hub.handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return hub, server, port


async def _connect(port, role):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    await ws.send(json.dumps({"type": "hello", "role": role}))
    return ws


@pytest.mark.asyncio
async def test_tick_relayed_to_adapter():
    hub, server, port = await _run_hub()
    ea = await _connect(port, "ea")
    adapter = await _connect(port, "adapter")
    await asyncio.sleep(0.05)

    # new-format tick: no "type" key, all-string values
    await ea.send(json.dumps({"symbol": "EURUSD", "time": "2024.06.01 10:00:00",
                              "ask": "1.0801", "bid": "1.08", "volume": "0",
                              "last": "0.0", "time_msec": "1712345678123",
                              "flags": "2"}))
    got = json.loads(await asyncio.wait_for(adapter.recv(), timeout=1))
    assert got["symbol"] == "EURUSD" and got["bid"] == "1.08"

    await adapter.close()
    await ea.close()
    server.close()


@pytest.mark.asyncio
async def test_multiple_ea_connections_all_relay():
    hub, server, port = await _run_hub()
    ea1 = await _connect(port, "ea")
    ea2 = await _connect(port, "ea")
    adapter = await _connect(port, "adapter")
    await asyncio.sleep(0.05)

    await ea1.send(json.dumps({"symbol": "EURUSD", "bid": "1.08",
                               "time_msec": "1"}))
    m1 = json.loads(await asyncio.wait_for(adapter.recv(), timeout=1))
    assert m1["symbol"] == "EURUSD"

    await ea2.send(json.dumps({"symbol": "XAUUSD", "ask": "2340.5",
                               "time_msec": "2"}))
    m2 = json.loads(await asyncio.wait_for(adapter.recv(), timeout=1))
    assert m2["symbol"] == "XAUUSD"

    assert len(hub._eas) == 2

    await adapter.close()
    await ea1.close()
    await ea2.close()
    server.close()


@pytest.mark.asyncio
async def test_subscribe_not_forwarded_to_ea():
    hub, server, port = await _run_hub()
    ea = await _connect(port, "ea")
    adapter = await _connect(port, "adapter")
    await asyncio.sleep(0.05)

    await adapter.send(json.dumps({"type": "subscribe", "symbols": ["EURUSD"]}))
    await asyncio.sleep(0.05)
    assert hub._symbols == {"EURUSD"}

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(ea.recv(), timeout=0.3)

    await adapter.close()
    await ea.close()
    server.close()


@pytest.mark.asyncio
async def test_ea_disconnect_releases_slot():
    hub, server, port = await _run_hub()
    ea = await _connect(port, "ea")
    await asyncio.sleep(0.05)
    assert len(hub._eas) == 1

    await ea.close()
    await asyncio.sleep(0.05)
    assert len(hub._eas) == 0

    server.close()
