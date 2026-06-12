"""
examples/test_place_order.py

Places a single 0.01 lot BUY market order on XAUUSD, waits 5 seconds,
then closes it. Use this to confirm the execution path works end-to-end
before running the full strategy.

    python examples/test_place_order.py

What it tests
-------------
  - MT5 connection and login
  - mt5.order_send() for market BUY with automatic filling mode retry
  - Position detection via mt5.positions_get()
  - Closing a position via opposite market order
"""

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
import MetaTrader5 as mt5

load_dotenv(Path(__file__).parent.parent / ".env")

def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        sys.exit(f"ERROR: '{key}' not set in .env")
    return val

ACCOUNT = int(_require("MT5_ACCOUNT"))
PASSWORD = _require("MT5_PASSWORD")
SERVER   = _require("MT5_SERVER")
SYMBOL   = os.getenv("MT5_SYMBOLS", "XAUUSD").split(",")[0].strip()
VOLUME   = 0.01
MAGIC    = 510


def connect():
    print(f"Connecting to MT5 — account {ACCOUNT} on {SERVER}...")
    if not mt5.initialize():
        sys.exit(f"mt5.initialize() failed: {mt5.last_error()}")
    if not mt5.login(ACCOUNT, PASSWORD, SERVER):
        mt5.shutdown()
        sys.exit(f"mt5.login() failed: {mt5.last_error()}")
    info = mt5.account_info()
    print(f"Connected — balance: {info.balance:.2f} {info.currency}")
    
    # Check AutoTrading status
    terminal_info = mt5.terminal_info()
    if terminal_info:
        print(f"AutoTrading enabled: {terminal_info.trade_allowed}")
        if not terminal_info.trade_allowed:
            print("\n⚠️  WARNING: AutoTrading is DISABLED in MT5 terminal!")
            print("   Click the 'AutoTrading' button in MT5 toolbar (should show green dot)")
            print("   Then run this script again.\n")
            return False
    return True


def place_order_with_retry(side: str) -> int | None:
    """Place a market order, trying different filling modes."""
    mt5.symbol_select(SYMBOL, True)
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        print(f"ERROR: cannot get tick for {SYMBOL}")
        return None

    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if side == "BUY" else tick.bid

    # Try different filling modes (some symbols like XAUUSD don't support IOC)
    filling_modes = [
        (mt5.ORDER_FILLING_IOC, "IOC"),
        (mt5.ORDER_FILLING_RETURN, "RETURN"),
        (mt5.ORDER_FILLING_FOK, "FOK"),
    ]
    
    for fill_mode, mode_name in filling_modes:
        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       SYMBOL,
            "volume":       VOLUME,
            "type":         order_type,
            "price":        price,
            "deviation":    20,
            "magic":        MAGIC,
            "comment":      f"test_{side.lower()}",
            "type_filling": fill_mode,
            "type_time":    mt5.ORDER_TIME_GTC,
        }
        
        result = mt5.order_send(request)
        
        if result is None:
            print(f"  order_send returned None with {mode_name}")
            continue
            
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"  ✓ {side} {VOLUME} {SYMBOL} @ {price:.2f}  ticket={result.order} (filling_mode={mode_name})")
            return result.order
        elif result.retcode == 10030:  # Unsupported filling mode
            print(f"  {mode_name} not supported (retcode=10030), trying next...")
            continue
        else:
            print(f"  Failed with {mode_name}: retcode={result.retcode} comment={result.comment}")
            # Don't continue on other errors
            return None
    
    print(f"ERROR: All filling modes failed for {side} order")
    return None


def close_position_with_retry(ticket: int):
    """Close a position by ticket, trying different filling modes."""
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        print(f"  Position {ticket} not found (may already be closed)")
        return

    pos = positions[0]
    # Determine closing side (opposite of position)
    if pos.type == mt5.ORDER_TYPE_BUY:
        close_side = "SELL"
        close_type = mt5.ORDER_TYPE_SELL
    else:
        close_side = "BUY"
        close_type = mt5.ORDER_TYPE_BUY
    
    tick = mt5.symbol_info_tick(SYMBOL)
    price = tick.bid if close_side == "SELL" else tick.ask

    filling_modes = [
        (mt5.ORDER_FILLING_IOC, "IOC"),
        (mt5.ORDER_FILLING_RETURN, "RETURN"),
        (mt5.ORDER_FILLING_FOK, "FOK"),
    ]
    
    for fill_mode, mode_name in filling_modes:
        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       SYMBOL,
            "volume":       pos.volume,
            "type":         close_type,
            "position":     ticket,
            "price":        price,
            "deviation":    20,
            "magic":        MAGIC,
            "comment":      "test_close",
            "type_filling": fill_mode,
            "type_time":    mt5.ORDER_TIME_GTC,
        }

        result = mt5.order_send(request)
        
        if result is None:
            print(f"  order_send returned None with {mode_name}")
            continue
            
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            # Calculate P&L
            deals = mt5.history_deals_get(position=ticket)
            pnl = sum(d.profit for d in deals) if deals else 0.0
            print(f"  ✓ Closed ticket={ticket}  P&L: {pnl:+.2f} (filling_mode={mode_name})")
            return
        elif result.retcode == 10030:  # Unsupported filling mode
            print(f"  Close: {mode_name} not supported (retcode=10030), trying next...")
            continue
        else:
            print(f"  Close failed with {mode_name}: retcode={result.retcode} comment={result.comment}")
    
    print(f"ERROR: Could not close position {ticket}")


def main():
    print(f"\n{'─' * 50}")
    print(f"  Test Order — {SYMBOL}  {VOLUME} lots")
    print(f"{'─' * 50}\n")

    if not connect():
        sys.exit(1)

    print(f"\nPlacing BUY order...")
    ticket = place_order_with_retry("BUY")
    if ticket is None:
        mt5.shutdown()
        sys.exit(1)

    print(f"\nWaiting 5 seconds...")
    time.sleep(5)

    print(f"\nClosing position...")
    close_position_with_retry(ticket)

    mt5.shutdown()
    print(f"\n{'─' * 50}")
    print(f"  Done — execution path confirmed working")
    print(f"{'─' * 50}\n")


if __name__ == "__main__":
    main()