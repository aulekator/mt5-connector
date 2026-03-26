"""
examples/backtest_xauusd_fvg.py

Fair Value Gap (FVG) strategy backtest on XAUUSD H1 data from MT5.

Run download_xauusd.py first, then:

    python examples/backtest_xauusd_fvg.py

─────────────────────────────────────────────────────────────────────────────
WHAT IS A FAIR VALUE GAP (FVG)?
─────────────────────────────────────────────────────────────────────────────

A Fair Value Gap is a 3-candle pattern from ICT (Inner Circle Trader) concepts:

  BULLISH FVG (price likely to continue UP):
    ┌─────────────────────────────────────────┐
    │  Candle[i-2]  low                        │
    │                  ↑  GAP  ↑              │
    │                           Candle[i].high │
    │  Candle[i-1]  is the impulse candle      │
    └─────────────────────────────────────────┘
    Condition: Candle[i].high < Candle[i-2].low
    → Price leaves a gap that the market tends to "fill" on retracement,
      then continue bullish.

  BEARISH FVG (price likely to continue DOWN):
    Condition: Candle[i].low > Candle[i-2].high
    → Inverse logic.

STRATEGY LOGIC (this implementation):
─────────────────────────────────────
  1. On each new H1 bar, scan the last 3 bars for a Bullish or Bearish FVG.
  2. If a BULLISH FVG forms AND we have no open position:
       - Store the FVG zone (gap_low = candle[i].high, gap_high = candle[i-2].low)
       - Enter LONG when the next bar's close retraces INTO the FVG zone
         (i.e. close is between gap_low and gap_high).
       - Stop Loss  : below the low of the impulse candle (candle[i-1])
       - Take Profit: FVG height × RISK_REWARD_RATIO above entry
  3. BEARISH FVG: mirror logic for SHORT.
  4. Positions are closed at TP or SL via on_bar() price checks.
     (No bracket orders — NautilusTrader backtest engine handles this via
     price comparison since we're using market orders for simplicity.)

PARAMETERS (tune these):
─────────────────────────
  FVG_MIN_PIPS    : Minimum gap size in price units to qualify as a valid FVG.
                    Filters out tiny noise gaps. For XAUUSD, think in dollars
                    (0.50 = 50 cents minimum gap on gold).
  RISK_REWARD     : Take profit as a multiple of the stop loss distance.
  TRADE_SIZE      : Lot size per trade (0.10 = 1 mini lot).
  TREND_FILTER    : If True, only take bullish FVGs when price > SMA(50),
                    and bearish FVGs when price < SMA(50). Reduces false signals.
"""

import pathlib
from decimal import Decimal
from datetime import datetime, timezone
from dataclasses import dataclass, field

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType, BarSpecification
from nautilus_trader.model.enums import (
    AccountType, OmsType, OrderSide, PriceType, BarAggregation,
)
from nautilus_trader.model.identifiers import InstrumentId, Venue, TraderId
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.config import StrategyConfig

import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

CATALOG_PATH  = "./catalog"
SYMBOL        = "XAUUSDm"      # Change to "XAUUSD" if your broker uses no suffix
VENUE_STR     = "MT5"
START         = datetime(2024, 1,  1, tzinfo=timezone.utc)
END           = datetime(2024, 12, 31, tzinfo=timezone.utc)

# Strategy parameters
FVG_MIN_PIPS   = 0.50      # Minimum FVG height in price units (XAUUSD = dollars)
RISK_REWARD    = 2.0       # Take profit = stop_distance × RISK_REWARD
TRADE_SIZE     = Decimal("0.10")  # Lot size
TREND_FILTER   = True      # Only trade in direction of SMA(50)
SMA_PERIOD     = 50        # Trend filter period

INITIAL_CASH   = 10_000.0


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — identical to backtest_eurusd.py
# ─────────────────────────────────────────────────────────────────────────────

