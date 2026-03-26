"""
examples/test_place_order.py

Places a single 0.01 lot BUY market order on EURUSDm, waits 5 seconds,
then closes it. Use this to confirm the execution path works end-to-end
before running the full strategy.

    python examples/test_place_order.py

What it tests
-------------
  - MT5 connection and login
  - mt5.order_send() for market BUY
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
SYMBOL   = os.getenv("MT5_SYMBOLS", "EURUSDm").split(",")[0].strip()
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


def place_order(side: str) -> int | None:
    """Place a market order. side = 'BUY' or 'SELL'. Returns ticket or None."""
    mt5.symbol_select(SYMBOL, True)
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        print(f"ERROR: cannot get tick for {SYMBOL}")
        return None

    order_type = mt5.ORDER_TYPE_BUY  if side == "BUY"  else mt5.ORDER_TYPE_SELL
    price      = tick.ask            if side == "BUY"  else tick.bid

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       VOLUME,
        "type":         order_type,
        "price":        price,
        "deviation":    20,
        "magic":        MAGIC,
        "comment":      f"test_{side.lower()}",
        "type_filling": mt5.ORDER_FILLING_IOC,
        "type_time":    mt5.ORDER_TIME_GTC,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        retcode = result.retcode if result else "None"
        comment = result.comment if result else "no result"
        print(f"ERROR: order_send failed — retcode={retcode} comment={comment}")
        return None

    print(f"  ✓ {side} {VOLUME} {SYMBOL} @ {price:.5f}  ticket={result.order}")
    return result.order


def close_position(ticket: int):
    """Close a position by ticket using an opposite market order."""
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        print(f"  Position {ticket} not found (may already be closed)")
        return

    pos  = positions[0]
    side = "SELL" if pos.type == mt5.ORDER_TYPE_BUY else "BUY"
    tick = mt5.symbol_info_tick(SYMBOL)
    price = tick.bid if side == "SELL" else tick.ask

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       pos.volume,
        "type":         mt5.ORDER_TYPE_SELL if side == "SELL" else mt5.ORDER_TYPE_BUY,
        "position":     ticket,
        "price":        price,
        "deviation":    20,
        "magic":        MAGIC,
        "comment":      "test_close",
        "type_filling": mt5.ORDER_FILLING_IOC,
        "type_time":    mt5.ORDER_TIME_GTC,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        retcode = result.retcode if result else "None"
        print(f"ERROR: close failed — retcode={retcode}")
        return

    # P&L
    deals = mt5.history_deals_get(position=ticket)
    pnl   = sum(d.profit for d in deals) if deals else 0.0
    print(f"  ✓ Closed ticket={ticket}  P&L: {pnl:+.2f} {mt5.account_info().currency}")


def main():
    print(f"\n{'─' * 50}")
    print(f"  Test Order — {SYMBOL}  {VOLUME} lots")
    print(f"{'─' * 50}\n")

    connect()

    print(f"\nPlacing BUY order...")
    ticket = place_order("BUY")
    if ticket is None:
        mt5.shutdown()
        sys.exit(1)

    print(f"\nWaiting 5 seconds...")
    time.sleep(5)

    print(f"\nClosing position...")
    close_position(ticket)

    mt5.shutdown()
    print(f"\n{'─' * 50}")
    print(f"  Done — execution path confirmed working")
    print(f"{'─' * 50}\n")


if __name__ == "__main__":
    main()
