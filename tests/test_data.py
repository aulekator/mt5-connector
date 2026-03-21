"""
tests/test_data.py

Exhaustive tests for MT5DataClient.

Tests are split into:
  1.  Helpers (_nanos_to_datetime, _bar_spec_to_mt5_timeframe)
  2.  Initial state
  3.  _connect() — instrument loading, poll task creation
  4.  _disconnect() — task cancellation, state cleanup
  5.  _subscribe_quote_ticks() / _unsubscribe_quote_ticks()
  6.  _subscribe_bars() / _unsubscribe_bars()
  7.  _poll_once() — tick emission, duplicate suppression
  8.  _poll_loop() — reconnect on connection error, stop on failure
  9.  _request_quote_ticks() — historical tick delivery
  10. _request_bars() — historical bar delivery
  11. No-op methods — don't raise
  12. Properties — subscribed_quote_ticks, is_polling
"""

import asyncio
import pytest
import numpy as np
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call

from nautilus_trader.model.identifiers import InstrumentId, ClientId
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.data import QuoteTick, Bar

from nautilus_mt5.data import MT5DataClient, _nanos_to_datetime, _bar_spec_to_mt5_timeframe
from nautilus_mt5.connection import ConnectionState
from nautilus_mt5.errors import MT5ConnectionError
from nautilus_mt5.constants import MT5_VENUE
# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

UTC = timezone.utc

def make_instrument(symbol="EURUSDm"):
    from nautilus_trader.model.currencies import Currency
    from nautilus_trader.model.identifiers import Symbol, Venue
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
def make_raw_tick(bid=1.085, ask=1.0852, time_s=1_700_000_000, time_msc=None):
    tick = MagicMock()
    tick.bid      = bid
    tick.ask      = ask
    tick.time     = time_s
    tick.time_msc = time_msc or (time_s * 1000)
    tick.last     = bid
    tick.volume   = 1
    return tick
def make_raw_rate(time_s=1_700_000_000, open_=1.085, high=1.090,
                  low=1.080, close=1.088, tick_volume=1000):
    dtype = np.dtype([
        ("time",        np.int64),
        ("open",        np.float64),
        ("high",        np.float64),
        ("low",         np.float64),
        ("close",       np.float64),
        ("tick_volume", np.int64),
        ("spread",      np.int32),
        ("real_volume", np.int64),
    ])
    arr = np.array([(time_s, open_, high, low, close, tick_volume, 2, 0)], dtype=dtype)
    return arr[0]
def make_config(symbols=None):
    from nautilus_mt5.config import MT5Config
    return MT5Config(
        account=12345678,
        password="test",
        server="Exness-MT5Trial1",
        symbols=symbols or ["EURUSDm"],
        poll_interval_ms=50,
        reconnect_initial_delay_s=0.01,
        reconnect_max_delay_s=0.05,
        reconnect_max_attempts=2,
    )
def make_conn(connected=True):
    conn = MagicMock()
    conn.is_connected = connected
    conn.state = ConnectionState.CONNECTED if connected else ConnectionState.DISCONNECTED
    conn.ensure_connected = MagicMock() if connected else MagicMock(
        side_effect=MT5ConnectionError("not connected")
    )
    conn.reconnect_async = AsyncMock(return_value=connected)
    return conn
def make_provider(instrument=None):
    """
    Build a real MT5InstrumentProvider subclass — NautilusTrader's
    PyCondition.type() rejects MagicMock, so we must use a real subclass.
    We override the methods to return test data without touching MT5.
    """
    from nautilus_mt5.providers import MT5InstrumentProvider
    from nautilus_mt5.connection import MT5Connection

    # Minimal connection mock that satisfies MT5Connection's interface
    conn = MagicMock(spec=MT5Connection)
    conn.ensure_connected = MagicMock()

    inst = instrument or make_instrument("EURUSDm")

    provider = MT5InstrumentProvider.__new__(MT5InstrumentProvider)
    # Manually initialise the InstrumentProvider base
    from nautilus_trader.common.providers import InstrumentProvider
    InstrumentProvider.__init__(provider)
    # Set our test attributes
    provider._conn = conn
    provider._failed_symbols = []

    # Override methods to return test data
    provider.get_instrument  = MagicMock(return_value=inst)
    provider.load_symbol     = MagicMock(return_value=inst)
    provider.list_all        = MagicMock(return_value=[inst])
    provider.load_all_async  = AsyncMock()

    return provider
