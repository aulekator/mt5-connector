import asyncio
import json

import pytest
import websockets
from websockets.asyncio.server import serve

from mt5connect.ws_stream import WSStreamClient


async def _echo_server():
    async def echo(ws):
        async for raw in ws:
            await ws.send(raw)

    server = await serve(echo, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


@pytest.mark.asyncio
async def test_receives_tick_messages():
    server, port = await _echo_server()
    received = []

    client = WSStreamClient(f"ws://127.0.0.1:{port}", received.append,
                            initial_delay_s=0.01, max_delay_s=0.05)
    await client.start()
    await asyncio.sleep(0.05)

    # The echo server only echoes after we connect, so drive a send.
    await client.send({"type": "hello", "role": "adapter"})
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.01)

    assert received and received[0]["type"] == "hello"

    await client.stop()
    server.close()


@pytest.mark.asyncio
async def test_pending_subscribe_flushed_on_connect():
    seen = []

    async def recorder(ws):
        async for raw in ws:
            seen.append(json.loads(raw))

    server = await serve(recorder, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    client = WSStreamClient(f"ws://127.0.0.1:{port}", lambda m: None,
                            initial_delay_s=0.01, max_delay_s=0.05)
    await client.send({"type": "subscribe", "symbols": ["EURUSD"]})
    await client.start()
    for _ in range(100):
        if seen:
            break
        await asyncio.sleep(0.01)

    assert seen and seen[0]["type"] == "subscribe" and "EURUSD" in seen[0]["symbols"]

    await client.stop()
    server.close()


@pytest.mark.asyncio
async def test_send_replaces_pending_of_same_type():
    seen = []

    async def recorder(ws):
        async for raw in ws:
            seen.append(json.loads(raw))

    server = await serve(recorder, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    client = WSStreamClient(f"ws://127.0.0.1:{port}", lambda m: None,
                            initial_delay_s=0.01, max_delay_s=0.05)
    await client.send({"type": "subscribe", "symbols": ["EURUSD"]})
    await client.send({"type": "subscribe", "symbols": ["EURUSD", "GBPUSD"]})
    await client.start()
    for _ in range(100):
        if seen:
            break
        await asyncio.sleep(0.01)

    assert seen and seen[0]["symbols"] == ["EURUSD", "GBPUSD"]

    await client.stop()
    server.close()
