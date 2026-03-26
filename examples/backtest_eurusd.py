"""
examples/backtest_eurusd.py

SMA crossover backtest on EURUSDm H1 data downloaded from MT5.

Run download_historical_data.py first, then:

    python examples/backtest_eurusd.py
"""

import pathlib
from decimal import Decimal
from datetime import datetime, timezone

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType, BarSpecification
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, PriceType, BarAggregation
from nautilus_trader.model.identifiers import InstrumentId, Venue, TraderId
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.config import StrategyConfig


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

CATALOG_PATH = "./catalog"
SYMBOL       = "EURUSDm"
VENUE_STR    = "MT5"
START        = datetime(2024, 1,  1, tzinfo=timezone.utc)
END          = datetime(2024, 12, 31, tzinfo=timezone.utc)

FAST_PERIOD  = 10
SLOW_PERIOD  = 30
TRADE_SIZE   = Decimal("0.10")
INITIAL_CASH = 10_000.0


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _bar_type_str_from_disk(catalog_path: str, symbol: str) -> str | None:
    bar_dir = pathlib.Path(catalog_path) / "data" / "bar"
    if not bar_dir.exists():
        return None
    for d in bar_dir.iterdir():
        if d.is_dir() and symbol in d.name:
            return d.name
    return None


def _load_bars_from_parquet(bar_type_str: str,
                             bar_type_obj: BarType,
                             instrument,
                             start: datetime,
                             end: datetime) -> list[Bar]:
    """
    Read bars directly from parquet files on disk.
    Guarantees the correct bar_type is set on every Bar object so the
    strategy's subscription matches the data in the engine.
    """
    import pandas as pd

    bar_dir   = pathlib.Path(CATALOG_PATH) / "data" / "bar" / bar_type_str
    pq_files  = sorted(bar_dir.glob("*.parquet"))
    if not pq_files:
        return []

    start_ns = int(start.timestamp() * 1_000_000_000)
    end_ns   = int(end.timestamp()   * 1_000_000_000)
    pp       = instrument.price_precision
    sp       = instrument.size_precision

    bars = []
    for pfile in pq_files:
        df = pd.read_parquet(pfile)

        # Show columns on first file so we can debug if needed
        if not bars:
            print(f"  Parquet columns: {df.columns.tolist()}")

        # Determine timestamp column
        ts_col = next((c for c in ["ts_event", "timestamp", "ts_init"]
                       if c in df.columns), None)
        if ts_col:
            df = df[(df[ts_col] >= start_ns) & (df[ts_col] <= end_ns)]

        # Determine volume column
        vol_col = next((c for c in ["volume", "tick_volume", "real_volume"]
                        if c in df.columns), None)

        def _decode(val, divisor: float) -> float:
                """NT catalog stores all numerics as little-endian int64 bytes.
                Prices use divisor=1e9, volumes use divisor=1e2 (2 dp)."""
                if isinstance(val, (bytes, bytearray)):
                    val = int.from_bytes(val, "little")
                return float(int(val)) / divisor

        for _, row in df.iterrows():
            ts  = _decode(row[ts_col], 1.0) if ts_col else 0
            ts  = int(ts)

            vol = _decode(row[vol_col], 1e9) if vol_col else 1.0

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
# STRATEGY
# ─────────────────────────────────────────────────────────────────────────────

class SmaCrossConfig(StrategyConfig, frozen=True):
    instrument_id : str
    bar_type      : str
    fast_period   : int     = 10
    slow_period   : int     = 30
    trade_size    : Decimal = Decimal("0.10")


