import pytest
from unittest.mock import MagicMock, patch
from nautilus_mt5.config import MT5Config


# ── Shared config fixture ─────────────────────────────────────────────────────

@pytest.fixture
def config():
    """A valid MT5Config with fast reconnect settings for testing."""
    return MT5Config(
        account=12345678,
        password="test_password",
        server="Exness-MT5Trial1",
        symbols=["EURUSD", "XAUUSD"],
        reconnect_initial_delay_s=0.01,   # near-instant for tests
        reconnect_max_delay_s=0.05,
        reconnect_max_attempts=3,
        timeout_s=5.0,
    )


# ── MT5 module mock fixture ───────────────────────────────────────────────────

@pytest.fixture
def mock_mt5():
    """
    Patches the entire MetaTrader5 module.

    Every test that needs MT5 uses this fixture. It gives you a mock
    object where you can configure return values for any mt5.* call.

    Default behaviour (all green):
        mt5.initialize()   → True
        mt5.login()        → True
        mt5.last_error()   → (0, "No error")
        mt5.account_info() → realistic demo account namedtuple
        mt5.terminal_info() → realistic terminal namedtuple

    Override in individual tests:
        mock_mt5.initialize.return_value = False
        mock_mt5.last_error.return_value = (5, "IPC timeout")
    """
    with patch("nautilus_mt5.connection.mt5") as mock:
        # ── initialize / shutdown ──────────────────────────────────────────
        mock.initialize.return_value = True
        mock.shutdown.return_value = None

        # ── login ──────────────────────────────────────────────────────────
        mock.login.return_value = True

        # ── last_error: (0, "No error") = success ─────────────────────────
        mock.last_error.return_value = (0, "No error")

        # ── account_info: realistic Exness demo account ───────────────────
        account = MagicMock()
        account.login    = 12345678
        account.server   = "Exness-MT5Trial1"
        account.balance  = 10000.00
        account.equity   = 10050.25
        account.margin   = 100.00
        account.margin_free = 9950.25
        account.margin_level = 10050.25
        account.currency = "USD"
        account.leverage = 2000
        account.profit   = 50.25
        account.name     = "Test Trader"
        account.company  = "Exness Technologies Ltd"
        mock.account_info.return_value = account

        # ── terminal_info ─────────────────────────────────────────────────
        terminal = MagicMock()
        terminal.name         = "MetaTrader 5"
        terminal.path         = "C:\\Program Files\\MetaTrader 5"
        terminal.data_path    = "C:\\Users\\Trader\\AppData\\Roaming\\MetaQuotes\\Terminal"
        terminal.connected    = True
        terminal.ping_last    = 3
        terminal.retransmission = 0.0
        mock.terminal_info.return_value = terminal

        yield mock