def make_client(symbols=None, connected=True, instrument=None):
    """Build a fully wired MT5DataClient with real NautilusTrader components."""
    from nautilus_trader.test_kit.stubs.component import TestComponentStubs
    from nautilus_trader.common.component import LiveClock

    # Use the running event loop (pytest-asyncio's loop) to avoid cross-loop errors
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    config   = make_config(symbols)
    conn     = make_conn(connected)
    provider = make_provider(instrument)

    # NautilusTrader requires real component types — MagicMock fails PyCondition checks
    msgbus = TestComponentStubs.msgbus()
    cache  = TestComponentStubs.cache()
    clock  = LiveClock()

    client = MT5DataClient(
        loop=loop,
        connection=conn,
        msgbus=msgbus,
        cache=cache,
        clock=clock,
        instrument_provider=provider,
        config=config,
    )
    # Patch internal handler methods so we can track emitted data
    client._handle_data        = MagicMock()
    client._handle_quote_ticks = MagicMock()
    client._handle_bars        = MagicMock()
    client._handle_instrument  = MagicMock()
    client._handle_instruments = MagicMock()

    return client, conn, provider, loop
@pytest.fixture
def client():
    c, conn, prov, loop = make_client()
    yield c
    loop.close()

@pytest.fixture
def client_with_loop():
    c, conn, prov, loop = make_client()
    yield c, conn, prov, loop
    loop.close()
# ═════════════════════════════════════════════════════════════════════════════
# 1. Helpers
# ═════════════════════════════════════════════════════════════════════════════

class TestNanosToDatetime:

    def test_converts_nanos_to_utc_datetime(self):
        nanos = 1_700_000_000_000_000_000
        result = _nanos_to_datetime(nanos)
        assert isinstance(result, datetime)
        assert result.tzinfo == UTC

    def test_none_returns_none(self):
        assert _nanos_to_datetime(None) is None

    def test_epoch_zero(self):
        result = _nanos_to_datetime(0)
        assert result.year == 1970

    def test_value_correct(self):
        nanos = 1_700_000_000 * 1_000_000_000
        result = _nanos_to_datetime(nanos)
        assert result.timestamp() == pytest.approx(1_700_000_000.0)
class TestBarSpecToMt5Timeframe:

    def _make_bar_type(self, step, aggregation_name):
        from nautilus_trader.model.enums import BarAggregation, PriceType
        from nautilus_trader.model.data import BarType, BarSpecification
        bar_spec = MagicMock()
        bar_spec.step = step
        bar_spec.aggregation = getattr(BarAggregation, aggregation_name)
        bar_type = MagicMock()
        bar_type.spec = bar_spec
        return bar_type

    def test_m1_returns_1(self):
        bt = self._make_bar_type(1, "MINUTE")
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            result = _bar_spec_to_mt5_timeframe(bt)
        assert result == 1

    def test_m5_returns_5(self):
        bt = self._make_bar_type(5, "MINUTE")
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            result = _bar_spec_to_mt5_timeframe(bt)
        assert result == 5

    def test_m15_returns_15(self):
        bt = self._make_bar_type(15, "MINUTE")
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            result = _bar_spec_to_mt5_timeframe(bt)
        assert result == 15

    def test_h1_returns_16385(self):
        bt = self._make_bar_type(1, "HOUR")
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.TIMEFRAME_H4 = 16388
            result = _bar_spec_to_mt5_timeframe(bt)
        assert result == 16385

    def test_h4_returns_16388(self):
        bt = self._make_bar_type(4, "HOUR")
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.TIMEFRAME_H4 = 16388
            result = _bar_spec_to_mt5_timeframe(bt)
        assert result == 16388

    def test_d1_returns_correct(self):
        bt = self._make_bar_type(1, "DAY")
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_D1 = 16408
            mock_mt5.TIMEFRAME_H1 = 16385
            result = _bar_spec_to_mt5_timeframe(bt)
        assert result == 16408

    def test_unknown_falls_back_to_h1(self):
        bt = self._make_bar_type(999, "MINUTE")
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            result = _bar_spec_to_mt5_timeframe(bt)
        assert result == 16385
