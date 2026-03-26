"""
nautilus_mt5/config.py

User-facing configuration for the nautilus-mt5 adapter.
This is the only file users need to touch to connect their broker.
"""

from dataclasses import dataclass
from mt5connect.constants import (
    DEFAULT_POLL_INTERVAL_MS,
    DEFAULT_EXEC_POLL_INTERVAL_MS,
    RECONNECT_INITIAL_DELAY_S,
    RECONNECT_MAX_DELAY_S,
    RECONNECT_MAX_ATTEMPTS,
    MT5_MAGIC_NUMBER,
)


@dataclass
class MT5Config:
    """
    Configuration for the MT5 adapter.

    Parameters
    ----------
    account : int
        Your MT5 account number (shown in the top-left of the MT5 terminal).
    password : str
        Your MT5 account password.
    server : str
        Your broker's MT5 server name.
        Find it in MT5 → File → Open Account → search your broker.
        Examples:
            Exness standard demo : "Exness-MT5Trial1" or "Exness-MT5Trial9"
            Exness live          : "Exness-MT5Real8"
            IC Markets demo      : "ICMarketsSC-Demo"
    symbols : list[str]
        Symbol names EXACTLY as they appear in MT5 Market Watch.
        Different brokers use different naming conventions — use the exact
        name shown in your MT5 terminal, including any suffixes.

        Examples by broker:
            Exness standard : ["EURUSDm", "XAUUSDm", "BTCUSDm"]
            Exness zero/raw : ["EURUSD",  "XAUUSD",  "BTCUSD"]
            IC Markets      : ["EURUSD",  "XAUUSD",  "BTCUSD"]
            Pepperstone     : ["EURUSD",  "XAUUSD",  "BTCUSD"]

        The adapter automatically handles suffix normalisation internally —
        you just provide the exact broker symbol name and it works.

    poll_interval_ms : int
        How often (milliseconds) to poll MT5 for live tick data.
        Default: 100ms. Lower = fresher data, more CPU.
    exec_poll_interval_ms : int
        How often (milliseconds) to poll MT5 for position/fill updates.
        Default: 250ms.
    magic_number : int
        Unique order identifier for this bot instance.
        Change if running multiple bots simultaneously.
        Default: 510.
    reconnect_initial_delay_s : float
        Seconds before first reconnect attempt. Default: 1.0s.
    reconnect_max_delay_s : float
        Maximum seconds between reconnect attempts. Default: 60.0s.
    reconnect_max_attempts : int
        Give up after this many reconnect attempts. Default: 20.
    timeout_s : float
        Seconds to wait for MT5 terminal response. Default: 10.0s.

    Examples
    --------
    Exness standard demo (symbols have 'm' suffix):
        config = MT5Config(
            account=12345678,
            password="demo_password",
            server="Exness-MT5Trial9",
            symbols=["EURUSDm", "XAUUSDm", "BTCUSDm"],
        )

    Exness zero/raw account (no suffix):
        config = MT5Config(
            account=87654321,
            password="live_password",
            server="Exness-MT5Real8",
            symbols=["EURUSD", "XAUUSD", "BTCUSD"],
        )

    IC Markets:
        config = MT5Config(
            account=11223344,
            password="ic_password",
            server="ICMarketsSC-Demo",
            symbols=["EURUSD", "XAUUSD"],
        )
    """

    # ── Required ──────────────────────────────────────────────────────────────
    account: int
    password: str
    server: str
    symbols: list[str]

    # ── Optional / defaults ───────────────────────────────────────────────────
    poll_interval_ms: int         = DEFAULT_POLL_INTERVAL_MS
    exec_poll_interval_ms: int    = DEFAULT_EXEC_POLL_INTERVAL_MS
    magic_number: int             = MT5_MAGIC_NUMBER
    reconnect_initial_delay_s: float = RECONNECT_INITIAL_DELAY_S
    reconnect_max_delay_s: float  = RECONNECT_MAX_DELAY_S
    reconnect_max_attempts: int   = RECONNECT_MAX_ATTEMPTS
    timeout_s: float              = 10.0

    def __post_init__(self) -> None:
        if not self.account or self.account <= 0:
            raise ValueError("MT5Config.account must be a positive integer.")
        if not self.password:
            raise ValueError("MT5Config.password cannot be empty.")
        if not self.server:
            raise ValueError("MT5Config.server cannot be empty.")
        if not self.symbols:
            raise ValueError("MT5Config.symbols cannot be empty.")

        # Preserve exact broker symbol names — do NOT uppercase.
        # Brokers like Exness use lowercase suffixes (EURUSDm).
        # We only strip surrounding whitespace.
        self.symbols = [s.strip() for s in self.symbols]

        if self.poll_interval_ms < 10:
            raise ValueError("poll_interval_ms must be at least 10ms.")
        if self.exec_poll_interval_ms < 50:
            raise ValueError("exec_poll_interval_ms must be at least 50ms.")

    @property
    def poll_interval_s(self) -> float:
        return self.poll_interval_ms / 1000.0

    @property
    def exec_poll_interval_s(self) -> float:
        return self.exec_poll_interval_ms / 1000.0
