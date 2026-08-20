import asyncio
from unittest.mock import MagicMock, patch

import pytest

from mt5connect.config import MT5Config
from mt5connect.data import MT5DataClient


def _remote_config():
    return MT5Config(account=1, password="p", server="s", symbols=["EURUSD"],
                     backend="remote", server_url="http://127.0.0.1:5000",
                     ws_url="ws://127.0.0.1:9000")


def _make_instrument(symbol="EURUSD"):
    from decimal import Decimal
    from nautilus_trader.model.currencies import Currency
    from nautilus_trader.model.identifiers import InstrumentId, Symbol
    from nautilus_trader.model.instruments import CurrencyPair
    from nautilus_trader.model.objects import Price, Quantity

    return CurrencyPair(
        instrument_id=InstrumentId.from_str(f"{symbol}.MT5"),
        raw_symbol=Symbol(symbol),
        base_currency=Currency.from_str("EUR"),
        quote_currency=Currency.from_str("USD"),
        price_precision=5,
        size_precision=2,
        price_increment=Price(0.00001, 5),
        size_increment=Quantity(0.01, 2),
        lot_size=Quantity(100000, 0),
        max_quantity=Quantity(1000.0, 2),
        min_quantity=Quantity(0.01, 2),
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0.03"),
        margin_maint=Decimal("0.03"),
        maker_fee=Decimal("0"),
        taker_fee=Decimal("0"),
        ts_event=0,
        ts_init=0,
    )


class FakeStream:
    """Awaitable stand-in for WSStreamClient (MagicMock is not awaitable)."""

    def __init__(self, **kwargs):
        self.start_calls = 0
        self.sent = []

    async def start(self):
        self.start_calls += 1

    async def stop(self):
        pass

    async def send(self, payload):
        self.sent.append(payload)


def _client():
    # Real Nautilus components are required (MagicMock fails PyCondition checks).
    from nautilus_trader.common.component import LiveClock
    from nautilus_trader.test_kit.stubs.component import TestComponentStubs
    from mt5connect.providers import MT5InstrumentProvider
    from nautilus_trader.common.providers import InstrumentProvider

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    conn = MagicMock()
    provider = MT5InstrumentProvider.__new__(MT5InstrumentProvider)
    InstrumentProvider.__init__(provider)
    provider._conn = conn
    provider._failed_symbols = []
    provider.get_instrument = MagicMock(return_value=_make_instrument())

    msgbus = TestComponentStubs.msgbus()
    cache = TestComponentStubs.cache()
    clock = LiveClock()

    client = MT5DataClient(loop, conn, msgbus, cache, clock, provider,
                           _remote_config())
    client._handle_data = MagicMock()
    return client


@pytest.mark.asyncio
async def test_connect_starts_ws_stream_not_poll_loop():
    client = _client()
    with patch("mt5connect.ws_stream.WSStreamClient",
               return_value=FakeStream()) as ws_cls:
        await client._connect()
        assert ws_cls.called
        assert client._ws is not None
        assert client._ws.start_calls == 1
        assert client._poll_task is None


@pytest.mark.asyncio
async def test_ws_on_message_handles_known_symbol():
    client = _client()
    client._subscribed_symbols.add("EURUSD")
    payload = {"symbol": "EURUSD", "time": "2024.06.01 10:00:00",
               "ask": "1.0801", "bid": "1.08", "volume": "0", "last": "0.0",
               "time_msec": "1704067200123", "flags": "2"}
    client._ws_on_message(payload)
    client._handle_data.assert_called_once()
    quote = client._handle_data.call_args[0][0]
    assert quote.bid_price.as_double() == 1.08
    assert quote.ts_event == 1704067200 * 1_000_000_000  # epoch from time_msec


@pytest.mark.asyncio
async def test_ws_on_message_ignores_unknown_symbol():
    client = _client()
    client._subscribed_symbols.add("UNKNOWN")
    client._provider.get_instrument.return_value = None
    payload = {"symbol": "UNKNOWN", "bid": "1.08", "ask": "1.09",
               "time_msec": "0"}
    client._ws_on_message(payload)
    client._handle_data.assert_not_called()


@pytest.mark.asyncio
async def test_ws_on_message_drops_unsubscribed_symbol():
    client = _client()
    payload = {"symbol": "EURUSD", "bid": "1.08", "ask": "1.09",
               "time_msec": "1704067200123"}
    client._ws_on_message(payload)
    client._handle_data.assert_not_called()


@pytest.mark.asyncio
async def test_subscribe_pushes_to_ws():
    client = _client()
    client._is_connected = True
    client._ws = FakeStream()
    command = MagicMock()
    command.instrument_id.symbol.value = "EURUSD"
    await client._subscribe_quote_ticks(command)
    assert client._ws.sent
    payload = client._ws.sent[0]
    assert payload["type"] == "subscribe" and "EURUSD" in payload["symbols"]