# ═════════════════════════════════════════════════════════════════════════════
# 2. Initial state
# ═════════════════════════════════════════════════════════════════════════════

class TestInitialState:

    def test_client_id_is_mt5(self, client):
        assert client.id == ClientId("MT5")

    def test_no_subscribed_ticks_initially(self, client):
        assert client.subscribed_quote_ticks == []

    def test_not_polling_initially(self, client):
        assert client.is_polling is False

    def test_poll_task_none_initially(self, client):
        assert client._poll_task is None

    def test_last_tick_time_empty(self, client):
        assert client._last_tick_time == {}
# ═════════════════════════════════════════════════════════════════════════════
# 3. _connect()
# ═════════════════════════════════════════════════════════════════════════════

class TestConnect:

    @pytest.mark.asyncio
    async def test_connect_checks_connection(self):
        c, conn, prov, loop = make_client()
        try:
            await c._connect()
            conn.ensure_connected.assert_called()
        finally:
            await c._disconnect()
    

    @pytest.mark.asyncio
    async def test_connect_loads_instruments(self):
        c, conn, prov, loop = make_client()
        try:
            await c._connect()
            prov.get_instrument.assert_called()
        finally:
            await c._disconnect()
    

    @pytest.mark.asyncio
    async def test_connect_emits_instruments(self):
        c, conn, prov, loop = make_client()
        try:
            await c._connect()
            c._handle_data.assert_called()
        finally:
            await c._disconnect()
    

    @pytest.mark.asyncio
    async def test_connect_starts_poll_task(self):
        c, conn, prov, loop = make_client()
        try:
            await c._connect()
            assert c._poll_task is not None
        finally:
            await c._disconnect()
    

    @pytest.mark.asyncio
    async def test_connect_poll_task_is_running(self):
        c, conn, prov, loop = make_client()
        try:
            await c._connect()
            assert not c._poll_task.done()
        finally:
            await c._disconnect()
    
# ═════════════════════════════════════════════════════════════════════════════
# 4. _disconnect()
# ═════════════════════════════════════════════════════════════════════════════

class TestDisconnect:

    @pytest.mark.asyncio
    async def test_disconnect_cancels_poll_task(self):
        c, conn, prov, loop = make_client()
        await c._connect()
        task = c._poll_task
        await c._disconnect()
        assert task.cancelled() or task.done()
    @pytest.mark.asyncio
    async def test_disconnect_clears_subscriptions(self):
        c, conn, prov, loop = make_client()
        await c._connect()
        c._subscribed_ticks.add("EURUSDm")
        await c._disconnect()
        assert c._subscribed_ticks == set()
    @pytest.mark.asyncio
    async def test_disconnect_clears_last_tick_time(self):
        c, conn, prov, loop = make_client()
        await c._connect()
        c._last_tick_time["EURUSDm"] = 123456
        await c._disconnect()
        assert c._last_tick_time == {}
    @pytest.mark.asyncio
    async def test_disconnect_sets_poll_task_none(self):
        c, conn, prov, loop = make_client()
        await c._connect()
        await c._disconnect()
        assert c._poll_task is None
    @pytest.mark.asyncio
    async def test_not_polling_after_disconnect(self):
        c, conn, prov, loop = make_client()
        await c._connect()
        await c._disconnect()
        assert c.is_polling is False

