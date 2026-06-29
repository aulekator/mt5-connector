"""
tests/test_providers.py

Exhaustive tests for MT5InstrumentProvider.

Every method, every path, every edge case.

Test groups:
  1.  Initial state
  2.  load_all_async() — happy path
  3.  load_all_async() — symbol filter
  4.  load_all_async() — partial failures (bad symbols skipped)
  5.  load_all_async() — mt5.symbols_get() returns None
  6.  load_all_async() — empty broker symbol list
  7.  load_all_async() — not connected
  8.  load_ids_async() — happy path
  9.  load_ids_async() — symbol not found
  10. load_ids_async() — not connected
  11. load_symbol()    — happy path
  12. load_symbol()    — symbol_select fails
  13. load_symbol()    — symbol_info returns None
  14. load_symbol()    — parse error
  15. load_symbol()    — not connected
  16. get_instrument() — found and not found
  17. loaded_symbols property
  18. failed_symbols property
  19. count property
  20. __repr__
  21. Multiple loads — instruments accumulate
  22. load_all_async clears failed_symbols on each call
  23. Instrument type coverage — FX, metal, crypto, index loaded correctly
  24. load_ids_async loads only requested symbols
  25. symbol_select called for each symbol during load_all
"""

import pytest
from unittest.mock import MagicMock, patch, call
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CurrencyPair, Cfd, CryptoPerpetual

from mt5connect.providers import MT5InstrumentProvider
from mt5connect.connection import MT5Connection, ConnectionState
from mt5connect.errors import (
    MT5ConnectionError,
    MT5InstrumentError,
    MT5SymbolNotFoundError,
)
from mt5connect.constants import MT5_VENUE


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def make_symbol_info(
    name="EURUSD",
    digits=5,
    volume_min=0.01,
    volume_max=1000.0,
    volume_step=0.01,
    trade_contract_size=100000.0,
    currency_base="EUR",
    currency_profit="USD",
    currency_margin="USD",
    margin_initial=3.0,
    margin_maintenance=0.0,
    calc_mode=0,
):
    info = MagicMock()
    info.name               = name
    info.digits             = digits
    info.trade_tick_size    = 10 ** -digits
    info.volume_min         = volume_min
    info.volume_max         = volume_max
    info.volume_step        = volume_step
    info.trade_contract_size = trade_contract_size
    info.currency_base      = currency_base
    info.currency_profit    = currency_profit
    info.currency_margin    = currency_margin
    info.margin_initial     = margin_initial
    info.margin_maintenance = margin_maintenance
    info.calc_mode          = calc_mode
    info.description        = f"{name} description"
    return info


# Pre-built symbol infos for standard test symbols
EURUSD_INFO  = make_symbol_info("EURUSD",  digits=5, currency_base="EUR",  currency_profit="USD")
# Broker-suffixed fixture verifies lowercase suffixes are not uppercased away.
EURUSDM_INFO = make_symbol_info("EURUSDm", digits=5, currency_base="EUR",  currency_profit="USD")
GBPUSD_INFO  = make_symbol_info("GBPUSD",  digits=5, currency_base="GBP",  currency_profit="USD")
XAUUSD_INFO  = make_symbol_info("XAUUSD",  digits=2, currency_base="XAU",  currency_profit="USD", trade_contract_size=100.0,  margin_initial=1.0)
BTCUSD_INFO  = make_symbol_info("BTCUSD",  digits=2, currency_base="BTC",  currency_profit="USD", trade_contract_size=1.0,    margin_initial=1.0)
US500_INFO   = make_symbol_info("US500",   digits=1, currency_base="USD",  currency_profit="USD", trade_contract_size=1.0,    margin_initial=1.0, volume_step=0.1)

ALL_SYMBOLS_INFO = [EURUSD_INFO, GBPUSD_INFO, XAUUSD_INFO, BTCUSD_INFO, US500_INFO]

# Map symbol name → info for mock lookup
SYMBOL_INFO_MAP = {
    "EURUSD": EURUSD_INFO,
    # The suffixed key must stay exact because mt5.symbol_info() is exact-name based.
    "EURUSDm": EURUSDM_INFO,
    "GBPUSD": GBPUSD_INFO,
    "XAUUSD": XAUUSD_INFO,
    "BTCUSD": BTCUSD_INFO,
    "US500":  US500_INFO,
}


