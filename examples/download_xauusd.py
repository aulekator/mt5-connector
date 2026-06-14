"""
examples/download_xauusd.py

Downloads H1 (and M15) bar data for XAUUSD from MT5 and writes it
into a NautilusTrader Parquet catalog.

Run this BEFORE running backtest_xauusd_fvg.py:

    python examples/download_xauusd.py

Your broker may use a suffix on the symbol name:
    Exness standard  → XAUUSDm
    Exness zero/raw  → XAUUSD
    IC Markets       → XAUUSD
    Pepperstone      → XAUUSD

Set the correct name in your .env file as:
    MT5_SYMBOLS=XAUUSDm          # or XAUUSD, etc.

Or override SYMBOL directly below.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from mt5connect.config import MT5Config
from mt5connect.connection import MT5Connection
from mt5connect.providers import MT5InstrumentProvider
from mt5connect.downloader import MT5DataDownloader

import MetaTrader5 as mt5

# ─────────────────────────────────────────────────────────────────────────────
# LOAD CREDENTIALS
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent / ".env")

def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        sys.exit(f"ERROR: '{key}' is not set. Add it to your .env file.")
    return val

ACCOUNT  = int(_require("MT5_ACCOUNT"))
PASSWORD = _require("MT5_PASSWORD")
SERVER   = _require("MT5_SERVER")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# Change SYMBOL to match your broker's exact name for Gold (check Market Watch)
# ─────────────────────────────────────────────────────────────────────────────

SYMBOL  = os.getenv("DOWNLOAD_SYMBOL",
          os.getenv("MT5_SYMBOLS", "XAUUSDm").split(",")[0].strip())

START   = datetime(2024, 1,  1, tzinfo=timezone.utc)
END     = datetime(2024, 12, 31, tzinfo=timezone.utc)
CATALOG = "./catalog"

# Timeframes to download.
# FVG strategy uses H1 for trend context + M15 for entry signals.
# Remove mt5.TIMEFRAME_M15 if you only want H1.
TIMEFRAMES = [
    mt5.TIMEFRAME_M15,   # 15-minute bars  (FVG entry timeframe)
    mt5.TIMEFRAME_H1,    # 1-hour bars     (FVG trend context)
    mt5.TIMEFRAME_H4,    # 4-hour bars     (higher timeframe bias)
]

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'=' * 60}")
    print(f"  XAUUSD Historical Data Downloader")
    print(f"  Symbol  : {SYMBOL}")
    print(f"  Server  : {SERVER}")
    print(f"  Account : {ACCOUNT}")
    print(f"  Period  : {START.date()} → {END.date()}")
    print(f"  Catalog : {CATALOG}")
    print(f"{'=' * 60}\n")

    config = MT5Config(
        account=ACCOUNT,
        password=PASSWORD,
        server=SERVER,
        symbols=[SYMBOL],
    )

    conn = MT5Connection(config)
    print(f"Connecting to MT5...")
    conn.connect()
    print(f"Connected: {conn}\n")

    provider   = MT5InstrumentProvider(conn)
    catalog    = ParquetDataCatalog(CATALOG)
    downloader = MT5DataDownloader(conn, provider, catalog)

    # ── Write instrument definition ──────────────────────────────────────────
    # Required so the backtest engine can find the instrument in the catalog.
    instrument = provider.load_symbol(SYMBOL)
    catalog.write_data([instrument])
    print(f"Instrument written : {instrument.id}")
    print(f"  Price precision  : {instrument.price_precision}")
    print(f"  Size precision   : {instrument.size_precision}\n")

    # ── Download each timeframe ──────────────────────────────────────────────
    tf_names = {
        mt5.TIMEFRAME_M1:  "M1",
        mt5.TIMEFRAME_M5:  "M5",
        mt5.TIMEFRAME_M15: "M15",
        mt5.TIMEFRAME_M30: "M30",
        mt5.TIMEFRAME_H1:  "H1",
        mt5.TIMEFRAME_H4:  "H4",
        mt5.TIMEFRAME_D1:  "D1",
    }

    total_rows = 0
    for tf in TIMEFRAMES:
        tf_name = tf_names.get(tf, str(tf))
        print(f"Downloading {SYMBOL} {tf_name} bars...")
        result = downloader.download_bars(
            symbol=SYMBOL,
            start=START,
            end=END,
            timeframe=tf,
        )
        print(f"  {result}\n")
        total_rows += result.total_written
        if result.errors:
            print(f"  Errors:")
            for err in result.errors:
                print(f"    - {err}")

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"{'─' * 60}")
    print(f"  Download complete!")
    print(f"  Total bars written : {total_rows:,}")
    print(f"  Catalog path       : {CATALOG}")
    print(f"\n  Next step:")
    print(f"    python examples/backtest_xauusd_fvg.py")
    print(f"{'─' * 60}\n")

    conn.disconnect()


if __name__ == "__main__":
    main()
