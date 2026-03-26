"""
examples/download_historical_data.py

Downloads H1 bar data from MT5 and writes it into a NautilusTrader
Parquet catalog. Run this before running backtest_eurusd.py.

    python examples/download_historical_data.py
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

# Load credentials from .env in the project root
load_dotenv(Path(__file__).parent.parent / ".env")

def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        sys.exit(f"ERROR: '{key}' is not set. Add it to your .env file.")
    return val

ACCOUNT  = int(_require("MT5_ACCOUNT"))
PASSWORD = _require("MT5_PASSWORD")
SERVER   = _require("MT5_SERVER")
SYMBOL   = os.getenv("DOWNLOAD_SYMBOL", _require("MT5_SYMBOLS").split(",")[0].strip())
START    = datetime(2024, 1,  1, tzinfo=timezone.utc)
END      = datetime(2024, 12, 31, tzinfo=timezone.utc)
CATALOG  = "./catalog"
# ─────────────────────────────────────────────────────────────────────────────

config = MT5Config(
    account=ACCOUNT,
    password=PASSWORD,
    server=SERVER,
    symbols=[SYMBOL],
)

conn     = MT5Connection(config)
conn.connect()

provider = MT5InstrumentProvider(conn)
catalog  = ParquetDataCatalog(CATALOG)

# Load and write the instrument definition first
# This is required so the backtest engine can find it in the catalog
instrument = provider.load_symbol(SYMBOL)
catalog.write_data([instrument])
print(f"Instrument written: {instrument.id}")

# Download bars
downloader = MT5DataDownloader(conn, provider, catalog)

result = downloader.download_bars(
    symbol=SYMBOL,
    start=START,
    end=END,
    timeframe=16385,  # H1
)
print(result)

conn.disconnect()