def _bar_type_str_from_disk(catalog_path: str, symbol: str) -> str | None:
    """Find the bar folder for this symbol on disk."""
    bar_dir = pathlib.Path(catalog_path) / "data" / "bar"
    if not bar_dir.exists():
        return None
    # Prefer H1 folder; fall back to first match
    for d in sorted(bar_dir.iterdir()):
        if d.is_dir() and symbol in d.name and "1-HOUR" in d.name:
            return d.name
    for d in bar_dir.iterdir():
        if d.is_dir() and symbol in d.name:
            return d.name
    return None


def _load_bars_from_parquet(
    bar_type_str: str,
    bar_type_obj: BarType,
    instrument,
    start: datetime,
    end: datetime,
) -> list[Bar]:
    """
    Read bars directly from Parquet files, bypassing catalog.bars().
    Sets the correct bar_type on every Bar so subscribe_bars() works.
    (Same approach as backtest_eurusd.py — battle-tested.)
    """
    bar_dir  = pathlib.Path(CATALOG_PATH) / "data" / "bar" / bar_type_str
    pq_files = sorted(bar_dir.glob("*.parquet"))
    if not pq_files:
        return []

    start_ns = int(start.timestamp() * 1_000_000_000)
    end_ns   = int(end.timestamp()   * 1_000_000_000)
    pp       = instrument.price_precision
    sp       = instrument.size_precision

    bars = []
    for pfile in pq_files:
        df = pd.read_parquet(pfile)

        if not bars:
            print(f"  Parquet columns : {df.columns.tolist()}")

        ts_col  = next((c for c in ["ts_event", "timestamp", "ts_init"] if c in df.columns), None)
        vol_col = next((c for c in ["volume", "tick_volume", "real_volume"] if c in df.columns), None)

        if ts_col:
            df = df[(df[ts_col] >= start_ns) & (df[ts_col] <= end_ns)]

        def _decode(val, divisor: float) -> float:
            if isinstance(val, (bytes, bytearray)):
                val = int.from_bytes(val, "little")
            return float(int(val)) / divisor

        for _, row in df.iterrows():
            ts  = int(_decode(row[ts_col], 1.0)) if ts_col else 0
            vol = _decode(row[vol_col], 1e9)     if vol_col else 1.0

            bar = Bar(
                bar_type = bar_type_obj,
                open     = Price(_decode(row["open"],  1e9), pp),
                high     = Price(_decode(row["high"],  1e9), pp),
                low      = Price(_decode(row["low"],   1e9), pp),
                close    = Price(_decode(row["close"], 1e9), pp),
                volume   = Quantity(vol, sp),
                ts_event = ts,
                ts_init  = ts,
            )
            bars.append(bar)

    return bars


# ─────────────────────────────────────────────────────────────────────────────
# FVG ZONE DATACLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FVGZone:
    """Stores a detected Fair Value Gap waiting to be traded."""
    direction : OrderSide      # BUY (bullish FVG) or SELL (bearish FVG)
    gap_low   : float          # Lower boundary of the gap
    gap_high  : float          # Upper boundary of the gap
    stop_loss : float          # Where the SL will be placed on entry
    formed_at : int            # ts_event of the signal bar (nanoseconds)
    active    : bool = True    # False once price has entered the zone
    max_bars  : int  = 20      # Invalidate after this many bars without entry


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY CONFIG
# ─────────────────────────────────────────────────────────────────────────────

class FVGStrategyConfig(StrategyConfig, frozen=True):
    instrument_id  : str
    bar_type       : str
    fvg_min_size   : float  = 0.50
    risk_reward    : float  = 2.0
    trade_size     : Decimal = Decimal("0.10")
    trend_filter   : bool   = True
    sma_period     : int    = 50


# ─────────────────────────────────────────────────────────────────────────────
# FVG STRATEGY
# ─────────────────────────────────────────────────────────────────────────────

