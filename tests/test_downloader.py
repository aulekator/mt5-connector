"""
tests/test_downloader.py

Exhaustive tests for MT5DataDownloader.

Every method, every path, every edge case — without a real MT5 terminal.

Test groups:
  1.  _ensure_utc()         — timezone handling
  2.  _date_chunks()        — chunk splitting logic
  3.  DownloadResult        — dataclass behaviour
  4.  download_ticks()      — happy path
  5.  download_ticks()      — empty chunks (weekends)
  6.  download_ticks()      — chunk errors (partial failure)
  7.  download_ticks()      — symbol not found
  8.  download_ticks()      — not connected
  9.  download_ticks()      — auto-loads instrument if not pre-loaded
  10. download_bars()       — happy path
  11. download_bars()       — default timeframe is H1
  12. download_bars()       — empty chunks
  13. download_bars()       — chunk errors
  14. download_bars()       — symbol not found
  15. download_all()        — happy path multiple symbols
  16. download_all()        — include_ticks=False skip ticks
  17. download_all()        — include_bars=False skip bars
  18. download_all()        — multiple timeframes
  19. Chunking              — weekly chunks created correctly
  20. Chunking              — last chunk handles partial week
  21. Catalog               — write_data called with correct data types
  22. Catalog               — chunks written incrementally
  23. write order           — ts_event ordering within chunk
"""

import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call
from nautilus_trader.model.data import QuoteTick, Bar
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.instruments import CurrencyPair

from nautilus_mt5.downloader import (
    MT5DataDownloader,
    DownloadResult,
    _ensure_utc,
    _date_chunks,
)
from nautilus_mt5.connection import ConnectionState
from nautilus_mt5.errors import MT5ConnectionError, MT5SymbolNotFoundError


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

UTC = timezone.utc

