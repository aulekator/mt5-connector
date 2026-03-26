"""
nautilus_mt5/downloader.py

Downloads historical tick and bar data from MT5 and writes it
into a NautilusTrader Parquet data catalog.

Run this ONCE (or periodically) before backtesting.
After it completes, backtesting runs fully offline — no MT5 needed.

Two main classes:
  MT5DataDownloader   — orchestrates the full download for one or more symbols
  DownloadResult      — summary of what was downloaded (counts, errors)

Usage
-----
    from datetime import datetime
    from mt5connect.downloader import MT5DataDownloader
    from mt5connect.config import MT5Config
    from mt5connect.connection import MT5Connection
    from mt5connect.providers import MT5InstrumentProvider
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    config = MT5Config(
        account=12345678,
        password="demo_password",
        server="Exness-MT5Trial1",
        symbols=["EURUSD", "XAUUSD"],
    )
    conn = MT5Connection(config)
    conn.connect()

    provider = MT5InstrumentProvider(conn)
    catalog  = ParquetDataCatalog("./catalog")

    downloader = MT5DataDownloader(
        connection=conn,
        provider=provider,
        catalog=catalog,
    )

    result = downloader.download_ticks(
        symbol="EURUSD",
        start=datetime(2024, 1, 1),
        end=datetime(2024, 12, 31),
    )
    print(result)

    conn.disconnect()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import MetaTrader5 as mt5
import numpy as np

from nautilus_trader.model.data import Bar, QuoteTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from mt5connect.errors import MT5ConnectionError, MT5SymbolNotFoundError
from mt5connect.parsing import parse_bar, parse_quote_tick

if TYPE_CHECKING:
    from mt5connect.connection import MT5Connection
    from mt5connect.providers import MT5InstrumentProvider

logger = logging.getLogger(__name__)

# MT5 hard limits per API call
_MAX_TICKS_PER_CALL = 2_000_000
_MAX_BARS_PER_CALL  = 100_000

# Chunk size for tick downloads (1 week per request to stay under MT5 limits)
_TICK_CHUNK_DAYS  = 7
_BAR_CHUNK_DAYS   = 365  # bars are much smaller — 1 year per request is fine


# ─────────────────────────────────────────────────────────────────────────────
# RESULT DATACLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DownloadResult:
    """
    Summary of a completed download operation.

    Attributes
    ----------
    symbol : str
    data_type : str          "ticks" or "bars"
    total_written : int      total data points written to catalog
    chunks_processed : int   number of time chunks fetched from MT5
    chunks_empty : int       chunks that returned no data (e.g. weekend)
    errors : list[str]       error messages for any failed chunks
    start : datetime
    end : datetime
    """
    symbol: str
    data_type: str
    total_written: int = 0
    chunks_processed: int = 0
    chunks_empty: int = 0
    errors: list[str] = field(default_factory=list)
    start: datetime | None = None
    end: datetime | None = None

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    def __str__(self) -> str:
        status = "OK" if self.success else f"{len(self.errors)} errors"
        return (
            f"DownloadResult({self.symbol} {self.data_type} | "
            f"{self.total_written:,} rows | "
            f"{self.chunks_processed} chunks | "
            f"status={status})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# DOWNLOADER
# ─────────────────────────────────────────────────────────────────────────────

class MT5DataDownloader:
    """
    Downloads historical data from MT5 and writes to a Parquet catalog.

    Parameters
    ----------
    connection : MT5Connection
        Active MT5 connection.
    provider : MT5InstrumentProvider
        Instrument provider (instruments must be loaded before downloading).
    catalog : ParquetDataCatalog
        The NautilusTrader catalog to write data into.
    chunk_days_ticks : int
        How many days per MT5 API call for tick data. Default: 7.
        Lower = safer (stays under MT5 limits), higher = fewer API calls.
    chunk_days_bars : int
        How many days per MT5 API call for bar data. Default: 365.
    """

    def __init__(
        self,
        connection: "MT5Connection",
        provider: "MT5InstrumentProvider",
        catalog: ParquetDataCatalog,
        chunk_days_ticks: int = _TICK_CHUNK_DAYS,
        chunk_days_bars: int  = _BAR_CHUNK_DAYS,
    ) -> None:
        self._conn     = connection
        self._provider = provider
        self._catalog  = catalog
        self._chunk_days_ticks = chunk_days_ticks
        self._chunk_days_bars  = chunk_days_bars

    # ── Public API ────────────────────────────────────────────────────────────

    def download_ticks(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> DownloadResult:
        """
        Download ALL tick data for a symbol between start and end.

        Splits the range into weekly chunks to respect MT5 API limits.
        Each chunk is fetched, converted to QuoteTick objects, and
        written to the catalog immediately (no RAM buildup).

        Parameters
        ----------
        symbol : str
            MT5 symbol name (e.g. "EURUSD").
        start : datetime
            Start of the download range (UTC).
        end : datetime
            End of the download range (UTC).

        Returns
        -------
        DownloadResult
        """
        symbol  = symbol.strip()   # preserve broker casing (EURUSDm, EURUSD, etc.)
        start   = _ensure_utc(start)
        end     = _ensure_utc(end)
        result  = DownloadResult(symbol=symbol, data_type="ticks", start=start, end=end)

        self._conn.ensure_connected()

        # Ensure instrument is loaded
        instrument = self._ensure_instrument(symbol, result)
        if instrument is None:
            return result

        logger.info(
            f"Downloader: downloading ticks for {symbol} "
            f"from {start.date()} to {end.date()}"
        )

        # Chunk by week
        chunks = list(_date_chunks(start, end, days=self._chunk_days_ticks))
        logger.info(f"Downloader: {len(chunks)} weekly chunks to fetch")

        for chunk_start, chunk_end in chunks:
            result.chunks_processed += 1
            try:
                raw = mt5.copy_ticks_range(
                    symbol,
                    chunk_start,
                    chunk_end,
                    mt5.COPY_TICKS_ALL,
                )

                if raw is None or len(raw) == 0:
                    result.chunks_empty += 1
                    logger.debug(
                        f"Downloader: {symbol} {chunk_start.date()} → empty "
                        f"(weekend or no data)"
                    )
                    continue

                ticks = [parse_quote_tick(row, instrument) for row in raw]
                self._catalog.write_data(ticks)
                result.total_written += len(ticks)

                logger.debug(
                    f"Downloader: {symbol} {chunk_start.date()} → "
                    f"{len(ticks):,} ticks written"
                )

            except Exception as exc:
                msg = (
                    f"Chunk {chunk_start.date()}–{chunk_end.date()} failed: {exc}"
                )
                logger.error(f"Downloader: {symbol} {msg}")
                result.errors.append(msg)

        logger.info(f"Downloader: {result}")
        return result

    def download_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: int | None = None,
    ) -> DownloadResult:
        """
        Download OHLCV bar data for a symbol between start and end.

        Parameters
        ----------
        symbol : str
            MT5 symbol name (e.g. "EURUSD").
        start : datetime
            Start of the download range (UTC).
        end : datetime
            End of the download range (UTC).
        timeframe : int, optional
            MT5 timeframe constant. Defaults to mt5.TIMEFRAME_H1.
            Common values:
                mt5.TIMEFRAME_M1  = 1      (1 minute)
                mt5.TIMEFRAME_M5  = 5      (5 minutes)
                mt5.TIMEFRAME_H1  = 16385  (1 hour)
                mt5.TIMEFRAME_H4  = 16388  (4 hours)
                mt5.TIMEFRAME_D1  = 16408  (daily)

        Returns
        -------
        DownloadResult
        """
        symbol    = symbol.strip()   # preserve broker casing (EURUSDm, EURUSD, etc.)
        start     = _ensure_utc(start)
        end       = _ensure_utc(end)
        timeframe = timeframe or mt5.TIMEFRAME_H1
        result    = DownloadResult(symbol=symbol, data_type="bars", start=start, end=end)

        self._conn.ensure_connected()

        instrument = self._ensure_instrument(symbol, result)
        if instrument is None:
            return result

        logger.info(
            f"Downloader: downloading bars TF={timeframe} for {symbol} "
            f"from {start.date()} to {end.date()}"
        )

        chunks = list(_date_chunks(start, end, days=self._chunk_days_bars))
        logger.info(f"Downloader: {len(chunks)} chunks to fetch")

        for chunk_start, chunk_end in chunks:
            result.chunks_processed += 1
            try:
                raw = mt5.copy_rates_range(
                    symbol,
                    timeframe,
                    chunk_start,
                    chunk_end,
                )

                if raw is None or len(raw) == 0:
                    result.chunks_empty += 1
                    logger.debug(
                        f"Downloader: {symbol} TF={timeframe} "
                        f"{chunk_start.date()} → empty"
                    )
                    continue

                bars = [parse_bar(row, instrument, timeframe) for row in raw]
                self._catalog.write_data(bars)
                result.total_written += len(bars)

                logger.debug(
                    f"Downloader: {symbol} {chunk_start.date()} → "
                    f"{len(bars):,} bars written"
                )

            except Exception as exc:
                msg = (
                    f"Chunk {chunk_start.date()}–{chunk_end.date()} "
                    f"TF={timeframe} failed: {exc}"
                )
                logger.error(f"Downloader: {symbol} {msg}")
                result.errors.append(msg)

        logger.info(f"Downloader: {result}")
        return result

    def download_all(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        include_ticks: bool = True,
        include_bars: bool  = True,
        timeframes: list[int] | None = None,
    ) -> dict[str, list[DownloadResult]]:
        """
        Download tick and/or bar data for multiple symbols.

        Parameters
        ----------
        symbols : list[str]
        start, end : datetime
        include_ticks : bool
            Whether to download tick data.
        include_bars : bool
            Whether to download bar data.
        timeframes : list[int], optional
            MT5 timeframe constants. Defaults to [H1, D1].

        Returns
        -------
        dict[str, list[DownloadResult]]
            Keyed by symbol, value is list of DownloadResult (one per data type).
        """
        timeframes = timeframes or [mt5.TIMEFRAME_H1, mt5.TIMEFRAME_D1]
        results: dict[str, list[DownloadResult]] = {}

        for symbol in symbols:
            symbol_results = []

            if include_ticks:
                r = self.download_ticks(symbol, start, end)
                symbol_results.append(r)

            if include_bars:
                for tf in timeframes:
                    r = self.download_bars(symbol, start, end, timeframe=tf)
                    symbol_results.append(r)

            results[symbol] = symbol_results
            logger.info(
                f"Downloader: {symbol} complete — "
                f"{sum(r.total_written for r in symbol_results):,} total rows"
            )

        return results

    # ── Private ───────────────────────────────────────────────────────────────

    def _ensure_instrument(self, symbol: str, result: DownloadResult):
        """
        Get the loaded instrument for a symbol.
        Tries to load it if not already loaded.
        Returns None and records error in result if it fails.
        """
        instrument = self._provider.get_instrument(symbol)
        if instrument is not None:
            return instrument

        # Try loading it now
        try:
            instrument = self._provider.load_symbol(symbol)
            return instrument
        except MT5SymbolNotFoundError:
            msg = f"Symbol '{symbol}' not found on broker — skipping"
            logger.error(f"Downloader: {msg}")
            result.errors.append(msg)
            return None
        except Exception as exc:
            msg = f"Failed to load instrument '{symbol}': {exc}"
            logger.error(f"Downloader: {msg}")
            result.errors.append(msg)
            return None


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_utc(dt: datetime) -> datetime:
    """Make a datetime timezone-aware (UTC) if it isn't already."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _date_chunks(
    start: datetime,
    end: datetime,
    days: int,
) -> list[tuple[datetime, datetime]]:
    """
    Split [start, end] into chunks of `days` days.
    The last chunk may be shorter than `days`.

    Returns list of (chunk_start, chunk_end) tuples.
    """
    chunks = []
    current = start
    delta   = timedelta(days=days)

    while current < end:
        chunk_end = min(current + delta, end)
        chunks.append((current, chunk_end))
        current = chunk_end

    return chunks
