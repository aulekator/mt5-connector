"""
Custom exceptions for the nautilus-mt5 adapter.
Raised throughout connection.py, parsing.py, data.py, and execution.py.
"""


class MT5Error(Exception):
    """Base exception for all nautilus-mt5 errors."""


class MT5ConnectionError(MT5Error):
    """
    Raised when the adapter cannot establish or maintain
    a connection to the MT5 terminal.

    Common causes:
    - MT5 terminal is not running
    - MT5 terminal is starting up (try again in a few seconds)
    - mt5.initialize() failed
    """


class MT5LoginError(MT5Error):
    """
    Raised when login to the broker account fails.

    Common causes:
    - Wrong account number, password, or server name
    - Account is disabled or expired
    - Broker server is unreachable
    """


class MT5SymbolNotFoundError(MT5Error):
    """
    Raised when a requested symbol does not exist on the broker
    or has not been enabled in Market Watch.

    Common causes:
    - Typo in symbol name (e.g. 'EURUSD.' vs 'EURUSD')
    - Symbol not offered by this broker
    - Symbol not added to Market Watch (mt5.symbol_select() failed)
    """

    def __init__(self, symbol: str):
        super().__init__(
            f"Symbol '{symbol}' not found. "
            "Check it exists in MT5 Market Watch and the name matches exactly."
        )
        self.symbol = symbol


class MT5OrderError(MT5Error):
    """
    Raised when an order submission, modification, or cancellation fails.

    Includes the MT5 retcode so the caller can inspect the exact reason.
    Full retcode list: https://www.mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes
    """

    def __init__(self, message: str, retcode: int | None = None):
        full_message = message
        if retcode is not None:
            full_message = f"{message} (MT5 retcode: {retcode})"
        super().__init__(full_message)
        self.retcode = retcode


class MT5InstrumentError(MT5Error):
    """
    Raised when an MT5 symbol cannot be converted into a
    NautilusTrader instrument definition.

    Common causes:
    - Missing or null fields in symbol_info()
    - Unsupported instrument type
    """