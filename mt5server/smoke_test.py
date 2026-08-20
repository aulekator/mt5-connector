"""End-to-end smoke test: EA -> WS hub -> connector.

Run from the host venv after the container is up (``make run``):
    ../.venv/bin/python smoke_test.py

Reads ../.env for MT5_ACCOUNT, MT5_PASSWORD, MT5_SERVER, MT5_SYMBOLS,
MT5_SERVER_URL (default http://localhost:5000). Non-zero exit on any FAIL.
"""
import asyncio
import json
import os
import sys
import time
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(REPO_ROOT, ".env"))

FAILURES: list[str] = []


def _report(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def _env(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name, default)
    if val is None:
        _report(f"env {name}", False, "missing")
    return val


def _wait_health(server_url: str, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{server_url}/health", timeout=5) as resp:
                data = json.loads(resp.read().decode())
                if data.get("mt5_initialized"):
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def _stream_check(ws_url: str, symbols: list[str], provider, wait_s: float = 60.0):
    from mt5connect.parsing import parse_quote_tick
    from mt5connect.remote_mt5 import tick_from_ws
    from mt5connect.ws_stream import WSStreamClient

    received: dict[str, dict] = {}
    parse_errors: list[str] = []

    def handler(payload: dict) -> None:
        if not ("symbol" in payload and ("bid" in payload or "ask" in payload)):
            return
        symbol = payload["symbol"]
        if symbol in received:
            return
        instrument = provider.get_instrument(symbol)
        if instrument is None:
            return
        try:
            parse_quote_tick(tick_from_ws(payload), instrument)
            received[symbol] = payload
        except Exception as exc:
            parse_errors.append(f"{symbol}: {exc}")

    async def _run() -> None:
        ws = WSStreamClient(ws_url, handler)
        await ws.start()
        try:
            await ws.send({"type": "subscribe", "symbols": symbols})
            deadline = time.time() + wait_s
            while len(received) < len(symbols) and time.time() < deadline:
                await asyncio.sleep(1)
        finally:
            await ws.stop()

    asyncio.run(_run())
    return received, parse_errors


def main() -> None:
    account = _env("MT5_ACCOUNT")
    password = _env("MT5_PASSWORD")
    server = _env("MT5_SERVER")
    raw_symbols = _env("MT5_SYMBOLS", "")
    server_url = _env("MT5_SERVER_URL", "http://localhost:5000")
    symbols = [s.strip() for s in raw_symbols.split(",") if s.strip()]

    if FAILURES:
        sys.exit(1)

    _report("health", _wait_health(server_url), server_url)

    from mt5connect.backend import set_backend
    from mt5connect.config import MT5Config

    config = MT5Config(
        account=int(account),
        password=password,
        server=server,
        symbols=symbols,
        backend="remote",
        server_url=server_url,
    )
    set_backend(config)

    from mt5connect.connection import MT5Connection
    from mt5connect.providers import MT5InstrumentProvider

    conn = MT5Connection(config)
    try:
        conn.connect()
        _report("rest login", conn.is_connected)
    except Exception as exc:
        _report("rest login", False, str(exc))

    provider = MT5InstrumentProvider(conn)
    for symbol in symbols:
        try:
            inst = provider.load_symbol(symbol)
            _report(f"instrument {symbol}", inst is not None)
        except Exception as exc:
            _report(f"instrument {symbol}", False, str(exc))

    import mt5connect.remote_mt5 as rmt5

    for symbol in symbols:
        try:
            tick = rmt5.symbol_info_tick(symbol)
            _report(f"rest tick {symbol}", tick is not None and tick.bid > 0)
        except Exception as exc:
            _report(f"rest tick {symbol}", False, str(exc))

    if conn.is_connected:
        received, parse_errors = _stream_check(config.ws_url, symbols, provider)
        for symbol in symbols:
            _report(
                f"ws tick {symbol}",
                symbol in received,
                "no tick yet - market may be closed; re-run after more ticks" if symbol not in received else "",
            )
        _report("ws parse", not parse_errors, "; ".join(parse_errors))
        conn.disconnect()

    if FAILURES:
        print("\nFAILURES:")
        for name in FAILURES:
            print(f"  - {name}")
        sys.exit(1)
    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    main()