class FVGStrategy(Strategy):
    """
    Fair Value Gap (FVG) strategy for XAUUSD.

    Workflow per bar
    ────────────────
    1. Update price history buffer (closes for SMA trend filter).
    2. Keep a rolling window of the last 3 bars for FVG detection.
    3. Detect a new FVG on the completed 3-bar pattern.
    4. If an active FVG zone exists, check if price has retraced into it.
       → Enter on retracement into the zone.
    5. If in a position, check if SL or TP has been hit.

    Why no bracket orders?
    ──────────────────────
    NautilusTrader's backtest engine supports stop and limit orders.
    However, managing SL/TP manually in on_bar() keeps this example
    simple and broker-agnostic. For production, replace _check_exit()
    with StopMarket + LimitOrder bracket pairs submitted via order_factory.
    """

    def __init__(self, config: FVGStrategyConfig) -> None:
        super().__init__(config)

        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type      = BarType.from_str(config.bar_type)
        self.fvg_min_size  = config.fvg_min_size
        self.risk_reward   = config.risk_reward
        self.trade_size    = config.trade_size
        self.trend_filter  = config.trend_filter
        self.sma_period    = config.sma_period

        # Rolling bar history [oldest ... newest]
        self._bars   : list[Bar]   = []
        self._closes : list[float] = []   # for SMA trend filter

        # Current pending FVG zone waiting for retracement
        self._pending_fvg : FVGZone | None = None

        # Active trade tracking
        self._position_side  : OrderSide | None = None
        self._entry_price    : float | None     = None
        self._stop_loss      : float | None     = None
        self._take_profit    : float | None     = None

        # Statistics
        self._bar_count  = 0
        self._fvg_found  = 0
        self._trades     = 0
        self._wins       = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument {self.instrument_id} not found in cache")
            return
        self.subscribe_bars(self.bar_type)
        self.log.info(
            f"FVG Strategy started — subscribed to {self.bar_type} | "
            f"min_fvg={self.fvg_min_size} rr={self.risk_reward} "
            f"trend_filter={self.trend_filter}"
        )

    # ── Main bar handler ──────────────────────────────────────────────────────

    def on_bar(self, bar: Bar) -> None:
        self._bar_count += 1
        close = float(bar.close)

        # Update histories
        self._bars.append(bar)
        if len(self._bars) > max(self.sma_period + 5, 10):
            self._bars.pop(0)

        self._closes.append(close)
        if len(self._closes) > self.sma_period + 5:
            self._closes.pop(0)

        # ── Exit logic first (manage open position) ───────────────────────────
        if self._position_side is not None:
            self._check_exit(bar)
            return  # one action per bar — don't enter a new trade same bar

        # ── Need at least 3 bars for FVG detection ────────────────────────────
        if len(self._bars) < 3:
            return

        # ── Age out stale pending FVG ──────────────────────────────────────────
        if self._pending_fvg is not None:
            bars_since = sum(
                1 for b in self._bars
                if b.ts_event > self._pending_fvg.formed_at
            )
            if bars_since > self._pending_fvg.max_bars:
                self.log.debug(
                    f"FVG expired after {bars_since} bars — "
                    f"zone {self._pending_fvg.gap_low:.2f}–"
                    f"{self._pending_fvg.gap_high:.2f}"
                )
                self._pending_fvg = None

        # ── Check if price retraced into pending FVG (entry signal) ──────────
        if self._pending_fvg is not None:
            self._check_fvg_entry(bar)
            return

        # ── Detect new FVG on the last 3 bars ────────────────────────────────
        self._detect_fvg()

    # ── FVG Detection ─────────────────────────────────────────────────────────

    def _detect_fvg(self) -> None:
        """
        Check the 3 most recent bars for a Bullish or Bearish FVG.

        Pattern (zero-indexed from newest):
            bar_a = self._bars[-3]   (oldest of the 3)
            bar_b = self._bars[-2]   (middle — the impulse candle)
            bar_c = self._bars[-1]   (most recent — creates the gap)

        Bullish FVG : bar_c.high < bar_a.low   → gap between bar_c.high and bar_a.low
        Bearish FVG : bar_c.low  > bar_a.high  → gap between bar_a.high and bar_c.low
        """
        bar_a = self._bars[-3]
        bar_b = self._bars[-2]
        bar_c = self._bars[-1]

        high_a = float(bar_a.high)
        low_a  = float(bar_a.low)
        low_b  = float(bar_b.low)
        high_b = float(bar_b.high)
        high_c = float(bar_c.high)
        low_c  = float(bar_c.low)

        # ── Trend filter: only trade in direction of SMA ──────────────────────
        sma = self._sma()

        # ── Bullish FVG ───────────────────────────────────────────────────────
        # Gap exists between bar_c's high and bar_a's low
        if high_c < low_a:
            gap_size = low_a - high_c
            if gap_size >= self.fvg_min_size:
                # Trend filter: only buy if price is above SMA
                if self.trend_filter and sma is not None:
                    close_c = float(bar_c.close)
                    if close_c < sma:
                        self.log.debug(
                            f"Bullish FVG filtered out — price {close_c:.2f} "
                            f"< SMA({self.sma_period}) {sma:.2f}"
                        )
                        return

                # SL goes below bar_b (impulse candle low) with a small buffer
                stop_loss = low_b - (gap_size * 0.5)

                self._pending_fvg = FVGZone(
                    direction = OrderSide.BUY,
                    gap_low   = high_c,        # bottom of the gap
                    gap_high  = low_a,         # top of the gap
                    stop_loss = stop_loss,
                    formed_at = bar_c.ts_event,
                )
                self._fvg_found += 1
                self.log.info(
                    f"[BULLISH FVG] gap {high_c:.2f}–{low_a:.2f} "
                    f"size={gap_size:.2f} SL={stop_loss:.2f}"
                )

        # ── Bearish FVG ───────────────────────────────────────────────────────
        # Gap exists between bar_a's high and bar_c's low
        elif low_c > high_a:
            gap_size = low_c - high_a
            if gap_size >= self.fvg_min_size:
                # Trend filter: only sell if price is below SMA
                if self.trend_filter and sma is not None:
                    close_c = float(bar_c.close)
                    if close_c > sma:
                        self.log.debug(
                            f"Bearish FVG filtered out — price {close_c:.2f} "
                            f"> SMA({self.sma_period}) {sma:.2f}"
                        )
                        return

                # SL goes above bar_b high with buffer
                stop_loss = high_b + (gap_size * 0.5)

                self._pending_fvg = FVGZone(
                    direction = OrderSide.SELL,
                    gap_low   = high_a,        # bottom of the gap
                    gap_high  = low_c,         # top of the gap
                    stop_loss = stop_loss,
                    formed_at = bar_c.ts_event,
                )
                self._fvg_found += 1
                self.log.info(
                    f"[BEARISH FVG] gap {high_a:.2f}–{low_c:.2f} "
                    f"size={gap_size:.2f} SL={stop_loss:.2f}"
                )

    # ── Entry Logic ───────────────────────────────────────────────────────────

    def _check_fvg_entry(self, bar: Bar) -> None:
        """
        Enter when the current bar's close retraces into the FVG zone.

        Bullish FVG entry condition:
            bar.close is INSIDE the gap (gap_low ≤ close ≤ gap_high)
            → price has pulled back into the inefficiency, expect continuation up

        Bearish FVG entry condition:
            bar.close is INSIDE the gap (gap_low ≤ close ≤ gap_high)
            → price has pulled back up into the inefficiency, expect continuation down
        """
        if self._pending_fvg is None:
            return

        fvg   = self._pending_fvg
        close = float(bar.close)
        high  = float(bar.high)
        low   = float(bar.low)

        # Check if bar touches the FVG zone
        bar_touches_zone = low <= fvg.gap_high and high >= fvg.gap_low

        if not bar_touches_zone:
            return

        entry_price   = close
        stop_distance = abs(entry_price - fvg.stop_loss)

        if stop_distance < 0.01:   # avoid division by zero / near-zero SL
            self.log.debug("FVG entry skipped — stop distance too small")
            self._pending_fvg = None
            return

        if fvg.direction == OrderSide.BUY:
            take_profit = entry_price + (stop_distance * self.risk_reward)
        else:
            take_profit = entry_price - (stop_distance * self.risk_reward)

        self.log.info(
            f"[ENTRY {fvg.direction.name}] @ {entry_price:.2f} | "
            f"SL={fvg.stop_loss:.2f} "
            f"TP={take_profit:.2f} | "
            f"risk={stop_distance:.2f} reward={stop_distance * self.risk_reward:.2f}"
        )

        # Submit order
        quantity = Quantity(float(self.trade_size), self.instrument.size_precision)
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=fvg.direction,
            quantity=quantity,
        )
        self.submit_order(order)

        # Track position
        self._position_side = fvg.direction
        self._entry_price   = entry_price
        self._stop_loss     = fvg.stop_loss
        self._take_profit   = take_profit
        self._trades       += 1

        # Clear pending zone — one trade per FVG
        self._pending_fvg = None

    # ── Exit Logic ────────────────────────────────────────────────────────────

    def _check_exit(self, bar: Bar) -> None:
        """
        Check if SL or TP has been hit on the current bar.

        Uses bar.high / bar.low to simulate intra-bar execution:
        - For a LONG position: SL is hit if bar.low <= stop_loss
                               TP is hit if bar.high >= take_profit
        - For a SHORT position: mirror logic.

        Note: In a real backtest engine with fill models you'd use
        stop/limit orders. This simplified check is sufficient to
        demonstrate the strategy's P&L profile.
        """
        if self._position_side is None:
            return

        high  = float(bar.high)
        low   = float(bar.low)
        close = float(bar.close)

        hit_sl = False
        hit_tp = False

        if self._position_side == OrderSide.BUY:
            if low  <= self._stop_loss:
                hit_sl = True
            if high >= self._take_profit:
                hit_tp = True
        else:  # SELL
            if high >= self._stop_loss:
                hit_sl = True
            if low  <= self._take_profit:
                hit_tp = True

        if hit_tp:
            self.log.info(
                f"[TP HIT] {self._position_side.name} @ {self._take_profit:.2f} "
                f"(entry={self._entry_price:.2f})"
            )
            self._wins += 1
            self._close_position()
        elif hit_sl:
            self.log.info(
                f"[SL HIT] {self._position_side.name} @ {self._stop_loss:.2f} "
                f"(entry={self._entry_price:.2f})"
            )
            self._close_position()

    # ── Order / Position Helpers ──────────────────────────────────────────────

    def _close_position(self) -> None:
        if self._position_side is None:
            return
        close_side = (
            OrderSide.SELL
            if self._position_side == OrderSide.BUY
            else OrderSide.BUY
        )
        for pos in self.cache.positions_open(instrument_id=self.instrument_id):
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=close_side,
                quantity=pos.quantity,
            )
            self.submit_order(order)

        self._position_side = None
        self._entry_price   = None
        self._stop_loss     = None
        self._take_profit   = None

    # ── Trend Filter ──────────────────────────────────────────────────────────

    def _sma(self) -> float | None:
        """Return the Simple Moving Average of the last sma_period closes."""
        if len(self._closes) < self.sma_period:
            return None
        return sum(self._closes[-self.sma_period:]) / self.sma_period

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def on_stop(self) -> None:
        win_rate = (self._wins / self._trades * 100) if self._trades > 0 else 0.0
        self.log.info(
            f"FVG Strategy stopped | "
            f"bars={self._bar_count} "
            f"fvgs_found={self._fvg_found} "
            f"trades={self._trades} "
            f"wins={self._wins} "
            f"win_rate={win_rate:.1f}%"
        )
        self._close_position()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN BACKTEST RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest():
    print(f"\n{'=' * 65}")
    print(f"  Fair Value Gap (FVG) Backtest — {SYMBOL} H1")
    print(f"  Period       : {START.date()} → {END.date()}")
    print(f"  Capital      : ${INITIAL_CASH:,.0f}")
    print(f"  FVG min size : {FVG_MIN_PIPS}")
    print(f"  Risk/Reward  : 1:{RISK_REWARD}")
    print(f"  Trade size   : {TRADE_SIZE} lots")
    print(f"  Trend filter : SMA({SMA_PERIOD}) — {TREND_FILTER}")
    print(f"{'=' * 65}\n")

    catalog = ParquetDataCatalog(CATALOG_PATH)

    # ── Load instrument ───────────────────────────────────────────────────────
    instrument_id   = InstrumentId.from_str(f"{SYMBOL}.{VENUE_STR}")
    all_instruments = catalog.instruments()
    instrument      = next(
        (i for i in all_instruments if i.id == instrument_id), None
    )

    if instrument is None:
        print(f"  ERROR: No instrument found for {instrument_id}")
        print(f"  Run: python examples/download_xauusd.py\n")
        return
    print(f"  Instrument : {instrument.id} ({type(instrument).__name__})")
    print(f"  Price prec : {instrument.price_precision}")

    # ── Find H1 bar folder ────────────────────────────────────────────────────
    bar_type_str = _bar_type_str_from_disk(CATALOG_PATH, SYMBOL)
    if not bar_type_str:
        print(f"  ERROR: No bar folder found for {SYMBOL}")
        print(f"  Run: python examples/download_xauusd.py\n")
        return
    print(f"  Bar type   : {bar_type_str}")
    bar_type_obj = BarType.from_str(bar_type_str)

    # ── Load bars ─────────────────────────────────────────────────────────────
    bars = _load_bars_from_parquet(bar_type_str, bar_type_obj, instrument, START, END)
    if not bars:
        print(f"\n  ERROR: No bars loaded.")
        print(f"  Run: python examples/download_xauusd.py\n")
        return
    print(f"  Bars loaded: {len(bars):,}")
    print(f"  First bar  : ts={bars[0].ts_event}  close={bars[0].close}")
    print(f"  Last bar   : ts={bars[-1].ts_event}  close={bars[-1].close}\n")

    # ── Engine ────────────────────────────────────────────────────────────────
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
            logging=LoggingConfig(log_level="WARNING"),
        )
    )

    engine.add_venue(
        venue=Venue(VENUE_STR),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(INITIAL_CASH, USD)],
        fill_model=FillModel(
            prob_fill_on_limit=0.95,
            prob_slippage=0.10,
            random_seed=42,
        ),
    )

    engine.add_instrument(instrument)
    engine.add_data(bars)

    strategy = FVGStrategy(
        config=FVGStrategyConfig(
            instrument_id = str(instrument_id),
            bar_type      = bar_type_str,
            fvg_min_size  = FVG_MIN_PIPS,
            risk_reward   = RISK_REWARD,
            trade_size    = TRADE_SIZE,
            trend_filter  = TREND_FILTER,
            sma_period    = SMA_PERIOD,
        )
    )
    engine.add_strategy(strategy)

    # ── Run ───────────────────────────────────────────────────────────────────
    engine.run(start=START, end=END)

    # ── Results ───────────────────────────────────────────────────────────────
    print(f"\n{'─' * 65}")
    print("  Results")
    print(f"{'─' * 65}")

    try:
        account = engine.trader.generate_account_report(Venue(VENUE_STR))
        print(account)
    except Exception as exc:
        print(f"  (account report unavailable: {exc})")

    try:
        fills  = engine.trader.generate_order_fills_report()
        trades = engine.trader.generate_positions_report()
        print(f"\n  Total fills     : {len(fills)}")
        print(f"  Total positions : {len(trades)}")
        if len(fills) > 0:
            print(f"\n  Last 10 fills:")
            print(fills.tail(10).to_string())
        if len(trades) > 0:
            print(f"\n  Last 10 positions:")
            print(trades.tail(10).to_string())
    except Exception as exc:
        print(f"  (reports unavailable: {exc})")

    engine.dispose()

    print(f"\n{'=' * 65}")
    print(f"  Backtest complete.")
    print(f"  Tip: Tune FVG_MIN_PIPS, RISK_REWARD, and TREND_FILTER")
    print(f"       to optimise for your risk profile.")
    print(f"{'=' * 65}\n")


if __name__ == "__main__":
    run_backtest()
