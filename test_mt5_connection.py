"""
test_mt5_connection.py

Simple test to verify MetaTrader 5 connection.
Run this to confirm MT5 is working before using the adapter.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import MetaTrader5 as mt5
import numpy as np

# Load credentials from .env
load_dotenv(Path(__file__).parent / ".env")

# Get credentials
ACCOUNT = int(os.getenv("MT5_ACCOUNT", 0))
PASSWORD = os.getenv("MT5_PASSWORD", "")
SERVER = os.getenv("MT5_SERVER", "")
SYMBOL = os.getenv("MT5_SYMBOLS", "EURUSD").split(",")[0].strip()

def main():
    print("\n" + "=" * 50)
    print("  MT5 Connection Test")
    print("=" * 50)
    
    # Step 1: Initialize MT5
    print("\n[1] Initializing MT5...")
    if not mt5.initialize():
        print(f"    ✗ FAILED: {mt5.last_error()}")
        print("\n    Make sure MetaTrader 5 is OPEN and RUNNING")
        return False
    
    print("    ✓ MT5 initialized")
    
    # Step 2: Login to broker
    print(f"\n[2] Logging in to {SERVER}...")
    if not mt5.login(ACCOUNT, PASSWORD, SERVER):
        print(f"    ✗ FAILED: {mt5.last_error()}")
        print("\n    Check your account number, password, and server name")
        mt5.shutdown()
        return False
    
    print(f"    ✓ Logged in as account {ACCOUNT}")
    
    # Step 3: Get account info
    print("\n[3] Account Information:")
    info = mt5.account_info()
    if info:
        print(f"    Balance:     ${info.balance:,.2f}")
        print(f"    Equity:      ${info.equity:,.2f}")
        print(f"    Free Margin: ${info.margin_free:,.2f}")
        print(f"    Leverage:    1:{info.leverage}")
        print(f"    Currency:    {info.currency}")
    else:
        print("    ✗ Could not retrieve account info")
    
    # Step 4: Test symbol
    print(f"\n[4] Testing symbol: {SYMBOL}")
    mt5.symbol_select(SYMBOL, True)
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick:
        spread = round((tick.ask - tick.bid) * 100000, 1)
        print(f"    Bid:  {tick.bid}")
        print(f"    Ask:  {tick.ask}")
        print(f"    Spread: {spread} points")
        print(f"    ✓ Symbol is active")
    else:
        print(f"    ✗ Could not get tick for {SYMBOL}")
        print("      Check if symbol exists in Market Watch")
    
    # Step 5: Test historical data
    print("\n[5] Testing historical data (last 5 H1 bars)...")
    from datetime import datetime, timedelta, timezone
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=5)
    rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_H1, start, end)
    
    # Check if rates is not None and has data (numpy array)
    if rates is not None and len(rates) > 0:
        print(f"    ✓ Retrieved {len(rates)} bars")
        last = rates[-1]
        print(f"    Last H1 close: {last['close']:.5f}")
    else:
        print("    ✗ Could not retrieve historical data")
        if rates is None:
            print(f"      Error: {mt5.last_error()}")
    
    # Success!
    print("\n" + "=" * 50)
    print("  ✓ MT5 IS CONNECTED AND WORKING ✓")
    print("=" * 50 + "\n")
    
    # Cleanup
    mt5.shutdown()
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)