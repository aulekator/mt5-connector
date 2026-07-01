"""
nautilus_mt5/providers.py

MT5InstrumentProvider — loads all available Exness instruments into
NautilusTrader's instrument cache using parsing.py for conversion.

This is called once on startup (inside _connect()) before any data
or execution clients begin working. NautilusTrader cannot process
ticks or orders until instruments are registered.

Two methods you must implement (NautilusTrader requires them):
  load_all_async()  — load every symbol available on the broker
  load_ids_async()  — load specific symbols by InstrumentId

Additionally exposes:
  load_symbol()     — load a single symbol by name (used internally)
  get_instrument()  — fetch a loaded instrument by symbol string
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import MetaTrader5 as mt5

from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.model.identifiers import InstrumentId, Symbol

from mt5connect.errors import MT5ConnectionError, MT5InstrumentError, MT5SymbolNotFoundError
from mt5connect.parsing import InstrumentAny, parse_symbol_info

if TYPE_CHECKING:
    from mt5connect.connection import MT5Connection

logger = logging.getLogger(__name__)


class MT5InstrumentProvider(InstrumentProvider):
    """
    Loads MT5 instrument definitions into NautilusTrader's cache.

    Parameters
    ----------
    connection : MT5Connection
        The active MT5 connection. Must be connected before any load call.

    Usage
    -----
        provider = MT5InstrumentProvider(connection=conn)

        # Load everything available on the broker
        await provider.load_all_async()

        # Load specific symbols only
        from nautilus_trader.model.identifiers import InstrumentId
        ids = [InstrumentId.from_str("EURUSD.MT5"), InstrumentId.from_str("XAUUSD.MT5")]
        await provider.load_ids_async(ids)

        # After loading, retrieve an instrument
        inst = provider.find(InstrumentId.from_str("EURUSD.MT5"))
        # or
        inst = provider.get_instrument("EURUSD")
    """

    def __init__(self, connection: "MT5Connection") -> None:
        super().__init__()
        self._conn = connection
        # Track symbols that failed to parse — logged but not fatal
        self._failed_symbols: list[tuple[str, str]] = []

    # ── Required NautilusTrader overrides ─────────────────────────────────────

    async def load_all_async(self, filters: dict | None = None) -> None:
        """
        Load ALL symbols available on the connected broker into the cache.

        Calls mt5.symbols_get() to get the full symbol list, then
        mt5.symbol_info() for each one, converts via parse_symbol_info(),
        and registers with self.add().

        Symbols that fail to parse are logged and skipped — they do not
        crash the whole load. Check self.failed_symbols after loading
        to see what was skipped and why.

        Parameters
        ----------
        filters : dict, optional
            Supports key "symbols" with a list of symbol name strings to
            restrict loading. All other keys are ignored.
            Example: {"symbols": ["EURUSD", "XAUUSD"]}
        """
        self._conn.ensure_connected()
        self._failed_symbols.clear()

        # Apply symbol filter if provided
        # Use `is not None` (not just truthy) so an empty list [] means "load nothing"
        symbol_filter: list[str] | None = None
        if filters is not None and "symbols" in filters:
            symbol_filter = [s.upper().strip() for s in filters["symbols"]]

        # Get all available symbols from broker
        all_symbols = mt5.symbols_get()
        if all_symbols is None:
            code, msg = mt5.last_error()
            raise MT5ConnectionError(
                f"mt5.symbols_get() returned None — error {code}: {msg}"
            )

        total    = len(all_symbols)
        loaded   = 0
        skipped  = 0
        filtered = 0

        logger.info(f"MT5InstrumentProvider: loading {total} symbols from broker")

        for sym_info in all_symbols:
            symbol = sym_info.name

            # Apply filter — None means no filter (load all), [] means load none
            if symbol_filter is not None and symbol not in symbol_filter:
                filtered += 1
                continue

            # Select symbol in Market Watch (required before symbol_info works reliably)
            mt5.symbol_select(symbol, True)

            # Get full symbol details
            info = mt5.symbol_info(symbol)
            if info is None:
                code, msg = mt5.last_error()
                logger.warning(
                    f"MT5InstrumentProvider: skipping '{symbol}' — "
                    f"symbol_info() returned None (error {code}: {msg})"
                )
                self._failed_symbols.append((symbol, f"symbol_info() None: {msg}"))
                skipped += 1
                continue

            # Parse into NautilusTrader instrument
            try:
                instrument = parse_symbol_info(info)
                self.add(instrument)
                loaded += 1
            except MT5InstrumentError as exc:
                logger.warning(
                    f"MT5InstrumentProvider: skipping '{symbol}' — parse error: {exc}"
                )
                self._failed_symbols.append((symbol, str(exc)))
                skipped += 1

        logger.info(
            f"MT5InstrumentProvider: loaded={loaded} "
            f"skipped={skipped} "
            f"filtered={filtered} "
            f"total={total}"
        )

        if self._failed_symbols:
            logger.warning(
                f"MT5InstrumentProvider: {len(self._failed_symbols)} symbols failed to parse. "
                "Check provider.failed_symbols for details."
            )

    async def load_ids_async(
        self,
        instrument_ids: list[InstrumentId],
        filters: dict | None = None,
    ) -> None:
        """
        Load specific instruments by InstrumentId into the cache.

        More efficient than load_all_async() when you only need a few symbols.
        Each InstrumentId must be in the format "SYMBOL.MT5" (e.g. "EURUSD.MT5").

        Parameters
        ----------
        instrument_ids : list[InstrumentId]
            The specific instruments to load.
        filters : dict, optional
            Ignored — included for interface compatibility.

        Raises
        ------
        MT5SymbolNotFoundError
            If a requested symbol does not exist on the broker.
        MT5InstrumentError
            If a symbol exists but fails to parse.
        """
        self._conn.ensure_connected()

        for instrument_id in instrument_ids:
            symbol = instrument_id.symbol.value
            await self._load_symbol_async(symbol)

    # ── Single symbol loader ──────────────────────────────────────────────────

    async def _load_symbol_async(self, symbol: str) -> InstrumentAny:
        """
        Load a single symbol by name. Internal async version.
        Raises MT5SymbolNotFoundError or MT5InstrumentError on failure.
        """
        return self.load_symbol(symbol)

    def load_symbol(self, symbol: str) -> InstrumentAny:
        """
        Load a single symbol by name (synchronous).

        Selects symbol in Market Watch, fetches symbol_info(),
        parses into NautilusTrader instrument, registers with self.add().

        Parameters
        ----------
        symbol : str
            The MT5 symbol name (e.g. "EURUSD", "XAUUSD").

        Returns
        -------
        InstrumentAny
            The parsed and registered instrument.

        Raises
        ------
        MT5ConnectionError
            If not connected.
        MT5SymbolNotFoundError
            If the symbol doesn't exist on this broker.
        MT5InstrumentError
            If the symbol exists but fails to parse.
        """
        self._conn.ensure_connected()

        symbol = symbol.strip()   # preserve broker casing (EURUSDm, EURUSD, etc.)

        # Select in Market Watch — required for some brokers
        selected = mt5.symbol_select(symbol, True)
        if not selected:
            raise MT5SymbolNotFoundError(symbol)

        # Fetch full symbol details
        info = mt5.symbol_info(symbol)
        if info is None:
            code, msg = mt5.last_error()
            raise MT5SymbolNotFoundError(symbol)

        # Parse and register
        instrument = parse_symbol_info(info)
        self.add(instrument)

        logger.debug(f"MT5InstrumentProvider: loaded '{symbol}' → {type(instrument).__name__}")
        return instrument

    # ── Convenience accessors ─────────────────────────────────────────────────

    def get_instrument(self, symbol: str) -> InstrumentAny | None:
        """
        Retrieve a loaded instrument by symbol name string.

        More convenient than find(InstrumentId.from_str("EURUSD.MT5")).
        Returns None if the symbol has not been loaded yet.

        Parameters
        ----------
        symbol : str
            Symbol name (e.g. "EURUSD", "XAUUSDm"). Exact broker casing
            is preserved — do NOT uppercase. Brokers like Exness use
            lowercase suffixes (e.g. "XAUUSDm"), and instruments are
            registered under that exact casing in load_symbol(), so
            uppercasing here would break the lookup for those symbols.
        """
        from mt5connect.constants import MT5_VENUE
        instrument_id = InstrumentId(
            Symbol(symbol.strip()),
            MT5_VENUE,
        )
        return self.find(instrument_id)

    @property
    def loaded_symbols(self) -> list[str]:
        """Return a list of all currently loaded symbol names."""
        return [inst.id.symbol.value for inst in self.list_all()]

    @property
    def failed_symbols(self) -> list[tuple[str, str]]:
        """
        Return list of (symbol, reason) tuples for symbols that failed to load.
        Populated after load_all_async() completes.
        """
        return list(self._failed_symbols)

    @property
    def count(self) -> int:
        """Number of instruments currently loaded."""
        return len(self.list_all())

    def __repr__(self) -> str:
        return (
            f"MT5InstrumentProvider("
            f"loaded={self.count}, "
            f"failed={len(self._failed_symbols)})"
        )