# ═════════════════════════════════════════════════════════════════════════════
# 5. Subscribe / unsubscribe quote ticks
# ═════════════════════════════════════════════════════════════════════════════

class TestSubscribeQuoteTicks:

    @pytest.mark.asyncio
    async def test_subscribe_adds_symbol(self, client):
        cmd = MagicMock()
        cmd.instrument_id.symbol.value = "EURUSDm"
        await client._subscribe_quote_ticks(cmd)
        assert "EURUSDm" in client._subscribed_ticks

    @pytest.mark.asyncio
    async def test_subscribe_multiple_symbols(self, client):
        for sym in ["EURUSDm", "XAUUSDm", "BTCUSDm"]:
            cmd = MagicMock()
            cmd.instrument_id.symbol.value = sym
            await client._subscribe_quote_ticks(cmd)
        assert client._subscribed_ticks == {"EURUSDm", "XAUUSDm", "BTCUSDm"}

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_symbol(self, client):
        client._subscribed_ticks.add("EURUSDm")
        cmd = MagicMock()
        cmd.instrument_id.symbol.value = "EURUSDm"
        await client._unsubscribe_quote_ticks(cmd)
        assert "EURUSDm" not in client._subscribed_ticks

    @pytest.mark.asyncio
    async def test_unsubscribe_clears_last_tick_time(self, client):
        client._subscribed_ticks.add("EURUSDm")
        client._last_tick_time["EURUSDm"] = 123456789
        cmd = MagicMock()
        cmd.instrument_id.symbol.value = "EURUSDm"
        await client._unsubscribe_quote_ticks(cmd)
        assert "EURUSDm" not in client._last_tick_time

    @pytest.mark.asyncio
    async def test_unsubscribe_non_subscribed_does_not_raise(self, client):
        cmd = MagicMock()
        cmd.instrument_id.symbol.value = "FAKESYM"
        await client._unsubscribe_quote_ticks(cmd)  # must not raise

    @pytest.mark.asyncio
    async def test_subscribed_quote_ticks_sorted(self, client):
        for sym in ["ZZZUSDm", "AAAUSDm", "MMMusd"]:
            cmd = MagicMock()
            cmd.instrument_id.symbol.value = sym
            await client._subscribe_quote_ticks(cmd)
        result = client.subscribed_quote_ticks
        assert result == sorted(result)
# ═════════════════════════════════════════════════════════════════════════════
# 6. Subscribe / unsubscribe bars
# ═════════════════════════════════════════════════════════════════════════════

class TestSubscribeBars:

    @pytest.mark.asyncio
    async def test_subscribe_bars_adds_to_set(self, client):
        cmd = MagicMock()
        cmd.bar_type.__str__ = lambda self: "EURUSDm.MT5-1-HOUR-MID-EXTERNAL"
        await client._subscribe_bars(cmd)
        assert "EURUSDm.MT5-1-HOUR-MID-EXTERNAL" in client._subscribed_bars

    @pytest.mark.asyncio
    async def test_unsubscribe_bars_removes_from_set(self, client):
        client._subscribed_bars.add("EURUSDm.MT5-1-HOUR-MID-EXTERNAL")
        cmd = MagicMock()
        cmd.bar_type.__str__ = lambda self: "EURUSDm.MT5-1-HOUR-MID-EXTERNAL"
        await client._unsubscribe_bars(cmd)
        assert "EURUSDm.MT5-1-HOUR-MID-EXTERNAL" not in client._subscribed_bars
# ═════════════════════════════════════════════════════════════════════════════
# 7. _poll_once()
# ═════════════════════════════════════════════════════════════════════════════