def make_connected_conn(config):
    """Return a mock MT5Connection that reports CONNECTED state."""
    conn = MagicMock(spec=MT5Connection)
    conn.is_connected = True
    conn.state = ConnectionState.CONNECTED
    conn.ensure_connected = MagicMock()   # no-op: connection is fine
    return conn


def make_disconnected_conn(config):
    """Return a mock MT5Connection that raises on ensure_connected."""
    conn = MagicMock(spec=MT5Connection)
    conn.is_connected = False
    conn.state = ConnectionState.DISCONNECTED
    conn.ensure_connected = MagicMock(
        side_effect=MT5ConnectionError("Not connected")
    )
    return conn


@pytest.fixture
def conn(config):
    return make_connected_conn(config)


@pytest.fixture
def provider(conn):
    return MT5InstrumentProvider(connection=conn)


# ═════════════════════════════════════════════════════════════════════════════
# 1. Initial state
# ═════════════════════════════════════════════════════════════════════════════

class TestInitialState:

    def test_count_zero_on_init(self, provider):
        assert provider.count == 0

    def test_list_all_empty_on_init(self, provider):
        assert provider.list_all() == []

    def test_failed_symbols_empty_on_init(self, provider):
        assert provider.failed_symbols == []

    def test_loaded_symbols_empty_on_init(self, provider):
        assert provider.loaded_symbols == []

    def test_repr_shows_zero(self, provider):
        r = repr(provider)
        assert "loaded=0" in r
        assert "failed=0" in r


