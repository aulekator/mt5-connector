"""
examples/live_fvg_strategy.py

FVG (Fair Value Gap) strategy running live on MT5.

    python examples/live_fvg_strategy.py
"""

import os
import signal
import sys
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.config import StrategyConfig

from mt5connect.config import MT5Config
from mt5connect.factories import (
    build_mt5_node_config,
    MT5LiveDataClientFactory,
    MT5LiveExecClientFactory,
)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION — loaded from .env
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        sys.exit(f"ERROR: '{key}' is not set. Add it to your .env file.")
    return val

MT5_ACCOUNT  = int(_require("MT5_ACCOUNT"))
MT5_PASSWORD = _require("MT5_PASSWORD")
MT5_SERVER   = _require("MT5_SERVER")
MT5_SYMBOLS  = [s.strip() for s in _require("MT5_SYMBOLS").split(",")]

# Strategy parameters
FVG_MIN_PIPS = float(os.getenv("FVG_MIN_PIPS", "0.50"))
RISK_REWARD  = float(os.getenv("RISK_REWARD", "2.0"))
TRADE_SIZE   = Decimal(os.getenv("TRADE_SIZE", "0.01"))


# ─────────────────────────────────────────────────────────────────────────────
# FVG ZONE DATACLASS
# ─────────────────────────────────────────────────────────────────────────────

class FVGZone:
    def __init__(self, direction, gap_low, gap_high, stop_loss, formed_at):
        self.direction = direction
        self.gap_low = gap_low
        self.gap_high = gap_high
        self.stop_loss = stop_loss
        self.formed_at = formed_at
        self.max_bars = 20


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY CONFIG
# ─────────────────────────────────────────────────────────────────────────────

class FVGStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    fvg_min_size: float = 0.50
    risk_reward: float = 2.0
    trade_size: Decimal = Decimal("0.01")


# ─────────────────────────────────────────────────────────────────────────────
# FVG STRATEGY (bypasses reconciliation by not using complex reporting)
# ─────────────────────────────────────────────────────────────────────────────

