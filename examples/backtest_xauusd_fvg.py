# backtest/run_fvg_backtest.py
"""
FVG Strategy Backtest Runner

Run:
"""

import pathlib
import sys
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType, PriceType, BarAggregation, AggregationSource
from nautilus_trader.model.identifiers import InstrumentId, Venue, TraderId
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from strategy import FVGStrategy, FVGStrategyConfig

import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

CATALOG_PATH = "./catalog"
SYMBOL = "XAUUSD"  # Change to "XAUUSD" if your broker uses no suffix
VENUE_STR = "MT5"
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 12, 31, tzinfo=timezone.utc)

# Strategy parameters
FVG_MIN_PIPS = 0.50
RISK_REWARD = 2.0
TRADE_SIZE = Decimal("0.10")
TREND_FILTER = True
SMA_PERIOD = 50
WARMUP_BARS = 100

INITIAL_CASH = 10_000.0


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _bar_type_str_from_disk(catalog_path: str, symbol: str) -> str | None:
    """Find the bar folder for this symbol on disk."""
    bar_dir = pathlib.Path(catalog_path) / "data" / "bar"
    if not bar_dir.exists():
        return None
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
    catalog_path: str,
) -> list[Bar]:
    """Read bars directly from Parquet files."""
    bar_dir = pathlib.Path(catalog_path) / "data" / "bar" / bar_type_str
    pq_files = sorted(bar_dir.glob("*.parquet"))
    if not pq_files:
        return []

    start_ns = int(start.timestamp() * 1_000_000_000)
    end_ns = int(end.timestamp() * 1_000_000_000)
    pp = instrument.price_precision
    sp = instrument.size_precision

    bars = []
    for pfile in pq_files:
        df = pd.read_parquet(pfile)

        ts_col = next((c for c in ["ts_event", "timestamp", "ts_init"] if c in df.columns), None)
        vol_col = next((c for c in ["volume", "tick_volume", "real_volume"] if c in df.columns), None)

        if ts_col:
            df = df[(df[ts_col] >= start_ns) & (df[ts_col] <= end_ns)]

        def _decode(val, divisor: float) -> float:
            if isinstance(val, (bytes, bytearray)):
                val = int.from_bytes(val, "little")
            return float(int(val)) / divisor

        for _, row in df.iterrows():
            ts = int(_decode(row[ts_col], 1.0)) if ts_col else 0
            vol = _decode(row[vol_col], 1e9) if vol_col else 1.0

            bar = Bar(
                bar_type=bar_type_obj,
                open=Price(_decode(row["open"], 1e9), pp),
                high=Price(_decode(row["high"], 1e9), pp),
                low=Price(_decode(row["low"], 1e9), pp),
                close=Price(_decode(row["close"], 1e9), pp),
                volume=Quantity(vol, sp),
                ts_event=ts,
                ts_init=ts,
            )
            bars.append(bar)

    return bars


# ─────────────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────────────

def print_summary_report(stats: dict, start: datetime, end: datetime) -> None:
    """Print a clean summary report."""
    print(f"\n{'=' * 70}")
    print(f"  FAIR VALUE GAP (FVG) BACKTEST SUMMARY")
    print(f"{'=' * 70}")

    print(f"\n  📊 TRADE STATISTICS")
    print(f"  {'─' * 58}")
    print(f"  Total Trades       : {stats['total_trades']}")
    print(f"  Wins               : {stats['wins']}")
    print(f"  Losses             : {stats['losses']}")
    print(f"  Win Rate           : {stats['win_rate']:.1f}%")

    print(f"\n  💰 P&L SUMMARY")
    print(f"  {'─' * 58}")
    print(f"  Net P&L            : ${stats['net_pnl']:>10,.2f}")
    print(f"  Gross Profit       : ${stats['gross_profit']:>10,.2f}")
    print(f"  Gross Loss         : ${stats['gross_loss']:>10,.2f}")
    print(f"  Profit Factor      : {stats['profit_factor']:.3f}")
    print(f"  Return             : {stats['return_pct']:.2f}%")
    print(f"  Final Equity       : ${stats['final_equity']:>10,.2f}")

    print(f"\n  📈 TRADE AVERAGES")
    print(f"  {'─' * 58}")
    print(f"  Average Win        : ${stats['avg_win']:>10,.2f}")
    print(f"  Average Loss       : ${stats['avg_loss']:>10,.2f}")
    print(f"  Best Trade         : ${stats['best_trade']:>10,.2f}")
    print(f"  Worst Trade        : ${stats['worst_trade']:>10,.2f}")

    print(f"\n  ⚠️  RISK METRICS")
    print(f"  {'─' * 58}")
    print(f"  Max Drawdown       : ${stats['max_drawdown']:>10,.2f}")
    print(f"  Max Drawdown %     : {stats['max_drawdown_pct']:.2f}%")

    print(f"\n  🔍 SIGNAL STATISTICS")
    print(f"  {'─' * 58}")
    print(f"  Total FVGs Found   : {stats['total_fvgs']}")
    if stats['total_fvgs'] > 0:
        print(f"  Trade-to-FVG Ratio : {stats['total_trades']/stats['total_fvgs']:.2f}")

    print(f"\n{'=' * 70}")
    print(f"  Backtest Complete — {start.date()} to {end.date()}")
    print(f"{'=' * 70}\n")


