"""
examples/live_simple_strategy.py

The same SMA crossover strategy from backtest_eurusd.py running live on MT5.

    python examples/live_simple_strategy.py

What changes vs the backtest
-----------------------------
    - Engine   : LiveTradingNode  (not BacktestEngine)
    - Data     : live MT5 ticks aggregated into bars in real time
    - Execution: real MT5 orders via MT5LiveExecutionClient
    - Config   : build_mt5_node_config() wires everything in one call

Everything else — the strategy class, the SMA logic, on_order_filled —
is identical to the backtest. That's the point.

Safety
------
    This example uses 0.01 lot size (micro lot) to minimise risk while
    demonstrating the live flow. Change TRADE_SIZE to suit your account.

    The magic_number (510 by default) tags every order this bot places.
    Orders without this number are ignored — safe to have MT5 open and
    trade manually alongside the bot.

    NEVER run a live strategy without understanding what it does.
    This is an educational example, not financial advice.
"""

import os
import time
import signal
import sys
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId, TraderId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.config import StrategyConfig, LoggingConfig

from mt5connect.config import MT5Config
from mt5connect.factories import (
    build_mt5_node_config,
    MT5LiveDataClientFactory,
    MT5LiveExecClientFactory,
)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION — loaded from .env (never hardcode credentials)
# Copy .env.example → .env and fill in your real values.
# ─────────────────────────────────────────────────────────────────────────────

# Load .env from the project root (one level up from examples/)
load_dotenv(Path(__file__).parent.parent / ".env")

def _require(key: str) -> str:
    """Read a required env var or exit with a clear message."""
    val = os.getenv(key)
    if not val:
        sys.exit(f"ERROR: '{key}' is not set. Add it to your .env file.")
    return val

MT5_ACCOUNT  = int(_require("MT5_ACCOUNT"))
MT5_PASSWORD = _require("MT5_PASSWORD")
MT5_SERVER   = _require("MT5_SERVER")
MT5_SYMBOLS  = [s.strip() for s in _require("MT5_SYMBOLS").split(",")]

FAST_PERIOD  = int(os.getenv("FAST_PERIOD",  "10"))
SLOW_PERIOD  = int(os.getenv("SLOW_PERIOD",  "30"))
TRADE_SIZE   = Decimal(os.getenv("TRADE_SIZE", "0.01"))


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY  (identical to backtest_eurusd.py — no changes needed)
# ─────────────────────────────────────────────────────────────────────────────

class SmaCrossConfig(StrategyConfig, frozen=True):
    instrument_id : str
    bar_type      : str
    fast_period   : int     = 10
    slow_period   : int     = 30
    trade_size    : Decimal = Decimal("0.01")


class SmaCrossStrategy(Strategy):
    """
    SMA crossover strategy — live version.

    Receives real bars aggregated from live MT5 ticks.
    Submits real orders via MT5LiveExecutionClient.
    """

    def __init__(self, config: SmaCrossConfig) -> None:
        super().__init__(config)

        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type      = BarType.from_str(config.bar_type)
        self.fast_period   = config.fast_period
        self.slow_period   = config.slow_period
        self.trade_size    = config.trade_size

        self._fast_prices: list[float] = []
        self._slow_prices: list[float] = []
        self._position_side: OrderSide | None = None

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument {self.instrument_id} not found")
            return

        # Subscribe to live bars — NautilusTrader builds them from tick data
        self.subscribe_bars(self.bar_type)
        self.log.info(
            f"SmaCrossStrategy LIVE — "
            f"fast={self.fast_period} slow={self.slow_period} "
            f"size={self.trade_size} lots on {self.instrument_id}"
        )

    def on_bar(self, bar: Bar) -> None:
        close = float(bar.close)
        self._fast_prices.append(close)
        self._slow_prices.append(close)

        if len(self._fast_prices) > self.fast_period:
            self._fast_prices.pop(0)
        if len(self._slow_prices) > self.slow_period:
            self._slow_prices.pop(0)

        if (len(self._fast_prices) < self.fast_period or
                len(self._slow_prices) < self.slow_period):
            return

        fast_sma = sum(self._fast_prices) / self.fast_period
        slow_sma = sum(self._slow_prices) / self.slow_period

        self.log.debug(
            f"Bar close={close:.5f}  "
            f"fast_sma={fast_sma:.5f}  slow_sma={slow_sma:.5f}"
        )

        if fast_sma > slow_sma and self._position_side != OrderSide.BUY:
            self._close_position()
            self._open_position(OrderSide.BUY)

        elif fast_sma < slow_sma and self._position_side != OrderSide.SELL:
            self._close_position()
            self._open_position(OrderSide.SELL)

    def _open_position(self, side: OrderSide) -> None:
        quantity = Quantity(float(self.trade_size), self.instrument.size_precision)
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=side,
            quantity=quantity,
        )
        self.submit_order(order)
        self._position_side = side
        self.log.info(f"Opening {side.name} {quantity} {self.instrument_id}")

    def _close_position(self) -> None:
        if self._position_side is None:
            return
        positions = self.cache.positions_open(instrument_id=self.instrument_id)
        for position in positions:
            close_side = (
                OrderSide.SELL if position.side.name == "LONG" else OrderSide.BUY
            )
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=close_side,
                quantity=position.quantity,
            )
            self.submit_order(order)
            self.log.info(
                f"Closing {position.side.name} {position.quantity} "
                f"{self.instrument_id}"
            )
        self._position_side = None

    def on_order_filled(self, event) -> None:
        self.log.info(
            f"✓ Fill: {event.order_side.name} {event.last_qty} "
            f"@ {event.last_px}  commission={event.commission}"
        )

    def on_stop(self) -> None:
        self.log.info("Strategy stopping — closing all positions")
        self._close_position()