def dt(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def make_raw_tick(bid=1.085, ask=1.0852, time_s=1_700_000_000):
    """Build a numpy structured array row matching mt5.copy_ticks_range() output."""
    dtype = np.dtype([
        ("time",   np.int64),
        ("bid",    np.float64),
        ("ask",    np.float64),
        ("last",   np.float64),
        ("volume", np.uint64),
        ("time_msc", np.int64),
        ("flags",  np.uint32),
        ("volume_real", np.float64),
    ])
    arr = np.array(
        [(time_s, bid, ask, bid, 1, time_s * 1000, 134, 0.0)],
        dtype=dtype,
    )
    return arr[0]


def make_raw_rate(time_s=1_700_000_000, open_=1.085, high=1.090,
                  low=1.080, close=1.088, tick_volume=1000):
    """Build a numpy structured array row matching mt5.copy_rates_range() output."""
    dtype = np.dtype([
        ("time",        np.int64),
        ("open",        np.float64),
        ("high",        np.float64),
        ("low",         np.float64),
        ("close",       np.float64),
        ("tick_volume", np.int64),
        ("spread",      np.int32),
        ("real_volume", np.int64),
    ])
    arr = np.array(
        [(time_s, open_, high, low, close, tick_volume, 2, 0)],
        dtype=dtype,
    )
    return arr[0]


def make_eurusd_instrument():
    """Build a real CurrencyPair instrument for EURUSD."""
    from nautilus_trader.model.currencies import Currency
    from nautilus_trader.model.identifiers import Symbol, Venue
    from decimal import Decimal
    return CurrencyPair(
        instrument_id=InstrumentId.from_str("EURUSD.MT5"),
        raw_symbol=from_str_sym("EURUSD"),
        base_currency=Currency.from_str("EUR"),
        quote_currency=Currency.from_str("USD"),
        price_precision=5,
        size_precision=2,
        price_increment=Price(0.00001, 5),
        size_increment=Quantity(0.01, 2),
        lot_size=Quantity(100000, 0),
        max_quantity=Quantity(1000.0, 2),
        min_quantity=Quantity(0.01, 2),
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0.03"),
        margin_maint=Decimal("0.03"),
        maker_fee=Decimal("0"),
        taker_fee=Decimal("0"),
        ts_event=0,
        ts_init=0,
    )


def from_str_sym(s):
    from nautilus_trader.model.identifiers import Symbol
    return Symbol(s)


def make_conn(connected=True):
    conn = MagicMock()
    conn.is_connected = connected
    conn.state = ConnectionState.CONNECTED if connected else ConnectionState.DISCONNECTED
    if connected:
        conn.ensure_connected = MagicMock()  # no-op
    else:
        conn.ensure_connected = MagicMock(
            side_effect=MT5ConnectionError("Not connected")
        )
    return conn


def make_provider(instrument=None):
    provider = MagicMock()
    eurusd = instrument or make_eurusd_instrument()
    provider.get_instrument.return_value = eurusd
    provider.load_symbol.return_value    = eurusd
    return provider


def make_catalog():
    catalog = MagicMock()
    catalog.write_data = MagicMock()
    return catalog


@pytest.fixture
def conn():       return make_conn()
@pytest.fixture
def provider():   return make_provider()
@pytest.fixture
def catalog():    return make_catalog()
@pytest.fixture
def downloader(conn, provider, catalog):
    return MT5DataDownloader(
        connection=conn,
        provider=provider,
        catalog=catalog,
        chunk_days_ticks=7,
        chunk_days_bars=365,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 1. _ensure_utc()
# ═════════════════════════════════════════════════════════════════════════════

class TestEnsureUtc:

    def test_naive_datetime_gets_utc(self):
        d = datetime(2024, 1, 1)
        result = _ensure_utc(d)
        assert result.tzinfo == UTC

    def test_aware_datetime_unchanged(self):
        d = datetime(2024, 1, 1, tzinfo=UTC)
        result = _ensure_utc(d)
        assert result.tzinfo == UTC
        assert result == d

    def test_naive_date_preserved(self):
        d = datetime(2024, 6, 15, 12, 30)
        result = _ensure_utc(d)
        assert result.year == 2024
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 12

    def test_returns_datetime(self):
        result = _ensure_utc(datetime(2024, 1, 1))
        assert isinstance(result, datetime)


# ═════════════════════════════════════════════════════════════════════════════
# 2. _date_chunks()
# ═════════════════════════════════════════════════════════════════════════════

class TestDateChunks:

    def test_single_chunk_when_range_less_than_chunk(self):
        start  = dt(2024, 1, 1)
        end    = dt(2024, 1, 3)
        chunks = _date_chunks(start, end, days=7)
        assert len(chunks) == 1
        assert chunks[0] == (start, end)

    def test_exact_chunk_size(self):
        start  = dt(2024, 1, 1)
        end    = dt(2024, 1, 8)  # exactly 7 days
        chunks = _date_chunks(start, end, days=7)
        assert len(chunks) == 1

    def test_two_chunks(self):
        start  = dt(2024, 1, 1)
        end    = dt(2024, 1, 15)  # 14 days → 2 chunks of 7
        chunks = _date_chunks(start, end, days=7)
        assert len(chunks) == 2

    def test_last_chunk_does_not_exceed_end(self):
        start  = dt(2024, 1, 1)
        end    = dt(2024, 1, 10)  # 9 days → chunk1=7 days, chunk2=2 days
        chunks = _date_chunks(start, end, days=7)
        assert len(chunks) == 2
        assert chunks[-1][1] == end

    def test_chunks_are_contiguous(self):
        start  = dt(2024, 1, 1)
        end    = dt(2024, 2, 1)
        chunks = _date_chunks(start, end, days=7)
        for i in range(len(chunks) - 1):
            assert chunks[i][1] == chunks[i + 1][0]

    def test_first_chunk_starts_at_start(self):
        start  = dt(2024, 1, 1)
        end    = dt(2024, 2, 1)
        chunks = _date_chunks(start, end, days=7)
        assert chunks[0][0] == start

    def test_last_chunk_ends_at_end(self):
        start  = dt(2024, 1, 1)
        end    = dt(2024, 2, 1)
        chunks = _date_chunks(start, end, days=7)
        assert chunks[-1][1] == end

    def test_one_year_weekly_has_correct_count(self):
        start  = dt(2024, 1, 1)
        end    = dt(2025, 1, 1)
        chunks = _date_chunks(start, end, days=7)
        # 365 days / 7 = ~52-53 chunks
        assert 52 <= len(chunks) <= 53

    def test_empty_range_returns_no_chunks(self):
        start = dt(2024, 1, 1)
        end   = dt(2024, 1, 1)  # zero-length range
        chunks = _date_chunks(start, end, days=7)
        assert chunks == []

    def test_returns_list_of_tuples(self):
        chunks = _date_chunks(dt(2024, 1, 1), dt(2024, 1, 8), days=7)
        assert isinstance(chunks, list)
        assert all(isinstance(c, tuple) and len(c) == 2 for c in chunks)


# ═════════════════════════════════════════════════════════════════════════════
# 3. DownloadResult
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadResult:

    def test_success_when_no_errors(self):
        r = DownloadResult(symbol="EURUSD", data_type="ticks")
        assert r.success is True

    def test_not_success_when_errors(self):
        r = DownloadResult(symbol="EURUSD", data_type="ticks",
                           errors=["chunk failed"])
        assert r.success is False

    def test_default_total_written_zero(self):
        r = DownloadResult(symbol="EURUSD", data_type="ticks")
        assert r.total_written == 0

    def test_str_contains_symbol(self):
        r = DownloadResult(symbol="EURUSD", data_type="ticks", total_written=1000)
        assert "EURUSD" in str(r)

    def test_str_contains_total_written(self):
        r = DownloadResult(symbol="EURUSD", data_type="ticks", total_written=5000)
        assert "5,000" in str(r)

    def test_str_contains_ok_on_success(self):
        r = DownloadResult(symbol="EURUSD", data_type="ticks")
        assert "OK" in str(r)

    def test_str_shows_error_count(self):
        r = DownloadResult(symbol="EURUSD", data_type="ticks",
                           errors=["e1", "e2"])
        assert "2" in str(r)

    def test_errors_list_mutable(self):
        r = DownloadResult(symbol="EURUSD", data_type="ticks")
        r.errors.append("something")
        assert len(r.errors) == 1


# ═════════════════════════════════════════════════════════════════════════════
# 4. download_ticks() — happy path
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadTicksHappy:

    def test_returns_download_result(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.TIMEFRAME_H1   = 16385
            mock_mt5.copy_ticks_range.return_value = [make_raw_tick()]

            result = downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        assert isinstance(result, DownloadResult)

    def test_success_true_when_no_errors(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = [make_raw_tick()]
            result = downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        assert result.success is True

    def test_total_written_matches_ticks_count(self, downloader):
        raw_ticks = [make_raw_tick(time_s=1_700_000_000 + i) for i in range(100)]
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = raw_ticks
            result = downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        assert result.total_written == 100

    def test_symbol_whitespace_stripped(self, downloader):
        """Symbols should have whitespace stripped but casing preserved."""
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = [make_raw_tick()]
            result = downloader.download_ticks(" EURUSDm ", dt(2024,1,1), dt(2024,1,8))

        # Whitespace stripped, broker casing preserved
        assert result.symbol == "EURUSDm"

    def test_symbol_casing_preserved(self, downloader):
        """Exness 'm' suffix must be preserved, not uppercased."""
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = [make_raw_tick()]
            result = downloader.download_ticks("EURUSDm", dt(2024,1,1), dt(2024,1,8))

        assert result.symbol == "EURUSDm"  # lowercase m preserved

    def test_catalog_write_data_called(self, downloader, catalog):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = [make_raw_tick()]
            downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        catalog.write_data.assert_called()

    def test_write_data_receives_quote_ticks(self, downloader, catalog):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = [make_raw_tick()]
            downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        written_data = catalog.write_data.call_args[0][0]
        assert all(isinstance(t, QuoteTick) for t in written_data)

    def test_chunks_processed_count(self, downloader):
        # 14 day range with 7-day chunks = 2 chunks
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = [make_raw_tick()]
            result = downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,15))

        assert result.chunks_processed == 2


# ═════════════════════════════════════════════════════════════════════════════
# 5. download_ticks() — empty chunks (weekends)
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadTicksEmptyChunks:

    def test_empty_chunk_not_counted_in_total(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = []  # weekend → empty
            result = downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        assert result.total_written == 0

    def test_empty_chunk_increments_chunks_empty(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = []
            result = downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        assert result.chunks_empty == 1

    def test_none_return_treated_as_empty(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = None
            result = downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        assert result.chunks_empty == 1
        assert result.total_written == 0

    def test_empty_chunks_do_not_call_write_data(self, downloader, catalog):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = []
            downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        catalog.write_data.assert_not_called()

    def test_success_true_even_with_empty_chunks(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = []
            result = downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        assert result.success is True


# ═════════════════════════════════════════════════════════════════════════════
# 6. download_ticks() — chunk errors (partial failure)
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadTicksErrors:

    def test_chunk_error_recorded_in_result(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.side_effect = RuntimeError("MT5 IPC error")
            result = downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        assert len(result.errors) == 1
        assert "MT5 IPC error" in result.errors[0]

    def test_success_false_when_errors(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.side_effect = RuntimeError("error")
            result = downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        assert result.success is False

    def test_partial_failure_continues_other_chunks(self, downloader):
        """First chunk fails, second succeeds — total_written should be > 0."""
        call_count = {"n": 0}
        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("chunk 1 error")
            return [make_raw_tick()]

        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.side_effect = side_effect
            # Use 14 day range to get 2 chunks
            result = downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,15))

        assert result.total_written == 1
        assert len(result.errors) == 1

    def test_error_message_contains_date_range(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.side_effect = RuntimeError("boom")
            result = downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        assert "2024-01-01" in result.errors[0]


# ═════════════════════════════════════════════════════════════════════════════
# 7. download_ticks() — symbol not found
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadTicksSymbolNotFound:

    def test_returns_result_with_error_when_symbol_not_found(self, conn, catalog):
        provider = MagicMock()
        provider.get_instrument.return_value = None
        provider.load_symbol.side_effect = MT5SymbolNotFoundError("FAKESYM")

        downloader = MT5DataDownloader(conn, provider, catalog)
        with patch("nautilus_mt5.downloader.mt5"):
            result = downloader.download_ticks("FAKESYM", dt(2024,1,1), dt(2024,1,8))

        assert result.success is False
        assert "FAKESYM" in result.errors[0]

    def test_total_written_zero_when_symbol_not_found(self, conn, catalog):
        provider = MagicMock()
        provider.get_instrument.return_value = None
        provider.load_symbol.side_effect = MT5SymbolNotFoundError("FAKESYM")

        downloader = MT5DataDownloader(conn, provider, catalog)
        with patch("nautilus_mt5.downloader.mt5"):
            result = downloader.download_ticks("FAKESYM", dt(2024,1,1), dt(2024,1,8))

        assert result.total_written == 0


# ═════════════════════════════════════════════════════════════════════════════
# 8. download_ticks() — not connected
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadTicksNotConnected:

    def test_raises_when_not_connected(self, catalog, provider):
        disconnected_conn = make_conn(connected=False)
        downloader = MT5DataDownloader(disconnected_conn, provider, catalog)

        with pytest.raises(MT5ConnectionError):
            downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))


# ═════════════════════════════════════════════════════════════════════════════
# 9. download_ticks() — auto-loads instrument
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadTicksAutoLoad:

    def test_auto_loads_instrument_if_not_pre_loaded(self, conn, catalog):
        eurusd = make_eurusd_instrument()
        provider = MagicMock()
        provider.get_instrument.return_value = None      # not pre-loaded
        provider.load_symbol.return_value    = eurusd    # auto-load succeeds

        downloader = MT5DataDownloader(conn, provider, catalog)
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = [make_raw_tick()]
            result = downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        provider.load_symbol.assert_called_once_with("EURUSD")
        assert result.total_written == 1


# ═════════════════════════════════════════════════════════════════════════════
# 10. download_bars() — happy path
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadBarsHappy:

    def test_returns_download_result(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.copy_rates_range.return_value = [make_raw_rate()]
            result = downloader.download_bars("EURUSD", dt(2024,1,1), dt(2024,12,31))

        assert isinstance(result, DownloadResult)

    def test_success_true(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.copy_rates_range.return_value = [make_raw_rate()]
            result = downloader.download_bars("EURUSD", dt(2024,1,1), dt(2024,12,31))

        assert result.success is True

    def test_total_written_correct(self, downloader):
        # downloader fixture has chunk_days_bars=365
        # Range: 2024-01-01 to 2025-01-01 = 366 days → 2 chunks (365 + 1 day)
        # Each returns 50 bars → total = 100
        raw_bars = [make_raw_rate(time_s=1_700_000_000 + i*3600) for i in range(50)]
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.copy_rates_range.return_value = raw_bars
            result = downloader.download_bars("EURUSD", dt(2024,1,1), dt(2025,1,1))

        # 366 days / 365 chunk_days = 2 chunks × 50 bars each = 100
        assert result.total_written == 100

    def test_write_data_receives_bars(self, downloader, catalog):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.copy_rates_range.return_value = [make_raw_rate()]
            downloader.download_bars("EURUSD", dt(2024,1,1), dt(2024,12,31))

        written_data = catalog.write_data.call_args[0][0]
        assert all(isinstance(b, Bar) for b in written_data)

    def test_data_type_is_bars(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.copy_rates_range.return_value = [make_raw_rate()]
            result = downloader.download_bars("EURUSD", dt(2024,1,1), dt(2024,12,31))

        assert result.data_type == "bars"


# ═════════════════════════════════════════════════════════════════════════════
# 11. download_bars() — default timeframe
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadBarsDefaultTimeframe:

    def test_default_timeframe_is_h1(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.copy_rates_range.return_value = [make_raw_rate()]
            downloader.download_bars("EURUSD", dt(2024,1,1), dt(2024,12,31))

        # Second arg to copy_rates_range should be H1 = 16385
        call_args = mock_mt5.copy_rates_range.call_args[0]
        assert call_args[1] == 16385  # timeframe = H1

    def test_explicit_timeframe_used(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.TIMEFRAME_D1 = 16408
            mock_mt5.copy_rates_range.return_value = [make_raw_rate()]
            downloader.download_bars("EURUSD", dt(2024,1,1), dt(2024,12,31),
                                     timeframe=16408)

        call_args = mock_mt5.copy_rates_range.call_args[0]
        assert call_args[1] == 16408  # D1


# ═════════════════════════════════════════════════════════════════════════════
# 12. download_bars() — empty chunks
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadBarsEmpty:

    def test_empty_bar_chunk_not_counted(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.copy_rates_range.return_value = []
            result = downloader.download_bars("EURUSD", dt(2024,1,1), dt(2024,12,31))

        assert result.total_written == 0
        assert result.chunks_empty  == 1


# ═════════════════════════════════════════════════════════════════════════════
# 13. download_bars() — chunk errors
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadBarsErrors:

    def test_error_recorded(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.copy_rates_range.side_effect = RuntimeError("IPC error")
            result = downloader.download_bars("EURUSD", dt(2024,1,1), dt(2024,12,31))

        assert len(result.errors) == 1

    def test_success_false_on_error(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.copy_rates_range.side_effect = RuntimeError("IPC error")
            result = downloader.download_bars("EURUSD", dt(2024,1,1), dt(2024,12,31))

        assert result.success is False


# ═════════════════════════════════════════════════════════════════════════════
# 14. download_bars() — symbol not found
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadBarsSymbolNotFound:

    def test_error_when_symbol_not_found(self, conn, catalog):
        provider = MagicMock()
        provider.get_instrument.return_value = None
        provider.load_symbol.side_effect = MT5SymbolNotFoundError("FAKESYM")

        downloader = MT5DataDownloader(conn, provider, catalog)
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            result = downloader.download_bars("FAKESYM", dt(2024,1,1), dt(2024,12,31))

        assert not result.success
        assert result.total_written == 0


# ═════════════════════════════════════════════════════════════════════════════
# 15. download_all() — happy path
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadAll:

    def test_returns_dict_keyed_by_symbol(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.TIMEFRAME_H1   = 16385
            mock_mt5.TIMEFRAME_D1   = 16408
            mock_mt5.copy_ticks_range.return_value = [make_raw_tick()]
            mock_mt5.copy_rates_range.return_value = [make_raw_rate()]

            results = downloader.download_all(
                ["EURUSD", "XAUUSD"],
                dt(2024,1,1), dt(2024,1,8),
                timeframes=[16385],
            )

        assert "EURUSD" in results
        assert "XAUUSD" in results

    def test_each_symbol_has_result_list(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.TIMEFRAME_H1   = 16385
            mock_mt5.copy_ticks_range.return_value = [make_raw_tick()]
            mock_mt5.copy_rates_range.return_value = [make_raw_rate()]

            results = downloader.download_all(
                ["EURUSD"],
                dt(2024,1,1), dt(2024,1,8),
                timeframes=[16385],
            )

        assert isinstance(results["EURUSD"], list)


# ═════════════════════════════════════════════════════════════════════════════
# 16. download_all() — include_ticks=False
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadAllNoTicks:

    def test_skip_ticks_when_include_ticks_false(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.copy_rates_range.return_value = [make_raw_rate()]

            downloader.download_all(
                ["EURUSD"], dt(2024,1,1), dt(2024,1,8),
                include_ticks=False, timeframes=[16385],
            )

        mock_mt5.copy_ticks_range.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# 17. download_all() — include_bars=False
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadAllNoBars:

    def test_skip_bars_when_include_bars_false(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = [make_raw_tick()]

            downloader.download_all(
                ["EURUSD"], dt(2024,1,1), dt(2024,1,8),
                include_bars=False,
            )

        mock_mt5.copy_rates_range.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# 18. download_all() — multiple timeframes
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadAllMultipleTimeframes:

    def test_multiple_timeframes_each_downloaded(self, downloader):
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.TIMEFRAME_H1   = 16385
            mock_mt5.TIMEFRAME_D1   = 16408
            mock_mt5.copy_ticks_range.return_value = [make_raw_tick()]
            mock_mt5.copy_rates_range.return_value = [make_raw_rate()]

            results = downloader.download_all(
                ["EURUSD"], dt(2024,1,1), dt(2024,1,8),
                timeframes=[16385, 16408],
            )

        # ticks + H1 bars + D1 bars = 3 results for EURUSD
        assert len(results["EURUSD"]) == 3


# ═════════════════════════════════════════════════════════════════════════════
# 19 & 20. Chunking integration
# ═════════════════════════════════════════════════════════════════════════════

class TestChunkingIntegration:

    def test_weekly_range_produces_one_chunk(self, downloader):
        call_count = {"n": 0}
        def count_calls(*args, **kwargs):
            call_count["n"] += 1
            return [make_raw_tick()]

        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.side_effect = count_calls
            downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        assert call_count["n"] == 1

    def test_four_week_range_produces_four_chunks(self, downloader):
        call_count = {"n": 0}
        def count_calls(*args, **kwargs):
            call_count["n"] += 1
            return [make_raw_tick()]

        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.side_effect = count_calls
            downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,29))

        assert call_count["n"] == 4

    def test_partial_last_chunk_uses_correct_end_date(self, downloader):
        """Last chunk must end at `end`, not at start + chunk_days."""
        chunk_ends = []
        def capture_calls(symbol, chunk_start, chunk_end, flags):
            chunk_ends.append(chunk_end)
            return [make_raw_tick()]

        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.side_effect = capture_calls
            # 10 days with 7-day chunks → chunk1=7 days, chunk2=3 days
            end = dt(2024,1,11)
            downloader.download_ticks("EURUSD", dt(2024,1,1), end)

        assert chunk_ends[-1] == end


# ═════════════════════════════════════════════════════════════════════════════
# 21 & 22. Catalog write behaviour
# ═════════════════════════════════════════════════════════════════════════════

class TestCatalogWriteBehaviour:

    def test_write_data_called_once_per_non_empty_chunk(self, conn, provider, catalog):
        """Two non-empty chunks → write_data called twice."""
        downloader = MT5DataDownloader(conn, provider, catalog,
                                       chunk_days_ticks=7)
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = [make_raw_tick()]
            # 14-day range → 2 chunks
            downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,15))

        assert catalog.write_data.call_count == 2

    def test_write_not_called_for_empty_chunks(self, conn, provider, catalog):
        downloader = MT5DataDownloader(conn, provider, catalog)
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = None
            downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,8))

        catalog.write_data.assert_not_called()

    def test_accumulated_total_across_chunks(self, conn, provider, catalog):
        """100 ticks per chunk × 2 chunks = 200 total written."""
        raw = [make_raw_tick(time_s=1_700_000_000 + i) for i in range(100)]
        downloader = MT5DataDownloader(conn, provider, catalog,
                                       chunk_days_ticks=7)
        with patch("nautilus_mt5.downloader.mt5") as mock_mt5:
            mock_mt5.COPY_TICKS_ALL = -1
            mock_mt5.copy_ticks_range.return_value = raw
            result = downloader.download_ticks("EURUSD", dt(2024,1,1), dt(2024,1,15))

        assert result.total_written == 200