def print_last_trades(trade_log, num_trades: int = 10):
    """Print the last N trades."""
    if not trade_log:
        return

    print(f"\n  📋 LAST {num_trades} TRADES")
    print(f"  {'─' * 70}")
    print(f"  {'#':<3} {'Direction':<8} {'Entry':<8} {'Exit':<8} {'P&L':<10} {'Status':<6}")
    print(f"  {'─' * 70}")
    
    for i, trade in enumerate(trade_log[-num_trades:], 1):
        sign = "+" if trade.pnl >= 0 else ""
        print(f"  {i:<3} {trade.direction:<8} {trade.entry_price:<8.2f} {trade.exit_price:<8.2f} {sign}${trade.pnl:<8.2f} {trade.status:<6}")
    
    print(f"  {'─' * 70}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN BACKTEST RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest():
    print(f"\n{'=' * 70}")
    print(f"  Fair Value Gap (FVG) Backtest — {SYMBOL} H1")
    print(f"  Period       : {START.date()} → {END.date()}")
    print(f"  Capital      : ${INITIAL_CASH:,.0f}")
    print(f"  FVG min size : {FVG_MIN_PIPS}")
    print(f"  Risk/Reward  : 1:{RISK_REWARD}")
    print(f"  Trade size   : {TRADE_SIZE} lots")
    print(f"  Trend filter : SMA({SMA_PERIOD}) — {TREND_FILTER}")
    print(f"{'=' * 70}\n")

    catalog = ParquetDataCatalog(CATALOG_PATH)

    # Load instrument
    instrument_id = InstrumentId.from_str(f"{SYMBOL}.{VENUE_STR}")
    all_instruments = catalog.instruments()
    instrument = next((i for i in all_instruments if i.id == instrument_id), None)

    if instrument is None:
        print(f"  ERROR: No instrument found for {instrument_id}")
        print(f"  Run: python examples/download_xauusd.py\n")
        return

    # Find bar folder
    bar_type_str = _bar_type_str_from_disk(CATALOG_PATH, SYMBOL)
    if not bar_type_str:
        print(f"  ERROR: No bar folder found for {SYMBOL}")
        print(f"  Run: python examples/download_xauusd.py\n")
        return

    bar_type_obj = BarType.from_str(bar_type_str)

    # Load bars
    bars = _load_bars_from_parquet(bar_type_str, bar_type_obj, instrument, START, END, CATALOG_PATH)
    if not bars:
        print(f"\n  ERROR: No bars loaded.")
        print(f"  Run: python examples/download_xauusd.py\n")
        return

    print(f"  Loaded {len(bars):,} bars")
    print(f"  First bar   : close={bars[0].close}")
    print(f"  Last bar    : close={bars[-1].close}\n")

    # Engine
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
            instrument_id=str(instrument_id),
            bar_type=bar_type_str,
            fvg_min_size=FVG_MIN_PIPS,
            risk_reward=RISK_REWARD,
            trade_size=TRADE_SIZE,
            trend_filter=TREND_FILTER,
            sma_period=SMA_PERIOD,
            warmup_bars=WARMUP_BARS,
        )
    )
    engine.add_strategy(strategy)

    # Run
    engine.run(start=START, end=END)

    # Extract stats from strategy
    stats = strategy.get_stats(INITIAL_CASH)

    # Print clean summary report
    print_summary_report(stats, START, END)

    # Print last trades
    print_last_trades(strategy.get_trade_log(), 10)

    engine.dispose()


if __name__ == "__main__":
    run_backtest()