class SmaCrossStrategy(Strategy):

    def __init__(self, config: SmaCrossConfig) -> None:
        super().__init__(config)
        self.instrument_id  = InstrumentId.from_str(config.instrument_id)
        self.bar_type       = BarType.from_str(config.bar_type)
        self.fast_period    = config.fast_period
        self.slow_period    = config.slow_period
        self.trade_size     = config.trade_size
        self._fast_prices: list[float] = []
        self._slow_prices: list[float] = []
        self._position_side: OrderSide | None = None
        self._bar_count     = 0

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument {self.instrument_id} not found in cache")
            return
        self.subscribe_bars(self.bar_type)
        self.log.info(f"SmaCross started — subscribed to {self.bar_type}")

    def on_bar(self, bar: Bar) -> None:
        self._bar_count += 1
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

    def _close_position(self) -> None:
        if self._position_side is None:
            return
        for pos in self.cache.positions_open(instrument_id=self.instrument_id):
            close_side = OrderSide.SELL if pos.side.name == "LONG" else OrderSide.BUY
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=close_side,
                quantity=pos.quantity,
            )
            self.submit_order(order)
        self._position_side = None

    def on_stop(self) -> None:
        self.log.info(f"SmaCross stopped — processed {self._bar_count} bars")
        self._close_position()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest():
    print(f"\n{'=' * 60}")
    print(f"  SMA({FAST_PERIOD}/{SLOW_PERIOD}) Crossover Backtest -- {SYMBOL} H1")
    print(f"  Period : {START.date()} -> {END.date()}")
    print(f"  Capital : ${INITIAL_CASH:,.0f}")
    print(f"{'=' * 60}\n")

    catalog = ParquetDataCatalog(CATALOG_PATH)

    # ── Load instrument ───────────────────────────────────────────────────────
    instrument_id   = InstrumentId.from_str(f"{SYMBOL}.{VENUE_STR}")
    all_instruments = catalog.instruments()
    instrument      = next((i for i in all_instruments if i.id == instrument_id), None)

    if instrument is None:
        print(f"  ERROR: No instrument found for {instrument_id}")
        print(f"  Run: python examples/download_historical_data.py\n")
        return
    print(f"  Instrument : {instrument.id} ({type(instrument).__name__})")

    # ── Find bar type from disk folder name ───────────────────────────────────
    bar_type_str = _bar_type_str_from_disk(CATALOG_PATH, SYMBOL)
    if not bar_type_str:
        print(f"  ERROR: No bar folder for {SYMBOL}")
        print(f"  Run: python examples/download_historical_data.py\n")
        return
    print(f"  Bar type   : {bar_type_str}")
    bar_type_obj = BarType.from_str(bar_type_str)

    # ── Load bars directly from parquet ──────────────────────────────────────
    # We bypass catalog.bars() entirely and build Bar objects ourselves.
    # This guarantees the correct bar_type is set on every bar so that
    # strategy.subscribe_bars(bar_type) correctly receives them in on_bar().
    bars = _load_bars_from_parquet(bar_type_str, bar_type_obj, instrument, START, END)
    if not bars:
        print(f"\n  ERROR: No bars loaded from parquet files.")
        print(f"  Run: python examples/download_historical_data.py\n")
        return
    print(f"  Bars loaded: {len(bars):,}")
    print(f"  First bar  : {bars[0].ts_event}  close={bars[0].close}")
    print(f"  Last bar   : {bars[-1].ts_event}  close={bars[-1].close}\n")

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

    strategy = SmaCrossStrategy(
        config=SmaCrossConfig(
            instrument_id = str(instrument_id),
            bar_type      = bar_type_str,
            fast_period   = FAST_PERIOD,
            slow_period   = SLOW_PERIOD,
            trade_size    = TRADE_SIZE,
        )
    )
    engine.add_strategy(strategy)

    # ── Run ───────────────────────────────────────────────────────────────────
    engine.run(start=START, end=END)

    # ── Results ───────────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print("  Results")
    print(f"{'─' * 60}")

    try:
        account = engine.trader.generate_account_report(Venue(VENUE_STR))
        print(account)
    except Exception as exc:
        print(f"  (account report: {exc})")

    try:
        fills  = engine.trader.generate_order_fills_report()
        trades = engine.trader.generate_positions_report()
        print(f"\n  Total fills     : {len(fills)}")
        print(f"  Total positions : {len(trades)}")
        if len(fills) > 0:
            print(f"\n  First 5 fills:")
            print(fills.head())
    except Exception as exc:
        print(f"  (report: {exc})")

    engine.dispose()
    print(f"\n{'=' * 60}\n")


if __name__ == "__main__":
    run_backtest()