# ─────────────────────────────────────────────────────────────────────────────
# NODE SETUP AND RUN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'═' * 60}")
    print(f"  SMA({FAST_PERIOD}/{SLOW_PERIOD}) Live Strategy — {MT5_SYMBOLS}")
    print(f"  Server  : {MT5_SERVER}")
    print(f"  Account : {MT5_ACCOUNT}")
    print(f"  Size    : {TRADE_SIZE} lots")
    print(f"  Press Ctrl+C to stop and close all positions")
    print(f"{'═' * 60}\n")

    # ── MT5 connection config ─────────────────────────────────────────────────
    mt5_config = MT5Config(
        account  = MT5_ACCOUNT,
        password = MT5_PASSWORD,
        server   = MT5_SERVER,
        symbols  = MT5_SYMBOLS,
        poll_interval_ms      = 100,    # tick polling
        exec_poll_interval_ms = 250,    # fill/order polling
    )

    # ── Strategy config ───────────────────────────────────────────────────────
    symbol        = MT5_SYMBOLS[0]
    instrument_id = f"{symbol}.MT5"
    # Bar type: 1-minute bars built from live ticks
    bar_type_str  = f"{instrument_id}-1-MINUTE-LAST-INTERNAL"

    strategy_config = SmaCrossConfig(
        instrument_id = instrument_id,
        bar_type      = bar_type_str,
        fast_period   = FAST_PERIOD,
        slow_period   = SLOW_PERIOD,
        trade_size    = TRADE_SIZE,
    )

    # ── Build and run the node ────────────────────────────────────────────────
    # build_mt5_node_config wires MT5Connection, MT5DataClient,
    # MT5LiveExecutionClient, and MT5InstrumentProvider in one call.
    #
    # IMPORTANT (NT 1.224+): Strategies must be added to the node AFTER
    # construction via node.trader.add_strategy(instance). Do NOT pass them into
    # build_mt5_node_config or TradingNodeConfig — NT expects
    # ImportableStrategyConfig objects there, not plain instances or tuples.
    node_config = build_mt5_node_config(mt5_config=mt5_config)

    # NT 1.224 node lifecycle — order matters:
    #   1. TradingNode(config)              — init kernel + engines
    #   2. add_*_client_factory()           — register MT5 factories
    #   3. node.trader.add_strategy()       — register strategy
    #   4. node.build()                     — instantiate MT5 clients (triggers MT5 connect)
    #   5. node.run()                       — start polling loops

    node = TradingNode(config=node_config)

    # Step 2 — register factories (issubclass check in NT requires proper inheritance)
    node.add_data_client_factory("MT5", MT5LiveDataClientFactory)
    node.add_exec_client_factory("MT5", MT5LiveExecClientFactory)

    # Step 3 — register strategy
    node.trader.add_strategy(SmaCrossStrategy(config=strategy_config))

    # Graceful shutdown on Ctrl+C
    def _shutdown(sig, frame):
        print("\nShutting down...")
        node.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print("Starting node — connecting to MT5...\n")
    node.build()  # Step 4 — connects to MT5, loads instruments
    node.run()    # Step 5 — start everything


if __name__ == "__main__":
    main()
