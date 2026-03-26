"""
nautilus_mt5/data.py

MT5DataClient — streams live market data from MT5 into NautilusTrader.

Since MT5 has no WebSocket or push API, this client runs an async polling
loop that calls mt5.symbol_info_tick() at a configurable interval (default
100ms) for each subscribed symbol and emits QuoteTick events onto the
NautilusTrader MessageBus.

Also implements _request_quote_ticks and _request_bars using the downloader
so the same client serves both historical data requests and live streaming.

Architecture
------------
  _connect()
    └─ loads instruments via provider
    └─ starts _poll_loop() as asyncio Task

  _poll_loop()  (runs every poll_interval_ms)
    └─ for each subscribed symbol:
         mt5.symbol_info_tick(symbol)
         → parse_quote_tick()
         → _handle_data(tick)   ← emits to MessageBus → strategy.on_quote_tick()

  _subscribe_quote_ticks()
    └─ adds symbol to self._subscribed set

  _unsubscribe_quote_ticks()
    └─ removes symbol from self._subscribed set

  _request_quote_ticks()
    └─ fetches historical ticks via mt5.copy_ticks_range()
    └─ _handle_quote_ticks(ticks, ...)

  _request_bars()
    └─ fetches historical bars via mt5.copy_rates_range()
    └─ _handle_bars(bars, ...)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import MetaTrader5 as mt5

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.data.messages import (
    RequestBars,
    RequestData,
    RequestQuoteTicks,
    SubscribeBars,
    SubscribeData,
    SubscribeQuoteTicks,
    UnsubscribeBars,
    UnsubscribeData,
    UnsubscribeQuoteTicks,
)
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.data import Bar, QuoteTick
from nautilus_trader.model.identifiers import ClientId, InstrumentId

from mt5connect.constants import MT5_VENUE
from mt5connect.errors import MT5ConnectionError
from mt5connect.parsing import parse_bar, parse_quote_tick

if TYPE_CHECKING:
    from mt5connect.config import MT5Config
    from mt5connect.connection import MT5Connection
    from mt5connect.providers import MT5InstrumentProvider

logger = logging.getLogger(__name__)


class MT5DataClient(LiveMarketDataClient):
    """
    Streams live market data from MT5 into NautilusTrader via polling.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
    connection : MT5Connection
        Active MT5 connection — must be connected before _connect() is called.
    msgbus : MessageBus
    cache : Cache
    clock : LiveClock
    instrument_provider : MT5InstrumentProvider
    config : MT5Config
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        connection: "MT5Connection",
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: "MT5InstrumentProvider",
        config: "MT5Config",
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=ClientId(MT5_VENUE.value),
            venue=MT5_VENUE,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
        )
        self._conn     = connection
        self._config   = config
        self._provider = instrument_provider

        # Symbols currently subscribed for live tick streaming
        self._subscribed_ticks: set[str]     = set()
        # Bar types currently subscribed for live bar streaming
        self._subscribed_bars: set[str]      = set()

        # The asyncio polling task — created in _connect, cancelled in _disconnect
        self._poll_task: asyncio.Task | None = None

        # Last known tick per symbol — used to suppress duplicate ticks
        # (MT5 returns the same tick if price hasn't moved)
        self._last_tick_time: dict[str, int] = {}

    # ── Required: connect / disconnect ───────────────────────────────────────

    async def _connect(self) -> None:
        """
        Called by NautilusTrader on node startup.

        Sequence:
          1. Verify MT5 connection is alive
          2. Load all instruments via provider
          3. Emit each instrument to the data engine
          4. Start the polling loop
        """
        self._conn.ensure_connected()

        # Load instruments for all configured symbols
        for symbol in self._config.symbols:
            try:
                instrument = self._provider.get_instrument(symbol)
                if instrument is None:
                    instrument = self._provider.load_symbol(symbol)
                self._handle_data(instrument)
                self._log.info(f"MT5DataClient: loaded instrument {symbol}")
            except Exception as exc:
                self._log.error(f"MT5DataClient: failed to load {symbol}: {exc}")

        # Start the async polling loop — use running loop to avoid cross-loop issues
        self._poll_task = asyncio.get_event_loop().create_task(
            self._poll_loop(),
            name="MT5DataClient._poll_loop",
        )
        self._log.info(
            f"MT5DataClient: connected — polling every {self._config.poll_interval_ms}ms "
            f"for {len(self._config.symbols)} symbols"
        )

    async def _disconnect(self) -> None:
        """Cancel the polling loop cleanly."""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        self._subscribed_ticks.clear()
        self._subscribed_bars.clear()
        self._last_tick_time.clear()
        self._log.info("MT5DataClient: disconnected")

    # ── Subscribe / unsubscribe ───────────────────────────────────────────────

    async def _subscribe_quote_ticks(self, command: SubscribeQuoteTicks) -> None:
        symbol = command.instrument_id.symbol.value
        self._subscribed_ticks.add(symbol)
        self._log.debug(f"MT5DataClient: subscribed ticks → {symbol}")

    async def _unsubscribe_quote_ticks(self, command: UnsubscribeQuoteTicks) -> None:
        symbol = command.instrument_id.symbol.value
        self._subscribed_ticks.discard(symbol)
        self._last_tick_time.pop(symbol, None)
        self._log.debug(f"MT5DataClient: unsubscribed ticks → {symbol}")

    async def _subscribe_bars(self, command: SubscribeBars) -> None:
        """
        MT5 does not push bar completions — we note the subscription but
        bars are delivered via _request_bars when strategy needs history.
        Live bar formation is handled by NautilusTrader's BarAggregator
        using the incoming QuoteTicks.
        """
        bar_type_str = str(command.bar_type)
        self._subscribed_bars.add(bar_type_str)
        self._log.debug(f"MT5DataClient: subscribed bars → {bar_type_str}")

    async def _unsubscribe_bars(self, command: UnsubscribeBars) -> None:
        bar_type_str = str(command.bar_type)
        self._subscribed_bars.discard(bar_type_str)
        self._log.debug(f"MT5DataClient: unsubscribed bars → {bar_type_str}")

    # ── No-op stubs for optional methods we don't support ────────────────────
    # MT5 doesn't provide order book, trade ticks, funding rates, etc.

    async def _subscribe(self, command: SubscribeData) -> None:
        pass

    async def _unsubscribe(self, command: UnsubscribeData) -> None:
        pass

    async def _subscribe_instruments(self, command) -> None:
        pass

    async def _subscribe_instrument(self, command) -> None:
        pass

    async def _subscribe_order_book_deltas(self, command) -> None:
        self._log.warning("MT5 does not support order book data")

    async def _subscribe_order_book_depth(self, command) -> None:
        self._log.warning("MT5 does not support order book data")

    async def _subscribe_trade_ticks(self, command) -> None:
        self._log.warning("MT5 does not provide individual trade ticks")

    async def _subscribe_mark_prices(self, command) -> None:
        pass

    async def _subscribe_index_prices(self, command) -> None:
        pass

    async def _subscribe_funding_rates(self, command) -> None:
        self._log.warning("MT5 does not provide funding rates")

    async def _subscribe_instrument_status(self, command) -> None:
        pass

    async def _subscribe_instrument_close(self, command) -> None:
        pass

    async def _unsubscribe_instruments(self, command) -> None:
        pass

    async def _unsubscribe_instrument(self, command) -> None:
        pass

    async def _unsubscribe_order_book_deltas(self, command) -> None:
        pass

    async def _unsubscribe_order_book_depth(self, command) -> None:
        pass

    async def _unsubscribe_trade_ticks(self, command) -> None:
        pass

    async def _unsubscribe_mark_prices(self, command) -> None:
        pass

    async def _unsubscribe_index_prices(self, command) -> None:
        pass

    async def _unsubscribe_funding_rates(self, command) -> None:
        pass

    async def _unsubscribe_instrument_status(self, command) -> None:
        pass

    async def _unsubscribe_instrument_close(self, command) -> None:
        pass

    # ── Historical data requests ──────────────────────────────────────────────

    async def _request(self, request: RequestData) -> None:
        pass

    async def _request_instrument(self, request) -> None:
        symbol = request.instrument_id.symbol.value
        try:
            instrument = self._provider.load_symbol(symbol)
            self._handle_instrument(instrument, request.id)
        except Exception as exc:
            self._log.error(f"MT5DataClient: _request_instrument failed: {exc}")

    async def _request_instruments(self, request) -> None:
        await self._provider.load_all_async()
        instruments = self._provider.list_all()
        self._handle_instruments(instruments, MT5_VENUE, request.id)

    async def _request_quote_ticks(self, request: RequestQuoteTicks) -> None:
        """
        Fetch historical ticks from MT5 and deliver to the data engine.
        Called when a strategy requests historical tick data.
        """
        symbol     = request.instrument_id.symbol.value
        start      = _nanos_to_datetime(request.start)
        end        = _nanos_to_datetime(request.end) if request.end else datetime.now(timezone.utc)

        instrument = self._provider.get_instrument(symbol)
        if instrument is None:
            self._log.error(f"MT5DataClient: instrument not found for {symbol}")
            return

        self._conn.ensure_connected()

        try:
            raw = mt5.copy_ticks_range(
                symbol,
                start,
                end,
                mt5.COPY_TICKS_ALL,
            )

            if raw is None or len(raw) == 0:
                self._log.warning(f"MT5DataClient: no ticks for {symbol} {start}→{end}")
                self._handle_quote_ticks([], instrument.id, request.id)
                return

            ticks = [parse_quote_tick(row, instrument) for row in raw]
            self._handle_quote_ticks(ticks, instrument.id, request.id)
            self._log.debug(f"MT5DataClient: delivered {len(ticks):,} ticks for {symbol}")

        except Exception as exc:
            self._log.error(f"MT5DataClient: _request_quote_ticks failed: {exc}")

    async def _request_bars(self, request: RequestBars) -> None:
        """
        Fetch historical bars from MT5 and deliver to the data engine.
        Called when a strategy requests historical bar data.
        """
        bar_type   = request.bar_type
        symbol     = bar_type.instrument_id.symbol.value
        timeframe  = _bar_spec_to_mt5_timeframe(bar_type)
        start      = _nanos_to_datetime(request.start)
        end        = _nanos_to_datetime(request.end) if request.end else datetime.now(timezone.utc)

        instrument = self._provider.get_instrument(symbol)
        if instrument is None:
            self._log.error(f"MT5DataClient: instrument not found for {symbol}")
            return

        self._conn.ensure_connected()

        try:
            raw = mt5.copy_rates_range(symbol, timeframe, start, end)

            if raw is None or len(raw) == 0:
                self._log.warning(f"MT5DataClient: no bars for {symbol} TF={timeframe}")
                self._handle_bars([], bar_type, request.id)
                return

            bars = [parse_bar(row, instrument, timeframe) for row in raw]
            self._handle_bars(bars, bar_type, request.id)
            self._log.debug(f"MT5DataClient: delivered {len(bars):,} bars for {symbol}")

        except Exception as exc:
            self._log.error(f"MT5DataClient: _request_bars failed: {exc}")

    async def _request_order_book_snapshot(self, request) -> None:
        self._log.warning("MT5 does not support order book snapshots")

    async def _request_trade_ticks(self, request) -> None:
        self._log.warning("MT5 does not provide individual trade ticks")

    async def _request_funding_rates(self, request) -> None:
        self._log.warning("MT5 does not provide funding rates")

    # ── Core polling loop ─────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """
        Main polling loop — runs for the lifetime of the data client.

        Every poll_interval_ms:
          1. For each subscribed symbol, call mt5.symbol_info_tick()
          2. If tick timestamp changed (new tick), emit QuoteTick
          3. Sleep until next poll

        On MT5 connection error:
          - Attempt reconnect via conn.reconnect_async()
          - If reconnect fails, stop the loop (node must be restarted)
        """
        self._log.info("MT5DataClient: poll loop started")

        while True:
            try:
                await self._poll_once()
                await asyncio.sleep(self._config.poll_interval_s)

            except asyncio.CancelledError:
                self._log.info("MT5DataClient: poll loop cancelled")
                break

            except MT5ConnectionError as exc:
                self._log.warning(f"MT5DataClient: connection lost — {exc}")
                ok = await self._conn.reconnect_async()
                if not ok:
                    self._log.error("MT5DataClient: reconnect failed — stopping poll loop")
                    break
                self._log.info("MT5DataClient: reconnected — resuming poll loop")

            except Exception as exc:
                self._log.error(f"MT5DataClient: unexpected poll error — {exc}")
                await asyncio.sleep(1.0)  # brief pause before retrying

        self._log.info("MT5DataClient: poll loop stopped")

    async def _poll_once(self) -> None:
        """
        Single poll iteration — fetch one tick per subscribed symbol.
        Called from _poll_loop every poll_interval_ms.
        """
        if not self._subscribed_ticks:
            return

        self._conn.ensure_connected()

        for symbol in list(self._subscribed_ticks):
            try:
                raw_tick = mt5.symbol_info_tick(symbol)
                if raw_tick is None:
                    continue

                # Suppress duplicate ticks (same timestamp = no new data)
                tick_time_ms = raw_tick.time_msc
                if self._last_tick_time.get(symbol) == tick_time_ms:
                    continue
                self._last_tick_time[symbol] = tick_time_ms

                instrument = self._provider.get_instrument(symbol)
                if instrument is None:
                    continue

                tick = parse_quote_tick(raw_tick, instrument)
                self._handle_data(tick)

            except Exception as exc:
                self._log.error(f"MT5DataClient: poll error for {symbol}: {exc}")

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def subscribed_quote_ticks(self) -> list[str]:
        """Currently subscribed symbols."""
        return sorted(self._subscribed_ticks)

    @property
    def is_polling(self) -> bool:
        """True if the poll loop task is running."""
        return self._poll_task is not None and not self._poll_task.done()


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _nanos_to_datetime(nanos: int | None) -> datetime | None:
    """Convert NautilusTrader nanosecond timestamp to UTC datetime."""
    if nanos is None:
        return None
    return datetime.fromtimestamp(nanos / 1_000_000_000, tz=timezone.utc)


def _bar_spec_to_mt5_timeframe(bar_type) -> int:
    """
    Convert a NautilusTrader BarType to an MT5 timeframe constant.

    Falls back to TIMEFRAME_H1 for unsupported specs.
    """
    from nautilus_trader.model.enums import BarAggregation

    spec = bar_type.spec
    agg  = spec.aggregation
    step = spec.step

    # Minute timeframes
    if agg == BarAggregation.MINUTE:
        tf_map = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6,
                  10: 10, 12: 12, 15: 15, 20: 20, 30: 30}
        return tf_map.get(step, mt5.TIMEFRAME_H1)

    # Hour timeframes
    if agg == BarAggregation.HOUR:
        tf_map = {1: mt5.TIMEFRAME_H1, 2: 16386, 3: 16387, 4: mt5.TIMEFRAME_H4,
                  6: 16390, 8: 16392, 12: 16396}
        return tf_map.get(step, mt5.TIMEFRAME_H1)

    # Daily / weekly / monthly
    if agg == BarAggregation.DAY:   return mt5.TIMEFRAME_D1
    if agg == BarAggregation.WEEK:  return 32769
    if agg == BarAggregation.MONTH: return 49153

    # Fallback
    return mt5.TIMEFRAME_H1