class TestPollOnce:

    @pytest.mark.asyncio
    async def test_does_nothing_when_no_subscriptions(self, client):
        client._subscribed_ticks.clear()
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            await client._poll_once()
        mock_mt5.symbol_info_tick.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_symbol_info_tick_for_subscribed(self, client):
        client._subscribed_ticks.add("EURUSDm")
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.symbol_info_tick.return_value = make_raw_tick()
            await client._poll_once()
        mock_mt5.symbol_info_tick.assert_called_once_with("EURUSDm")

    @pytest.mark.asyncio
    async def test_emits_quote_tick_on_new_data(self, client):
        client._subscribed_ticks.add("EURUSDm")
        raw = make_raw_tick(time_msc=1_700_000_000_000)
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.symbol_info_tick.return_value = raw
            await client._poll_once()
        client._handle_data.assert_called_once()
        args = client._handle_data.call_args[0]
        assert isinstance(args[0], QuoteTick)

    @pytest.mark.asyncio
    async def test_suppresses_duplicate_tick(self, client):
        client._subscribed_ticks.add("EURUSDm")
        raw = make_raw_tick(time_msc=1_700_000_000_000)
        # Pre-set last known time to same value
        client._last_tick_time["EURUSDm"] = 1_700_000_000_000
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.symbol_info_tick.return_value = raw
            await client._poll_once()
        # Should NOT emit because time_msc unchanged
        client._handle_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_emits_after_tick_time_changes(self, client):
        client._subscribed_ticks.add("EURUSDm")
        # First tick
        client._last_tick_time["EURUSDm"] = 1_700_000_000_000
        raw = make_raw_tick(time_msc=1_700_000_001_000)  # new time
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.symbol_info_tick.return_value = raw
            await client._poll_once()
        client._handle_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_last_tick_time(self, client):
        client._subscribed_ticks.add("EURUSDm")
        raw = make_raw_tick(time_msc=9_999_999_000)
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.symbol_info_tick.return_value = raw
            await client._poll_once()
        assert client._last_tick_time["EURUSDm"] == 9_999_999_000

    @pytest.mark.asyncio
    async def test_skips_none_tick(self, client):
        client._subscribed_ticks.add("EURUSDm")
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.symbol_info_tick.return_value = None
            await client._poll_once()
        client._handle_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_symbol_with_no_instrument(self, client):
        client._subscribed_ticks.add("UNKNOWN")
        client._provider.get_instrument.return_value = None
        raw = make_raw_tick(time_msc=1_700_000_000_000)
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.symbol_info_tick.return_value = raw
            await client._poll_once()
        client._handle_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_polls_multiple_symbols(self, client):
        for sym in ["EURUSDm", "XAUUSDm"]:
            client._subscribed_ticks.add(sym)
        raw = make_raw_tick(time_msc=1_700_000_000_000)
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.symbol_info_tick.return_value = raw
            await client._poll_once()
        assert mock_mt5.symbol_info_tick.call_count == 2

    @pytest.mark.asyncio
    async def test_error_in_one_symbol_does_not_stop_others(self, client):
        client._subscribed_ticks = {"EURUSDm", "XAUUSDm"}
        call_count = {"n": 0}
        def side_effect(sym):
            call_count["n"] += 1
            if sym == "EURUSDm":
                raise RuntimeError("IPC error")
            return make_raw_tick(time_msc=1_700_000_000_000)
        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.symbol_info_tick.side_effect = side_effect
            await client._poll_once()
        # Both symbols were attempted
        assert call_count["n"] == 2
# ═════════════════════════════════════════════════════════════════════════════
# 8. _poll_loop() reconnect behaviour
# ═════════════════════════════════════════════════════════════════════════════