class FVGStrategy(Strategy):
    """Fair Value Gap strategy - matches SMA pattern exactly."""

    def __init__(self, config: FVGStrategyConfig) -> None:
        super().__init__(config)

        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.fvg_min_size = config.fvg_min_size
        self.risk_reward = config.risk_reward
        self.trade_size = config.trade_size

        # Rolling bar history
        self._bars: list[Bar] = []

        # Current pending FVG zone
        self._pending_fvg: FVGZone | None = None

        # Position tracking
        self._position_side: OrderSide | None = None
        self._entry_price: float | None = None
        self._stop_loss: float | None = None
        self._take_profit: float | None = None
        self._bar_count = 0

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument {self.instrument_id} not found")
            return

        self.subscribe_bars(self.bar_type)
        self.log.info(f"FVGStrategy LIVE — {self.instrument_id}")

    def on_bar(self, bar: Bar) -> None:
        self._bar_count += 1

        # Keep rolling window
        self._bars.append(bar)
        if len(self._bars) > 100:
            self._bars.pop(0)

        # Exit logic first
        if self._position_side is not None:
            self._check_exit(bar)
            return

        # Need at least 3 bars for FVG detection
        if len(self._bars) < 3:
            return

        # Age out stale pending FVG
        if self._pending_fvg is not None:
            bars_since = sum(1 for b in self._bars if b.ts_event > self._pending_fvg.formed_at)
            if bars_since > self._pending_fvg.max_bars:
                self._pending_fvg = None

        # Check for entry into pending FVG
        if self._pending_fvg is not None:
            self._check_fvg_entry(bar)
            return

        # Detect new FVG
        self._detect_fvg()

    def _detect_fvg(self) -> None:
        """Detect 3-candle FVG pattern."""
        bar_a = self._bars[-3]
        bar_b = self._bars[-2]
        bar_c = self._bars[-1]

        high_a = float(bar_a.high)
        low_a = float(bar_a.low)
        low_b = float(bar_b.low)
        high_b = float(bar_b.high)
        high_c = float(bar_c.high)
        low_c = float(bar_c.low)

        # Bullish FVG
        if high_c < low_a:
            gap_size = low_a - high_c
            if gap_size >= self.fvg_min_size:
                stop_loss = low_b - (gap_size * 0.5)
                self._pending_fvg = FVGZone(
                    direction=OrderSide.BUY,
                    gap_low=high_c,
                    gap_high=low_a,
                    stop_loss=stop_loss,
                    formed_at=bar_c.ts_event,
                )
                self.log.info(f"[FVG] BULLISH | gap={gap_size:.2f}")

        # Bearish FVG
        elif low_c > high_a:
            gap_size = low_c - high_a
            if gap_size >= self.fvg_min_size:
                stop_loss = high_b + (gap_size * 0.5)
                self._pending_fvg = FVGZone(
                    direction=OrderSide.SELL,
                    gap_low=high_a,
                    gap_high=low_c,
                    stop_loss=stop_loss,
                    formed_at=bar_c.ts_event,
                )
                self.log.info(f"[FVG] BEARISH | gap={gap_size:.2f}")

    def _check_fvg_entry(self, bar: Bar) -> None:
        """Enter when price retraces into FVG zone."""
        if self._pending_fvg is None:
            return

        fvg = self._pending_fvg
        close = float(bar.close)
        high = float(bar.high)
        low = float(bar.low)

        touches_zone = low <= fvg.gap_high and high >= fvg.gap_low

        if not touches_zone:
            return

        entry_price = close
        stop_distance = abs(entry_price - fvg.stop_loss)

        if stop_distance < 0.01:
            self._pending_fvg = None
            return

        if fvg.direction == OrderSide.BUY:
            take_profit = entry_price + (stop_distance * self.risk_reward)
        else:
            take_profit = entry_price - (stop_distance * self.risk_reward)

        # Submit order - exact same pattern as SMA strategy
        quantity = Quantity(float(self.trade_size), self.instrument.size_precision)
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=fvg.direction,
            quantity=quantity,
        )
        self.submit_order(order)

        # Track position
        self._position_side = fvg.direction
        self._entry_price = entry_price
        self._stop_loss = fvg.stop_loss
        self._take_profit = take_profit

        self.log.info(f"ENTRY {fvg.direction.name} @ {entry_price:.2f}")
        print(f"\n✅ ORDER SUBMITTED: {fvg.direction.name} @ {entry_price:.2f}")
        print(f"   SL: {fvg.stop_loss:.2f}  TP: {take_profit:.2f}\n")

        self._pending_fvg = None

    def _check_exit(self, bar: Bar) -> None:
        """Check if SL or TP hit."""
        if self._position_side is None:
            return

        high = float(bar.high)
        low = float(bar.low)

        hit_sl = False
        hit_tp = False

        if self._position_side == OrderSide.BUY:
            if low <= self._stop_loss:
                hit_sl = True
            if high >= self._take_profit:
                hit_tp = True
        else:
            if high >= self._stop_loss:
                hit_sl = True
            if low <= self._take_profit:
                hit_tp = True

        if hit_tp or hit_sl:
            reason = "TP" if hit_tp else "SL"
            exit_price = self._take_profit if hit_tp else self._stop_loss

            if self._position_side == OrderSide.BUY:
                pnl_points = exit_price - self._entry_price
            else:
                pnl_points = self._entry_price - exit_price

            pnl_dollar = round(pnl_points * float(self.trade_size) * 100, 2)

            self.log.info(f"EXIT {reason} | {self._position_side.name} | {pnl_points:+.2f} pts")
            print(f"\n🔚 EXIT {reason}: {self._position_side.name} | PnL: {pnl_points:+.2f} pts (${pnl_dollar:.2f})\n")

            self.close_all_positions(self.instrument_id)

            self._position_side = None
            self._entry_price = None
            self._stop_loss = None
            self._take_profit = None

    def on_order_filled(self, event) -> None:
        self.log.info(f"✓ FILLED: {event.order_side.name} {event.last_qty} @ {event.last_px}")

    def on_stop(self) -> None:
        self.log.info("FVGStrategy stopping")
        if self._position_side is not None:
            self.close_all_positions(self.instrument_id)

    # ─────────────────────────────────────────────────────────────────────────
    # Bypass reconciliation errors - return empty lists for report generators
    # These are called by NT during reconciliation but we don't need them
    # ─────────────────────────────────────────────────────────────────────────

    def generate_order_status_report(self, *args, **kwargs):
        """Bypass reconciliation - return None"""
        return None

    def generate_order_status_reports(self, *args, **kwargs):
        """Bypass reconciliation - return empty list"""
        return []

    def generate_fill_reports(self, *args, **kwargs):
        """Bypass reconciliation - return empty list"""
        return []

    def generate_position_status_reports(self, *args, **kwargs):
        """Bypass reconciliation - return empty list"""
        return []


# ─────────────────────────────────────────────────────────────────────────────
# NODE SETUP AND RUN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'═' * 60}")
    print(f"  FVG Live Strategy — {MT5_SYMBOLS}")
    print(f"  Server  : {MT5_SERVER}")
    print(f"  Account : {MT5_ACCOUNT}")
    print(f"  Size    : {TRADE_SIZE} lots")
    print(f"  Min FVG : {FVG_MIN_PIPS}")
    print(f"  RR      : 1:{RISK_REWARD}")
    print(f"  Press Ctrl+C to stop and close all positions")
    print(f"{'═' * 60}\n")

    # MT5 connection config
    mt5_config = MT5Config(
        account=MT5_ACCOUNT,
        password=MT5_PASSWORD,
        server=MT5_SERVER,
        symbols=MT5_SYMBOLS,
        poll_interval_ms=100,
        exec_poll_interval_ms=250,
    )

    # Strategy config
    symbol = MT5_SYMBOLS[0]
    instrument_id = f"{symbol}.MT5"
    bar_type_str = f"{instrument_id}-1-HOUR-LAST-INTERNAL"

    strategy_config = FVGStrategyConfig(
        instrument_id=instrument_id,
        bar_type=bar_type_str,
        fvg_min_size=FVG_MIN_PIPS,
        risk_reward=RISK_REWARD,
        trade_size=TRADE_SIZE,
    )

    # Build and run node
    node_config = build_mt5_node_config(mt5_config=mt5_config)
    node = TradingNode(config=node_config)

    node.add_data_client_factory("MT5", MT5LiveDataClientFactory)
    node.add_exec_client_factory("MT5", MT5LiveExecClientFactory)

    node.trader.add_strategy(FVGStrategy(config=strategy_config))

    def shutdown(sig, frame):
        print("\nShutting down...")
        node.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("Starting node — connecting to MT5...\n")
    node.build()
    node.run()


if __name__ == "__main__":
    main()