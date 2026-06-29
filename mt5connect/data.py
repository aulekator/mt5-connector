"""
nautilus_mt5/data.py

MT5DataClient — streams live market data from MT5 into NautilusTrader.
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
        self._conn = connection
        self._config = config
        self._provider = instrument_provider

        self._subscribed_symbols: set[str] = set()
        self._subscribed_bar_types: set[str] = set()
        self._poll_task: asyncio.Task | None = None
        self._last_tick_time: dict[str, int] = {}
        self._is_connected = False
        self._pending_subscriptions: set[str] = set()

    async def _connect(self) -> None:
        """Called by NautilusTrader on node startup."""
        self._conn.ensure_connected()
        self._is_connected = True

        for symbol in self._config.symbols:
            instrument = self._provider.get_instrument(symbol)
            if instrument is None:
                instrument = self._provider.load_symbol(symbol)
            self._handle_data(instrument)
            self._log.info(f"MT5DataClient: loaded instrument {symbol}")

            if symbol not in self._subscribed_symbols:
                self._subscribed_symbols.add(symbol)

        await asyncio.sleep(0.5)

        for symbol in list(self._pending_subscriptions):
            if symbol not in self._subscribed_symbols:
                self._subscribed_symbols.add(symbol)
            self._pending_subscriptions.discard(symbol)

        self._poll_task = asyncio.get_event_loop().create_task(
            self._poll_loop(),
            name="MT5DataClient._poll_loop",
        )
        self._log.info(
            f"MT5DataClient: connected — polling every {self._config.poll_interval_ms}ms "
            f"for {len(self._config.symbols)} symbols"
        )

    async def _disconnect(self) -> None:
        self._is_connected = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            await self._poll_task
            self._poll_task = None

        self._subscribed_symbols.clear()
        self._subscribed_bar_types.clear()
        self._last_tick_time.clear()
        self._pending_subscriptions.clear()
        self._log.info("MT5DataClient: disconnected")

    async def _subscribe_quote_ticks(self, command: SubscribeQuoteTicks) -> None:
        symbol = command.instrument_id.symbol.value

        if not self._is_connected:
            self._pending_subscriptions.add(symbol)
            return

        self._subscribed_symbols.add(symbol)
        self._log.debug(f"MT5DataClient: subscribed ticks → {symbol}")

    async def _unsubscribe_quote_ticks(self, command: UnsubscribeQuoteTicks) -> None:
        symbol = command.instrument_id.symbol.value
        self._subscribed_symbols.discard(symbol)
        self._last_tick_time.pop(symbol, None)
        self._pending_subscriptions.discard(symbol)
        self._log.debug(f"MT5DataClient: unsubscribed ticks → {symbol}")

    async def _subscribe_bars(self, command: SubscribeBars) -> None:
        bar_type_str = str(command.bar_type)
        self._subscribed_bar_types.add(bar_type_str)

        symbol = command.bar_type.instrument_id.symbol.value

        if not self._is_connected:
            self._pending_subscriptions.add(symbol)
            return

        if symbol not in self._subscribed_symbols:
            self._subscribed_symbols.add(symbol)
            self._log.debug(
                f"MT5DataClient: auto-subscribed ticks for bar aggregation → {symbol}"
            )

        self._log.debug(f"MT5DataClient: subscribed bars → {bar_type_str}")

    async def _unsubscribe_bars(self, command: UnsubscribeBars) -> None:
        bar_type_str = str(command.bar_type)
        self._subscribed_bar_types.discard(bar_type_str)

        symbol = command.bar_type.instrument_id.symbol.value
        instrument_prefix = f"{command.bar_type.instrument_id.value}-"
        still_needed = any(
            bt.startswith(instrument_prefix) for bt in self._subscribed_bar_types
        )
        if not still_needed:
            self._subscribed_symbols.discard(symbol)
            self._last_tick_time.pop(symbol, None)
            self._pending_subscriptions.discard(symbol)
            self._log.debug(
                f"MT5DataClient: auto-unsubscribed ticks (no bars left) → {symbol}"
            )

        self._log.debug(f"MT5DataClient: unsubscribed bars → {bar_type_str}")

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

    async def _request(self, request: RequestData) -> None:
        pass

    async def _request_instrument(self, request) -> None:
        symbol = request.instrument_id.symbol.value
        instrument = self._provider.load_symbol(symbol)
        self._handle_instrument(instrument, request.id)

    async def _request_instruments(self, request) -> None:
        await self._provider.load_all_async()
        instruments = self._provider.list_all()
        self._handle_instruments(instruments, MT5_VENUE, request.id)

    async def _request_quote_ticks(self, request: RequestQuoteTicks) -> None:
        symbol = request.instrument_id.symbol.value
        start = _nanos_to_datetime(request.start)
        end = _nanos_to_datetime(request.end) if request.end else datetime.now(timezone.utc)

        instrument = self._provider.get_instrument(symbol)
        if instrument is None:
            self._log.error(f"MT5DataClient: instrument not found for {symbol}")
            return

        self._conn.ensure_connected()

        raw = mt5.copy_ticks_range(
            symbol,
            start,
            end,
            mt5.COPY_TICKS_ALL,
        )

        if raw is None or len(raw) == 0:
            self._log.warning(f"MT5DataClient: no ticks for {symbol} {start}→{end}")
            # _handle_quote_ticks signature: (instrument_id, ticks, correlation_id, start, end, params)
            self._handle_quote_ticks(instrument.id, [], request.id, request.start, request.end, request.params)
            return

        ticks = [parse_quote_tick(row, instrument) for row in raw]
        # _handle_quote_ticks signature: (instrument_id, ticks, correlation_id, start, end, params)
        self._handle_quote_ticks(instrument.id, ticks, request.id, request.start, request.end, request.params)
        self._log.debug(f"MT5DataClient: delivered {len(ticks):,} ticks for {symbol}")

    # ================================================================
    # FIXED: _request_bars with correct _handle_bars signature (6 args)
    # ================================================================
    async def _request_bars(self, request: RequestBars) -> None:
        bar_type = request.bar_type
        symbol = bar_type.instrument_id.symbol.value
        timeframe = _bar_spec_to_mt5_timeframe(bar_type)

        # Handle start time
        if request.start is not None:
            if hasattr(request.start, 'timestamp'):
                start = datetime.fromtimestamp(request.start.timestamp(), tz=timezone.utc)
            else:
                start = _nanos_to_datetime(request.start)
        else:
            start = None

        # Handle end time
        if request.end is not None:
            if hasattr(request.end, 'timestamp'):
                end = datetime.fromtimestamp(request.end.timestamp(), tz=timezone.utc)
            else:
                end = _nanos_to_datetime(request.end)
        else:
            end = datetime.now(timezone.utc)

        instrument = self._provider.get_instrument(symbol)
        if instrument is None:
            self._log.error(f"MT5DataClient: instrument not found for {symbol}")
            return

        self._conn.ensure_connected()

        raw = mt5.copy_rates_range(symbol, timeframe, start, end)

        if raw is None or len(raw) == 0:
            self._log.warning(f"MT5DataClient: no bars for {symbol} TF={timeframe}")
            # _handle_bars signature: (bar_type, bars, correlation_id, start, end, params)
            self._handle_bars(bar_type, [], request.id, request.start, request.end, request.params)
            return

        bars = [parse_bar(row, instrument, timeframe) for row in raw]
        # _handle_bars signature: (bar_type, bars, correlation_id, start, end, params)
        self._handle_bars(bar_type, bars, request.id, request.start, request.end, request.params)
        self._log.debug(f"MT5DataClient: delivered {len(bars):,} bars for {symbol}")

    async def _request_order_book_snapshot(self, request) -> None:
        self._log.warning("MT5 does not support order book snapshots")

    async def _request_trade_ticks(self, request) -> None:
        self._log.warning("MT5 does not provide individual trade ticks")

    async def _request_funding_rates(self, request) -> None:
        self._log.warning("MT5 does not provide funding rates")

    async def _poll_loop(self) -> None:
        self._log.info("MT5DataClient: poll loop started")

        while True:
            await self._poll_once()
            await asyncio.sleep(self._config.poll_interval_s)

    async def _poll_once(self) -> None:
        """Single poll iteration — fetch one tick per subscribed symbol."""

        if not self._subscribed_symbols:
            return

        self._conn.ensure_connected()

        for symbol in list(self._subscribed_symbols):

            if not mt5.symbol_select(symbol, True):
                await asyncio.sleep(0.1)
                if not mt5.symbol_select(symbol, True):
                    continue

            raw_tick = None

            for attempt in range(3):
                raw_tick = mt5.symbol_info_tick(symbol)
                if raw_tick is not None:
                    break
                await asyncio.sleep(0.05)

            if raw_tick is None:
                continue

            tick_time_ms = raw_tick.time_msc
            if self._last_tick_time.get(symbol) == tick_time_ms:
                continue
            self._last_tick_time[symbol] = tick_time_ms

            instrument = self._provider.get_instrument(symbol)
            if instrument is None:
                continue

            tick = parse_quote_tick(raw_tick, instrument)

            self._handle_data(tick)

    def subscribed_quote_ticks(self) -> tuple[str, ...]:
        """Currently subscribed symbols."""
        return tuple(sorted(self._subscribed_symbols))

    @property
    def is_polling(self) -> bool:
        """True if the poll loop task is running."""
        return self._poll_task is not None and not self._poll_task.done()

def _nanos_to_datetime(nanos: int | None) -> datetime | None:
    if nanos is None:
        return None
    return datetime.fromtimestamp(nanos / 1_000_000_000, tz=timezone.utc)

def _bar_spec_to_mt5_timeframe(bar_type) -> int:
    from nautilus_trader.model.enums import BarAggregation

    spec = bar_type.spec
    agg = spec.aggregation
    step = spec.step

    if agg == BarAggregation.MINUTE:
        tf_map = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6,
                  10: 10, 12: 12, 15: 15, 20: 20, 30: 30}
        return tf_map.get(step, mt5.TIMEFRAME_H1)

    if agg == BarAggregation.HOUR:
        tf_map = {1: mt5.TIMEFRAME_H1, 2: 16386, 3: 16387, 4: mt5.TIMEFRAME_H4,
                  6: 16390, 8: 16392, 12: 16396}
        return tf_map.get(step, mt5.TIMEFRAME_H1)

    if agg == BarAggregation.DAY:
        return mt5.TIMEFRAME_D1
    if agg == BarAggregation.WEEK:
        return 32769
    if agg == BarAggregation.MONTH:
        return 49153

    return mt5.TIMEFRAME_H1

#fix