class TestPollLoop:

    @pytest.mark.asyncio
    async def test_poll_loop_reconnects_on_connection_error(self):
        c, conn, prov, loop = make_client()

        call_count = {"n": 0}
        async def flaky_poll():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise MT5ConnectionError("lost")
            await asyncio.sleep(10)  # block until cancelled

        c._poll_once = flaky_poll
        conn.reconnect_async = AsyncMock(return_value=True)

        task = loop.create_task(c._poll_loop())
        await asyncio.sleep(0.1)  # let it run
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        conn.reconnect_async.assert_called()
    @pytest.mark.asyncio
    async def test_poll_loop_stops_when_reconnect_fails(self):
        c, conn, prov, loop = make_client()

        async def always_fails():
            raise MT5ConnectionError("lost")

        c._poll_once = always_fails
        conn.reconnect_async = AsyncMock(return_value=False)

        # loop should exit on its own when reconnect fails
        await asyncio.wait_for(c._poll_loop(), timeout=2.0)
        conn.reconnect_async.assert_called()

# ═════════════════════════════════════════════════════════════════════════════
# 9. _request_quote_ticks()
# ═════════════════════════════════════════════════════════════════════════════

class TestRequestQuoteTicks:

    @pytest.mark.asyncio
    async def test_fetches_and_delivers_ticks(self, client):
        request = MagicMock()
        request.instrument_id.symbol.value = "EURUSDm"
        request.start = 1_700_000_000_000_000_000
        request.end   = 1_700_100_000_000_000_000
        request.id    = "req-001"

        raw_ticks = [make_raw_tick(time_s=1_700_000_000 + i, time_msc=(1_700_000_000+i)*1000)
                     for i in range(10)]

        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = raw_ticks
            await client._request_quote_ticks(request)

        client._handle_quote_ticks.assert_called_once()
        args = client._handle_quote_ticks.call_args[0]
        assert len(args[0]) == 10

    @pytest.mark.asyncio
    async def test_delivers_empty_list_when_no_data(self, client):
        request = MagicMock()
        request.instrument_id.symbol.value = "EURUSDm"
        request.start = 1_700_000_000_000_000_000
        request.end   = 1_700_100_000_000_000_000
        request.id    = "req-002"

        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = []
            await client._request_quote_ticks(request)

        client._handle_quote_ticks.assert_called_once()
        args = client._handle_quote_ticks.call_args[0]
        assert args[0] == []

    @pytest.mark.asyncio
    async def test_handles_none_response_gracefully(self, client):
        request = MagicMock()
        request.instrument_id.symbol.value = "EURUSDm"
        request.start = 1_700_000_000_000_000_000
        request.end   = None
        request.id    = "req-003"

        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = None
            await client._request_quote_ticks(request)

        # Should still call handle (with empty list) and not raise
        client._handle_quote_ticks.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_when_instrument_not_found(self, client):
        client._provider.get_instrument.return_value = None
        request = MagicMock()
        request.instrument_id.symbol.value = "FAKESYM"
        request.start = 1_700_000_000_000_000_000
        request.end   = 1_700_100_000_000_000_000
        request.id    = "req-004"

        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            await client._request_quote_ticks(request)

        mock_mt5.copy_ticks_range.assert_not_called()
# ═════════════════════════════════════════════════════════════════════════════
# 10. _request_bars()
# ═════════════════════════════════════════════════════════════════════════════

