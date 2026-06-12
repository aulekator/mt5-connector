# download_csvs.py
"""
Download XAUUSD data as CSV files using MT5.
Works with trial accounts (uses copy_rates_from_pos fallback).
Downloads: 5min, 15min, 30min, 1H, 4H, Daily
"""

import os
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Config
SYMBOL = os.environ.get("MT5_SYMBOLS", "XAUUSD").split(",")[0].strip()
OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_DIR.mkdir(exist_ok=True)

# Timeframes to download (including Daily and 4H for IPDA)
TIMEFRAMES = {
    "M5":   {"mt5": 5,      "label": "5min",   "bars_needed": 10000},   # ~35 days
    "M15":  {"mt5": 15,     "label": "15min",  "bars_needed": 8000},    # ~83 days
    "M30":  {"mt5": 30,     "label": "30min",  "bars_needed": 5000},    # ~104 days
    "H1":   {"mt5": 60,     "label": "1hour",  "bars_needed": 3000},    # ~125 days
    "H4":   {"mt5": 240,    "label": "4hour",  "bars_needed": 1000},    # ~166 days
    "D1":   {"mt5": 1440,   "label": "daily",  "bars_needed": 500},     # ~500 days
}

# Date range
END_DATE = datetime.now(timezone.utc)
START_DATE = END_DATE - timedelta(days=90)  # 3 months minimum for IPDA 60-day range


def download_mt5_bars(symbol: str, tf_minutes: int, bars_needed: int) -> pd.DataFrame:
    """
    Download bars from MT5 using copy_rates_from_pos (trial-friendly).
    This pulls the most recent N bars regardless of date range.
    """
    import MetaTrader5 as mt5
    
    # Map minutes to MT5 timeframe constant
    tf_map = {
        5: mt5.TIMEFRAME_M5,
        15: mt5.TIMEFRAME_M15,
        30: mt5.TIMEFRAME_M30,
        60: mt5.TIMEFRAME_H1,
        240: mt5.TIMEFRAME_H4,
        1440: mt5.TIMEFRAME_D1,
    }
    tf = tf_map.get(tf_minutes)
    if tf is None:
        return pd.DataFrame()
    
    # Use copy_rates_from_pos (start from most recent bar, go backwards)
    # This works much better with trial accounts
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars_needed)
    
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={"tick_volume": "volume"})
    df = df[["time", "open", "high", "low", "close", "volume"]]
    df = df.sort_values("time").reset_index(drop=True)
    
    return df


def main():
    import MetaTrader5 as mt5
    
    print(f"\n{'='*60}")
    print(f"  Downloading {SYMBOL} data")
    print(f"  Requesting up to 90 days of data (MT5 will return what's available)")
    print(f"{'='*60}\n")
    
    # Connect MT5
    if not mt5.initialize(
        login=int(os.environ["MT5_ACCOUNT"]),
        password=os.environ["MT5_PASSWORD"],
        server=os.environ["MT5_SERVER"],
    ):
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    
    print(f"  Connected to {os.environ['MT5_SERVER']}")
    
    # Check symbol info
    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None:
        print(f"\n  ✗ Symbol {SYMBOL} not found. Available symbols:")
        symbols = mt5.symbols_get()
        gold_like = [s.name for s in symbols if 'GOLD' in s.name or 'XAU' in s.name][:5]
        print(f"    Try one of: {gold_like}")
        mt5.shutdown()
        return
    
    print(f"  Symbol: {SYMBOL} (Digits: {symbol_info.digits})\n")
    
    # Download each timeframe
    for tf_name, cfg in TIMEFRAMES.items():
        print(f"  Downloading {tf_name} ({cfg['label']})...")
        
        df = download_mt5_bars(SYMBOL, cfg["mt5"], cfg["bars_needed"])
        
        if not df.empty:
            csv_path = OUTPUT_DIR / f"{SYMBOL}_{cfg['label']}.csv"
            df.to_csv(csv_path, index=False)
            print(f"    ✓ Saved {len(df):,} bars to {csv_path}")
            print(f"      Range: {df['time'].iloc[0]} → {df['time'].iloc[-1]}")
        else:
            print(f"    ✗ No data for {tf_name}")
        print()
    
    mt5.shutdown()
    print("  Done.")


if __name__ == "__main__":
    main()