"""
tests/conftest.py

Mocks MetaTrader5 at the sys.modules level BEFORE any nautilus_mt5 module
is imported. This is required because constants.py does a bare
`import MetaTrader5 as mt5` at module scope to access mt5.ORDER_FILLING_IOC.

Without this pre-mock, the import fails on CI runners and macOS/Linux
where the MetaTrader5 package cannot initialise its Windows DLL.
"""

import sys
from unittest.mock import MagicMock, patch
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# PRE-MOCK MetaTrader5 IN sys.modules
#
# Must happen before any nautilus_mt5 import. pytest loads conftest.py first,
# so placing the mock here guarantees it is in place when the test files
# (and their module-level imports of nautilus_mt5.*) are collected.
# ─────────────────────────────────────────────────────────────────────────────

_mt5_mock = MagicMock()

# Constants used at module-load time in constants.py
_mt5_mock.ORDER_FILLING_IOC   = 1
_mt5_mock.ORDER_FILLING_FOK   = 2
_mt5_mock.ORDER_FILLING_RETURN = 3

# Order type constants used in execution.py
_mt5_mock.ORDER_TYPE_BUY            = 0
_mt5_mock.ORDER_TYPE_SELL           = 1
_mt5_mock.ORDER_TYPE_BUY_LIMIT      = 2
_mt5_mock.ORDER_TYPE_SELL_LIMIT     = 3
_mt5_mock.ORDER_TYPE_BUY_STOP       = 4
_mt5_mock.ORDER_TYPE_SELL_STOP      = 5
_mt5_mock.ORDER_TYPE_BUY_STOP_LIMIT  = 6
_mt5_mock.ORDER_TYPE_SELL_STOP_LIMIT = 7

# Trade action constants
_mt5_mock.TRADE_ACTION_DEAL    = 1
_mt5_mock.TRADE_ACTION_PENDING = 5
_mt5_mock.TRADE_ACTION_SLTP    = 6
_mt5_mock.TRADE_ACTION_MODIFY  = 7
_mt5_mock.TRADE_ACTION_REMOVE  = 8

# Time-in-force constants
_mt5_mock.ORDER_TIME_GTC = 0
_mt5_mock.ORDER_TIME_DAY = 1
_mt5_mock.ORDER_TIME_GTD = 2

# Return code
_mt5_mock.TRADE_RETCODE_DONE = 10009

# Deal type constants
_mt5_mock.DEAL_TYPE_BUY  = 0
_mt5_mock.DEAL_TYPE_SELL = 1

# Position type constants
_mt5_mock.POSITION_TYPE_BUY  = 0
_mt5_mock.POSITION_TYPE_SELL = 1

# Default return values for common calls
_mt5_mock.initialize.return_value   = True
_mt5_mock.shutdown.return_value     = None
_mt5_mock.login.return_value        = True
_mt5_mock.last_error.return_value   = (0, "No error")
_mt5_mock.account_info.return_value = None
_mt5_mock.symbols_get.return_value  = []
_mt5_mock.symbol_info.return_value  = None
_mt5_mock.symbol_select.return_value = True
_mt5_mock.orders_get.return_value   = []
_mt5_mock.positions_get.return_value = []
_mt5_mock.history_deals_get.return_value = []
_mt5_mock.order_send.return_value   = None
_mt5_mock.copy_rates_range.return_value = None

# Register in sys.modules before any nautilus_mt5 import
sys.modules.setdefault("MetaTrader5", _mt5_mock)


# ─────────────────────────────────────────────────────────────────────────────
# NOW safe to import mt5connect
# ─────────────────────────────────────────────────────────────────────────────

from mt5connect.config import MT5Config  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# SHARED CONFIG FIXTURE
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def config():
    """A valid MT5Config with fast reconnect settings for testing."""
    return MT5Config(
        account=12345678,
        password="test_password",
        server="Exness-MT5Trial1",
        symbols=["EURUSD", "XAUUSD"],
        reconnect_initial_delay_s=0.01,
        reconnect_max_delay_s=0.05,
        reconnect_max_attempts=3,
        timeout_s=5.0,
    )


# ─────────────────────────────────────────────────────────────────────────────
# MT5 MODULE MOCK FIXTURE
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_mt5():
    """
    Patches the MetaTrader5 module used inside nautilus_mt5.connection.

    Provides realistic defaults for all common mt5.* calls so tests
    can focus on adapter logic rather than MT5 plumbing.

    Override in individual tests:
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")
    """
    with patch("mt5connect.connection.mt5") as mock:
        mock.initialize.return_value = True
        mock.shutdown.return_value   = None
        mock.login.return_value      = True
        mock.last_error.return_value = (0, "No error")

        # Realistic Exness demo account
        account = MagicMock()
        account.login        = 12345678
        account.server       = "Exness-MT5Trial1"
        account.balance      = 10000.00
        account.equity       = 10050.25
        account.margin       = 100.00
        account.margin_free  = 9950.25
        account.margin_level = 10050.25
        account.currency     = "USD"
        account.leverage     = 2000
        account.profit       = 50.25
        account.name         = "Test Trader"
        account.company      = "Exness Technologies Ltd"
        mock.account_info.return_value = account

        terminal = MagicMock()
        terminal.name            = "MetaTrader 5"
        terminal.path            = "C:\\Program Files\\MetaTrader 5"
        terminal.data_path       = "C:\\Users\\Trader\\AppData\\Roaming\\MetaQuotes\\Terminal"
        terminal.connected       = True
        terminal.ping_last       = 3
        terminal.retransmission  = 0.0
        mock.terminal_info.return_value = terminal

        yield mock