class TestRequestBars:

    @pytest.mark.asyncio
    async def test_fetches_and_delivers_bars(self, client):
        request = MagicMock()
        request.bar_type.instrument_id.symbol.value = "EURUSDm"
        request.start = 1_700_000_000_000_000_000
        request.end   = 1_700_100_000_000_000_000
        request.id    = "req-bars-001"

        raw_bars = [make_raw_rate(time_s=1_700_000_000 + i*3600) for i in range(5)]

        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1   = 16385
            mock_mt5.TIMEFRAME_H4   = 16388
            mock_mt5.TIMEFRAME_D1   = 16408
            mock_mt5.copy_rates_range.return_value = raw_bars
            # Mock _bar_spec_to_mt5_timeframe return
            with patch("nautilus_mt5.data._bar_spec_to_mt5_timeframe", return_value=16385):
                await client._request_bars(request)

        client._handle_bars.assert_called_once()
        args = client._handle_bars.call_args[0]
        assert len(args[0]) == 5

    @pytest.mark.asyncio
    async def test_delivers_empty_list_when_no_bars(self, client):
        request = MagicMock()
        request.bar_type.instrument_id.symbol.value = "EURUSDm"
        request.start = 1_700_000_000_000_000_000
        request.end   = 1_700_100_000_000_000_000
        request.id    = "req-bars-002"

        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            mock_mt5.copy_rates_range.return_value = []
            with patch("nautilus_mt5.data._bar_spec_to_mt5_timeframe", return_value=16385):
                await client._request_bars(request)

        client._handle_bars.assert_called_once()
        args = client._handle_bars.call_args[0]
        assert args[0] == []

    @pytest.mark.asyncio
    async def test_skips_when_instrument_not_found(self, client):
        client._provider.get_instrument.return_value = None
        request = MagicMock()
        request.bar_type.instrument_id.symbol.value = "FAKESYM"
        request.start = 1_700_000_000_000_000_000
        request.end   = None
        request.id    = "req-bars-003"

        with patch("nautilus_mt5.data.mt5") as mock_mt5:
            with patch("nautilus_mt5.data._bar_spec_to_mt5_timeframe", return_value=16385):
                await client._request_bars(request)

        mock_mt5.copy_rates_range.assert_not_called()
# ═════════════════════════════════════════════════════════════════════════════
# 11. No-op methods — don't raise
# ═════════════════════════════════════════════════════════════════════════════

class TestNoOpMethods:

    @pytest.mark.asyncio
    async def test_subscribe_does_not_raise(self, client):
        await client._subscribe(MagicMock())

    @pytest.mark.asyncio
    async def test_unsubscribe_does_not_raise(self, client):
        await client._unsubscribe(MagicMock())

    @pytest.mark.asyncio
    async def test_subscribe_instruments_does_not_raise(self, client):
        await client._subscribe_instruments(MagicMock())

    @pytest.mark.asyncio
    async def test_subscribe_instrument_does_not_raise(self, client):
        await client._subscribe_instrument(MagicMock())

    @pytest.mark.asyncio
    async def test_subscribe_trade_ticks_does_not_raise(self, client):
        await client._subscribe_trade_ticks(MagicMock())

    @pytest.mark.asyncio
    async def test_subscribe_funding_rates_does_not_raise(self, client):
        await client._subscribe_funding_rates(MagicMock())

    @pytest.mark.asyncio
    async def test_request_trade_ticks_does_not_raise(self, client):
        await client._request_trade_ticks(MagicMock())

    @pytest.mark.asyncio
    async def test_request_funding_rates_does_not_raise(self, client):
        await client._request_funding_rates(MagicMock())

    @pytest.mark.asyncio
    async def test_request_order_book_snapshot_does_not_raise(self, client):
        await client._request_order_book_snapshot(MagicMock())
# ═════════════════════════════════════════════════════════════════════════════
# 12. Properties
# ═════════════════════════════════════════════════════════════════════════════

class TestProperties:

    def test_subscribed_quote_ticks_empty_initially(self, client):
        assert client.subscribed_quote_ticks == []

    @pytest.mark.asyncio
    async def test_subscribed_quote_ticks_after_subscribe(self, client):
        cmd = MagicMock()
        cmd.instrument_id.symbol.value = "EURUSDm"
        await client._subscribe_quote_ticks(cmd)
        assert "EURUSDm" in client.subscribed_quote_ticks

    def test_is_polling_false_initially(self, client):
        assert client.is_polling is False

    @pytest.mark.asyncio
    async def test_is_polling_true_after_connect(self):
        c, conn, prov, loop = make_client()
        try:
            await c._connect()
            assert c.is_polling is True
        finally:
            await c._disconnect()
    

    @pytest.mark.asyncio
    async def test_is_polling_false_after_disconnect(self):
        c, conn, prov, loop = make_client()
        await c._connect()
        await c._disconnect()
        assert c.is_polling is False