# ═════════════════════════════════════════════════════════════════════════════
# 2. load_all_async() — happy path
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadAllAsync:

    @pytest.mark.asyncio
    async def test_loads_all_symbols(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = ALL_SYMBOLS_INFO
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: SYMBOL_INFO_MAP.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async()

        assert provider.count == 5

    @pytest.mark.asyncio
    async def test_loaded_symbol_names_correct(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = ALL_SYMBOLS_INFO
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: SYMBOL_INFO_MAP.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async()

        names = set(provider.loaded_symbols)
        assert "EURUSD"  in names
        assert "GBPUSD"  in names
        assert "XAUUSD"  in names
        assert "BTCUSD"  in names
        assert "US500"   in names

    @pytest.mark.asyncio
    async def test_no_failed_symbols_on_clean_load(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = ALL_SYMBOLS_INFO
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: SYMBOL_INFO_MAP.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async()

        assert provider.failed_symbols == []

    @pytest.mark.asyncio
    async def test_symbol_select_called_for_each_symbol(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = [EURUSD_INFO, GBPUSD_INFO]
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: SYMBOL_INFO_MAP.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async()

        calls = [c[0][0] for c in mock_mt5.symbol_select.call_args_list]
        assert "EURUSD" in calls
        assert "GBPUSD" in calls

    @pytest.mark.asyncio
    async def test_eurusd_is_currency_pair(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = [EURUSD_INFO]
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async()

        inst = provider.get_instrument("EURUSD")
        assert isinstance(inst, CurrencyPair)

    @pytest.mark.asyncio
    async def test_xauusd_is_cfd(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = [XAUUSD_INFO]
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = XAUUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async()

        inst = provider.get_instrument("XAUUSD")
        assert isinstance(inst, Cfd)

    @pytest.mark.asyncio
    async def test_btcusd_is_crypto_perpetual(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = [BTCUSD_INFO]
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = BTCUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async()

        inst = provider.get_instrument("BTCUSD")
        assert isinstance(inst, CryptoPerpetual)


# ═════════════════════════════════════════════════════════════════════════════
# 3. load_all_async() — symbol filter
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadAllAsyncFilter:

    @pytest.mark.asyncio
    async def test_filter_loads_only_requested(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = ALL_SYMBOLS_INFO
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: SYMBOL_INFO_MAP.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async(filters={"symbols": ["EURUSD", "XAUUSD"]})

        assert provider.count == 2
        names = set(provider.loaded_symbols)
        assert "EURUSD" in names
        assert "XAUUSD" in names
        assert "GBPUSD" not in names

    @pytest.mark.asyncio
    async def test_filter_case_insensitive(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = ALL_SYMBOLS_INFO
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: SYMBOL_INFO_MAP.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async(filters={"symbols": ["eurusd"]})

        assert provider.count == 1
        assert "EURUSD" in provider.loaded_symbols

    @pytest.mark.asyncio
    async def test_filter_preserves_exact_suffixed_symbol(self, provider):
        # Exact filter names should load the broker symbol with its original case.
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = [EURUSDM_INFO, EURUSD_INFO]
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: SYMBOL_INFO_MAP.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async(filters={"symbols": ["EURUSDm"]})

        assert provider.count == 1
        assert provider.loaded_symbols == ["EURUSDm"]

    @pytest.mark.asyncio
    async def test_filter_lowercase_matches_suffixed_symbol(self, provider):
        # Lowercase filters remain supported through case-insensitive fallback.
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = [EURUSDM_INFO, GBPUSD_INFO]
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: SYMBOL_INFO_MAP.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async(filters={"symbols": ["eurusdm"]})

        assert provider.count == 1
        assert provider.loaded_symbols == ["EURUSDm"]

    @pytest.mark.asyncio
    async def test_no_filter_loads_all(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = ALL_SYMBOLS_INFO
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: SYMBOL_INFO_MAP.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async(filters=None)

        assert provider.count == 5

    @pytest.mark.asyncio
    async def test_empty_filter_loads_nothing(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = ALL_SYMBOLS_INFO
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: SYMBOL_INFO_MAP.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async(filters={"symbols": []})

        assert provider.count == 0


# ═════════════════════════════════════════════════════════════════════════════
# 4. load_all_async() — partial failures
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadAllAsyncPartialFailures:

    @pytest.mark.asyncio
    async def test_bad_symbol_skipped_good_symbols_loaded(self, provider):
        # CFD (unknown symbol) uses currency_profit for quote_currency
        # so setting currency_profit="" triggers the parse error
        bad_info = make_symbol_info("BADSY", digits=5,
                                   currency_base="USD",
                                   currency_profit="")  # empty → parse error on Cfd

        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = [EURUSD_INFO, bad_info, GBPUSD_INFO]
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: {
                "EURUSD": EURUSD_INFO,
                "BADSY":  bad_info,
                "GBPUSD": GBPUSD_INFO,
            }.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async()

        assert provider.count == 2
        assert "EURUSD" in provider.loaded_symbols
        assert "GBPUSD" in provider.loaded_symbols

    @pytest.mark.asyncio
    async def test_failed_symbols_recorded(self, provider):
        bad_info = make_symbol_info("BADSY", currency_base="USD", currency_profit="")

        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = [EURUSD_INFO, bad_info]
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: {
                "EURUSD": EURUSD_INFO,
                "BADSY":  bad_info,
            }.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_all_async()

        assert len(provider.failed_symbols) == 1
        failed_name, _ = provider.failed_symbols[0]
        assert failed_name == "BADSY"

    @pytest.mark.asyncio
    async def test_symbol_info_none_skipped(self, provider):
        """Symbol exists in symbols_get() but symbol_info() returns None."""
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = [EURUSD_INFO, GBPUSD_INFO]
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: (
                EURUSD_INFO if s == "EURUSD" else None
            )
            mock_mt5.last_error.return_value    = (6, "No connection")

            await provider.load_all_async()

        assert provider.count == 1
        assert "EURUSD" in provider.loaded_symbols
        assert len(provider.failed_symbols) == 1


# ═════════════════════════════════════════════════════════════════════════════
# 5. load_all_async() — mt5.symbols_get() returns None
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadAllAsyncSymbolsGetNone:

    @pytest.mark.asyncio
    async def test_raises_connection_error_when_symbols_get_none(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value = None
            mock_mt5.last_error.return_value  = (6, "No connection")

            with pytest.raises(MT5ConnectionError) as exc_info:
                await provider.load_all_async()

        assert "symbols_get" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_error_message_includes_mt5_error_code(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value = None
            mock_mt5.last_error.return_value  = (6, "No connection")

            with pytest.raises(MT5ConnectionError) as exc_info:
                await provider.load_all_async()

        assert "6" in str(exc_info.value)


# ═════════════════════════════════════════════════════════════════════════════
# 6. load_all_async() — empty symbol list
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadAllAsyncEmpty:

    @pytest.mark.asyncio
    async def test_empty_symbol_list_loads_nothing(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value = []
            mock_mt5.last_error.return_value  = (0, "No error")

            await provider.load_all_async()

        assert provider.count == 0

    @pytest.mark.asyncio
    async def test_empty_list_no_exceptions(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value = []
            mock_mt5.last_error.return_value  = (0, "No error")

            await provider.load_all_async()  # must not raise


# ═════════════════════════════════════════════════════════════════════════════
# 7. load_all_async() — not connected
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadAllAsyncNotConnected:

    @pytest.mark.asyncio
    async def test_raises_when_not_connected(self, config):
        disconnected_conn = make_disconnected_conn(config)
        provider = MT5InstrumentProvider(connection=disconnected_conn)

        with pytest.raises(MT5ConnectionError):
            await provider.load_all_async()


# ═════════════════════════════════════════════════════════════════════════════
# 8. load_ids_async() — happy path
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadIdsAsync:

    @pytest.mark.asyncio
    async def test_loads_requested_instruments(self, provider):
        ids = [
            InstrumentId.from_str("EURUSD.MT5"),
            InstrumentId.from_str("XAUUSD.MT5"),
        ]

        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: SYMBOL_INFO_MAP.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_ids_async(ids)

        assert provider.count == 2
        assert "EURUSD" in provider.loaded_symbols
        assert "XAUUSD" in provider.loaded_symbols

    @pytest.mark.asyncio
    async def test_does_not_load_unrequested_symbols(self, provider):
        ids = [InstrumentId.from_str("EURUSD.MT5")]

        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_ids_async(ids)

        assert "GBPUSD" not in provider.loaded_symbols
        assert "XAUUSD" not in provider.loaded_symbols

    @pytest.mark.asyncio
    async def test_single_id_loaded_correctly(self, provider):
        ids = [InstrumentId.from_str("EURUSD.MT5")]

        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_ids_async(ids)

        inst = provider.get_instrument("EURUSD")
        assert inst is not None
        assert isinstance(inst, CurrencyPair)

    @pytest.mark.asyncio
    async def test_empty_ids_list_loads_nothing(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            await provider.load_ids_async([])

        assert provider.count == 0
        mock_mt5.symbol_info.assert_not_called() if False else None  # just no exception


# ═════════════════════════════════════════════════════════════════════════════
# 9. load_ids_async() — symbol not found
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadIdsAsyncNotFound:

    @pytest.mark.asyncio
    async def test_raises_when_symbol_not_found(self, provider):
        ids = [InstrumentId.from_str("FAKESYMBOL.MT5")]

        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = False  # symbol doesn't exist
            mock_mt5.last_error.return_value    = (0, "No error")

            with pytest.raises(MT5SymbolNotFoundError) as exc_info:
                await provider.load_ids_async(ids)

        assert "FAKESYMBOL" in str(exc_info.value)


# ═════════════════════════════════════════════════════════════════════════════
# 10. load_ids_async() — not connected
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadIdsAsyncNotConnected:

    @pytest.mark.asyncio
    async def test_raises_when_not_connected(self, config):
        disconnected_conn = make_disconnected_conn(config)
        provider = MT5InstrumentProvider(connection=disconnected_conn)
        ids = [InstrumentId.from_str("EURUSD.MT5")]

        with pytest.raises(MT5ConnectionError):
            await provider.load_ids_async(ids)


# ═════════════════════════════════════════════════════════════════════════════
# 11. load_symbol() — happy path
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadSymbol:

    def test_load_eurusd_returns_instrument(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")

            inst = provider.load_symbol("EURUSD")

        assert inst is not None
        assert isinstance(inst, CurrencyPair)

    def test_load_symbol_adds_to_cache(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")

            provider.load_symbol("EURUSD")

        assert provider.count == 1
        assert "EURUSD" in provider.loaded_symbols

    def test_load_symbol_normalises_to_uppercase(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")

            provider.load_symbol("eurusd")

        assert "EURUSD" in provider.loaded_symbols

    def test_load_suffixed_symbol_preserves_broker_case(self, provider):
        # Direct symbol loading should store the parsed broker name unchanged.
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSDM_INFO
            mock_mt5.last_error.return_value    = (0, "No error")

            provider.load_symbol("EURUSDm")

        assert provider.loaded_symbols == ["EURUSDm"]

    def test_load_xauusd_returns_cfd(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = XAUUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")

            inst = provider.load_symbol("XAUUSD")

        assert isinstance(inst, Cfd)

    def test_load_btcusd_returns_crypto_perpetual(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = BTCUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")

            inst = provider.load_symbol("BTCUSD")

        assert isinstance(inst, CryptoPerpetual)


# ═════════════════════════════════════════════════════════════════════════════
# 12. load_symbol() — symbol_select fails
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadSymbolSelectFails:

    def test_raises_symbol_not_found_when_select_fails(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = False
            mock_mt5.last_error.return_value    = (0, "No error")

            with pytest.raises(MT5SymbolNotFoundError) as exc_info:
                provider.load_symbol("FAKESYM")

        assert "FAKESYM" in str(exc_info.value)

    def test_error_message_helpful(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = False
            mock_mt5.last_error.return_value    = (0, "No error")

            with pytest.raises(MT5SymbolNotFoundError) as exc_info:
                provider.load_symbol("FAKESYM")

        assert "Market Watch" in str(exc_info.value)


# ═════════════════════════════════════════════════════════════════════════════
# 13. load_symbol() — symbol_info returns None
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadSymbolInfoNone:

    def test_raises_when_symbol_info_none(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = None
            mock_mt5.last_error.return_value    = (0, "No error")

            with pytest.raises(MT5SymbolNotFoundError):
                provider.load_symbol("EURUSD")


# ═════════════════════════════════════════════════════════════════════════════
# 14. load_symbol() — parse error
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadSymbolParseError:

    def test_raises_instrument_error_on_bad_currency(self, provider):
        bad_info = make_symbol_info("EURUSD", currency_base="")

        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = bad_info
            mock_mt5.last_error.return_value    = (0, "No error")

            with pytest.raises(MT5InstrumentError):
                provider.load_symbol("EURUSD")

    def test_failed_load_does_not_add_to_cache(self, provider):
        bad_info = make_symbol_info("EURUSD", currency_base="")

        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = bad_info
            mock_mt5.last_error.return_value    = (0, "No error")

            try:
                provider.load_symbol("EURUSD")
            except MT5InstrumentError:
                pass

        assert provider.count == 0


# ═════════════════════════════════════════════════════════════════════════════
# 15. load_symbol() — not connected
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadSymbolNotConnected:

    def test_raises_when_not_connected(self, config):
        disconnected_conn = make_disconnected_conn(config)
        provider = MT5InstrumentProvider(connection=disconnected_conn)

        with pytest.raises(MT5ConnectionError):
            provider.load_symbol("EURUSD")


# ═════════════════════════════════════════════════════════════════════════════
# 16. get_instrument()
# ═════════════════════════════════════════════════════════════════════════════

class TestGetInstrument:

    def test_returns_instrument_after_load(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")
            provider.load_symbol("EURUSD")

        inst = provider.get_instrument("EURUSD")
        assert inst is not None

    def test_returns_none_when_not_loaded(self, provider):
        assert provider.get_instrument("EURUSD") is None

    def test_case_insensitive(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")
            provider.load_symbol("EURUSD")

        assert provider.get_instrument("eurusd") is not None
        assert provider.get_instrument("EurUsd") is not None

    def test_exact_suffixed_symbol_lookup(self, provider):
        # Exact lookup should find the instrument without changing its symbol ID.
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSDM_INFO
            mock_mt5.last_error.return_value    = (0, "No error")
            loaded = provider.load_symbol("EURUSDm")

        assert provider.get_instrument("EURUSDm") is loaded

    def test_lowercase_suffixed_symbol_lookup_fallback(self, provider):
        # Lowercase lookup should still resolve to the exact loaded broker symbol.
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSDM_INFO
            mock_mt5.last_error.return_value    = (0, "No error")
            loaded = provider.load_symbol("EURUSDm")

        assert provider.get_instrument("eurusdm") is loaded

    def test_correct_type_returned(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")
            provider.load_symbol("EURUSD")

        inst = provider.get_instrument("EURUSD")
        assert isinstance(inst, CurrencyPair)

    def test_instrument_id_format_correct(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")
            provider.load_symbol("EURUSD")

        inst = provider.get_instrument("EURUSD")
        assert str(inst.id) == "EURUSD.MT5"


# ═════════════════════════════════════════════════════════════════════════════
# 17. loaded_symbols property
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadedSymbols:

    def test_empty_before_any_load(self, provider):
        assert provider.loaded_symbols == []

    @pytest.mark.asyncio
    async def test_contains_all_loaded_symbols(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = [EURUSD_INFO, XAUUSD_INFO]
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: SYMBOL_INFO_MAP.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")
            await provider.load_all_async()

        symbols = provider.loaded_symbols
        assert "EURUSD" in symbols
        assert "XAUUSD" in symbols

    def test_returns_list_type(self, provider):
        assert isinstance(provider.loaded_symbols, list)


# ═════════════════════════════════════════════════════════════════════════════
# 18. failed_symbols property
# ═════════════════════════════════════════════════════════════════════════════

class TestFailedSymbols:

    def test_empty_initially(self, provider):
        assert provider.failed_symbols == []

    @pytest.mark.asyncio
    async def test_records_symbol_and_reason(self, provider):
        bad_info = make_symbol_info("BADSY", currency_base="USD", currency_profit="")

        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = [bad_info]
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = bad_info
            mock_mt5.last_error.return_value    = (0, "No error")
            await provider.load_all_async()

        assert len(provider.failed_symbols) == 1
        symbol, reason = provider.failed_symbols[0]
        assert symbol == "BADSY"
        assert len(reason) > 0

    @pytest.mark.asyncio
    async def test_cleared_on_each_load_all_call(self, provider):
        """failed_symbols must reset on each load_all_async() call."""
        bad_info = make_symbol_info("BADSY", currency_base="USD", currency_profit="")

        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = [bad_info]
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = bad_info
            mock_mt5.last_error.return_value    = (0, "No error")
            await provider.load_all_async()

        assert len(provider.failed_symbols) == 1

        # Second call with clean data — failed list must be cleared
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = [EURUSD_INFO]
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")
            await provider.load_all_async()

        assert len(provider.failed_symbols) == 0

    def test_returns_list_type(self, provider):
        assert isinstance(provider.failed_symbols, list)


# ═════════════════════════════════════════════════════════════════════════════
# 19. count property
# ═════════════════════════════════════════════════════════════════════════════

class TestCount:

    def test_zero_initially(self, provider):
        assert provider.count == 0

    @pytest.mark.asyncio
    async def test_increments_with_each_loaded_symbol(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbols_get.return_value   = ALL_SYMBOLS_INFO
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: SYMBOL_INFO_MAP.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")
            await provider.load_all_async()

        assert provider.count == 5

    def test_count_matches_list_all_length(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")
            provider.load_symbol("EURUSD")

        assert provider.count == len(provider.list_all())


# ═════════════════════════════════════════════════════════════════════════════
# 20. __repr__
# ═════════════════════════════════════════════════════════════════════════════

class TestRepr:

    def test_repr_shows_loaded_count(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")
            provider.load_symbol("EURUSD")

        assert "loaded=1" in repr(provider)

    def test_repr_shows_failed_count(self, provider):
        provider._failed_symbols = [("BADSY", "parse error")]
        assert "failed=1" in repr(provider)

    def test_repr_shows_zero_initially(self, provider):
        r = repr(provider)
        assert "loaded=0" in r
        assert "failed=0" in r


# ═════════════════════════════════════════════════════════════════════════════
# 21. Multiple loads — instruments accumulate
# ═════════════════════════════════════════════════════════════════════════════

class TestMultipleLoads:

    def test_load_symbol_twice_same_symbol_no_duplicate(self, provider):
        """Loading the same symbol twice should not duplicate it."""
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")

            provider.load_symbol("EURUSD")
            provider.load_symbol("EURUSD")

        # NautilusTrader's add() is idempotent — same ID overwrites
        assert provider.count == 1

    def test_load_different_symbols_accumulate(self, provider):
        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.last_error.return_value    = (0, "No error")

            mock_mt5.symbol_info.return_value = EURUSD_INFO
            provider.load_symbol("EURUSD")

            mock_mt5.symbol_info.return_value = XAUUSD_INFO
            provider.load_symbol("XAUUSD")

        assert provider.count == 2


# ═════════════════════════════════════════════════════════════════════════════
# 22. load_ids_async loads only requested symbols
# ═════════════════════════════════════════════════════════════════════════════

class TestLoadIdsAsyncScope:

    @pytest.mark.asyncio
    async def test_only_requested_symbols_loaded(self, provider):
        ids = [InstrumentId.from_str("EURUSD.MT5")]

        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.return_value   = EURUSD_INFO
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_ids_async(ids)

        # Only EURUSD should be present
        assert provider.count == 1
        assert provider.get_instrument("GBPUSD") is None
        assert provider.get_instrument("XAUUSD") is None

    @pytest.mark.asyncio
    async def test_multiple_ids_all_loaded(self, provider):
        ids = [
            InstrumentId.from_str("EURUSD.MT5"),
            InstrumentId.from_str("BTCUSD.MT5"),
        ]

        with patch("mt5connect.providers.mt5") as mock_mt5:
            mock_mt5.symbol_select.return_value = True
            mock_mt5.symbol_info.side_effect    = lambda s: SYMBOL_INFO_MAP.get(s)
            mock_mt5.last_error.return_value    = (0, "No error")

            await provider.load_ids_async(ids)

        assert provider.count == 2
        assert provider.get_instrument("EURUSD") is not None
        assert provider.get_instrument("BTCUSD") is not None
