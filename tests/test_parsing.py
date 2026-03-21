"""
tests/test_parsing.py

Exhaustive tests for parsing.py.

Every function, every symbol type, every edge case, every error path.
These tests prove that MT5 raw data is correctly converted into
NautilusTrader instrument objects with exact precision.

Test groups:
  1.  detect_instrument_type()     — all symbol categories
  2.  resolve_price_precision()    — digits vs overrides
  3.  make_price_increment()       — precision → increment
  4.  make_size_increment()        — volume_step → size increment
  5.  make_margin()                — MT5 % → NautilusTrader fraction
  6.  parse_currency()             — currency code parsing
  7.  parse_symbol_info() FX       — CurrencyPair output
  8.  parse_symbol_info() Metals   — Cfd output
  9.  parse_symbol_info() Energies — Cfd output
  10. parse_symbol_info() Indices  — Cfd output
  11. parse_symbol_info() Crypto   — CryptoPerpetual output
  12. parse_symbol_info() unknown  — Cfd fallback
  13. parse_symbol_info() errors   — None input, bad currencies
  14. parse_quote_tick()           — tick conversion
  15. parse_bar()                  — OHLCV bar conversion
  16. Timeframe mapping            — all MT5 timeframes
  17. Margin edge cases            — zero, >100, fractional
  18. Precision consistency        — price_increment matches price_precision
  19. Instrument ID format         — SYMBOL.MT5
  20. Size precision from volume_step
"""

import time
from decimal import Decimal
from unittest.mock import MagicMock
import numpy as np
import pytest

from nautilus_trader.model.instruments import CurrencyPair, Cfd, CryptoPerpetual
from nautilus_trader.model.enums import AssetClass, BarAggregation, PriceType
from nautilus_trader.model.data import QuoteTick, Bar
from nautilus_trader.model.objects import Price, Quantity

from nautilus_mt5.parsing import (
    detect_instrument_type,
    make_margin,
    make_price_increment,
    make_size_increment,
    parse_bar,
    parse_currency,
    parse_quote_tick,
    parse_symbol_info,
    resolve_price_precision,
    _MT5_TIMEFRAME_MAP,
)
from nautilus_mt5.errors import MT5InstrumentError
from nautilus_mt5.constants import MT5_VENUE


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — build fake MT5 symbol_info namedtuples
# ─────────────────────────────────────────────────────────────────────────────

def make_symbol_info(
    name="EURUSD",
    digits=5,
    trade_tick_size=0.00001,
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
    description="Euro vs US Dollar",
):
    """Build a mock MT5 symbol_info namedtuple with sensible defaults."""
    info = MagicMock()
    info.name               = name
    info.digits             = digits
    info.trade_tick_size    = trade_tick_size
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
    info.description        = description
    return info


def make_tick(bid=1.08500, ask=1.08502, last=1.08501, volume=1, time_s=1700000000):
    tick = MagicMock()
    tick.bid    = bid
    tick.ask    = ask
    tick.last   = last
    tick.volume = volume
    tick.time   = time_s
    return tick


def make_rate(open_=1.085, high=1.090, low=1.080, close=1.088,
              tick_volume=1000, spread=2, real_volume=0, time_s=1700000000):
    """Build a numpy structured array row matching mt5.copy_rates_range() output."""
    dtype = np.dtype([
        ("time", np.int64),
        ("open", np.float64),
        ("high", np.float64),
        ("low", np.float64),
        ("close", np.float64),
        ("tick_volume", np.int64),
        ("spread", np.int32),
        ("real_volume", np.int64),
    ])
    arr = np.array(
        [(time_s, open_, high, low, close, tick_volume, spread, real_volume)],
        dtype=dtype
    )
    return arr[0]


# ═════════════════════════════════════════════════════════════════════════════
# 1. detect_instrument_type()
# ═════════════════════════════════════════════════════════════════════════════

class TestDetectInstrumentType:

    # FX symbols
    def test_eurusd_is_fx(self):     assert detect_instrument_type("EURUSD")  == "fx"
    def test_gbpusd_is_fx(self):     assert detect_instrument_type("GBPUSD")  == "fx"
    def test_usdjpy_is_fx(self):     assert detect_instrument_type("USDJPY")  == "fx"
    def test_audusd_is_fx(self):     assert detect_instrument_type("AUDUSD")  == "fx"
    def test_nzdusd_is_fx(self):     assert detect_instrument_type("NZDUSD")  == "fx"
    def test_gbpjpy_is_fx(self):     assert detect_instrument_type("GBPJPY")  == "fx"
    def test_eurjpy_is_fx(self):     assert detect_instrument_type("EURJPY")  == "fx"
    def test_usdchf_is_fx(self):     assert detect_instrument_type("USDCHF")  == "fx"

    # Metals
    def test_xauusd_is_metal(self):  assert detect_instrument_type("XAUUSD")  == "metal"
    def test_xagusd_is_metal(self):  assert detect_instrument_type("XAGUSD")  == "metal"
    def test_xptusd_is_metal(self):  assert detect_instrument_type("XPTUSD")  == "metal"
    def test_xaueur_is_metal(self):  assert detect_instrument_type("XAUEUR")  == "metal"

    # Energies
    def test_usoil_is_energy(self):  assert detect_instrument_type("USOIL")   == "energy"
    def test_ukoil_is_energy(self):  assert detect_instrument_type("UKOIL")   == "energy"
    def test_ngas_is_energy(self):   assert detect_instrument_type("NGAS")    == "energy"

    # Indices
    def test_us500_is_index(self):   assert detect_instrument_type("US500")   == "index"
    def test_us30_is_index(self):    assert detect_instrument_type("US30")    == "index"
    def test_de40_is_index(self):    assert detect_instrument_type("DE40")    == "index"
    def test_uk100_is_index(self):   assert detect_instrument_type("UK100")   == "index"

    # Crypto
    def test_btcusd_is_crypto(self): assert detect_instrument_type("BTCUSD")  == "crypto"
    def test_ethusd_is_crypto(self): assert detect_instrument_type("ETHUSD")  == "crypto"
    def test_solusd_is_crypto(self): assert detect_instrument_type("SOLUSD")  == "crypto"

    # Unknown → fallback to cfd
    def test_unknown_is_cfd(self):        assert detect_instrument_type("AAPL")    == "cfd"
    def test_unknown2_is_cfd(self):       assert detect_instrument_type("RANDOM")  == "cfd"
    def test_empty_string_is_cfd(self):   assert detect_instrument_type("")        == "cfd"

    # Case insensitive
    def test_lowercase_eurusd(self):  assert detect_instrument_type("eurusd")  == "fx"
    def test_lowercase_btcusd(self):  assert detect_instrument_type("btcusd")  == "crypto"
    def test_mixed_case(self):        assert detect_instrument_type("EurUsd")  == "fx"


# ═════════════════════════════════════════════════════════════════════════════
# 2. resolve_price_precision()
# ═════════════════════════════════════════════════════════════════════════════

class TestResolvePricePrecision:

    def test_eurusd_uses_digits(self):
        # EURUSD not in overrides → use MT5 digits directly
        assert resolve_price_precision("EURUSD", 5) == 5

    def test_gbpusd_uses_digits(self):
        assert resolve_price_precision("GBPUSD", 5) == 5

    def test_xauusd_uses_override(self):
        # XAUUSD override = 2, regardless of what digits says
        assert resolve_price_precision("XAUUSD", 3) == 2

    def test_xagusd_uses_override(self):
        assert resolve_price_precision("XAGUSD", 4) == 3

    def test_btcusd_uses_override(self):
        assert resolve_price_precision("BTCUSD", 5) == 2

    def test_us500_uses_override(self):
        assert resolve_price_precision("US500", 2) == 1

    def test_de40_uses_override(self):
        assert resolve_price_precision("DE40", 2) == 1

    def test_override_beats_digits(self):
        # Even if broker sends different digits, override wins for known symbols
        assert resolve_price_precision("XAUUSD", 99) == 2

    def test_unknown_symbol_uses_digits(self):
        assert resolve_price_precision("UNKNOWN", 4) == 4

    def test_case_insensitive(self):
        assert resolve_price_precision("xauusd", 3) == 2


# ═════════════════════════════════════════════════════════════════════════════
# 3. make_price_increment()
# ═════════════════════════════════════════════════════════════════════════════

class TestMakePriceIncrement:

    def test_5_decimal_places(self):
        inc = make_price_increment(5)
        assert str(inc) == "0.00001"

    def test_4_decimal_places(self):
        inc = make_price_increment(4)
        assert str(inc) == "0.0001"

    def test_3_decimal_places(self):
        inc = make_price_increment(3)
        assert str(inc) == "0.001"

    def test_2_decimal_places(self):
        inc = make_price_increment(2)
        assert str(inc) == "0.01"

    def test_1_decimal_place(self):
        inc = make_price_increment(1)
        assert str(inc) == "0.1"

    def test_0_decimal_places(self):
        inc = make_price_increment(0)
        assert str(inc) == "1"

    def test_returns_price_type(self):
        assert isinstance(make_price_increment(5), Price)

    def test_precision_matches(self):
        inc = make_price_increment(5)
        assert inc.precision == 5


# ═════════════════════════════════════════════════════════════════════════════
# 4. make_size_increment()
# ═════════════════════════════════════════════════════════════════════════════

class TestMakeSizeIncrement:

    def test_micro_lots_001(self):
        qty, prec = make_size_increment(0.01)
        assert prec == 2
        assert str(qty) == "0.01"

    def test_mini_lots_01(self):
        qty, prec = make_size_increment(0.1)
        assert prec == 1
        assert str(qty) == "0.1"

    def test_standard_lots_1(self):
        qty, prec = make_size_increment(1.0)
        assert prec == 0
        assert str(qty) == "1"

    def test_returns_quantity_type(self):
        qty, prec = make_size_increment(0.01)
        assert isinstance(qty, Quantity)

    def test_precision_consistency(self):
        """size_increment.precision must equal returned precision."""
        qty, prec = make_size_increment(0.01)
        assert qty.precision == prec


# ═════════════════════════════════════════════════════════════════════════════
# 5. make_margin()
# ═════════════════════════════════════════════════════════════════════════════

class TestMakeMargin:

    def test_three_percent(self):
        m = make_margin(3.0)
        assert float(m) == pytest.approx(0.03, abs=1e-6)

    def test_one_percent(self):
        m = make_margin(1.0)
        assert float(m) == pytest.approx(0.01, abs=1e-6)

    def test_zero_returns_safe_default(self):
        m = make_margin(0.0)
        assert float(m) == pytest.approx(0.01, abs=1e-6)

    def test_negative_returns_safe_default(self):
        m = make_margin(-5.0)
        assert float(m) == pytest.approx(0.01, abs=1e-6)

    def test_100_percent(self):
        m = make_margin(100.0)
        assert float(m) == pytest.approx(1.0, abs=1e-4)

    def test_returns_decimal_type(self):
        assert isinstance(make_margin(3.0), Decimal)

    def test_fifty_percent(self):
        m = make_margin(50.0)
        assert float(m) == pytest.approx(0.5, abs=1e-4)

    def test_small_fraction(self):
        m = make_margin(0.5)
        assert float(m) == pytest.approx(0.005, abs=1e-6)


# ═════════════════════════════════════════════════════════════════════════════
# 6. parse_currency()
# ═════════════════════════════════════════════════════════════════════════════

class TestParseCurrency:

    def test_eur(self):
        c = parse_currency("EUR")
        assert c.code == "EUR"

    def test_usd(self):
        c = parse_currency("USD")
        assert c.code == "USD"

    def test_gbp(self):
        c = parse_currency("GBP")
        assert c.code == "GBP"

    def test_jpy(self):
        c = parse_currency("JPY")
        assert c.code == "JPY"

    def test_lowercase_normalised(self):
        c = parse_currency("eur")
        assert c.code == "EUR"

    def test_with_spaces(self):
        c = parse_currency("  USD  ")
        assert c.code == "USD"

    def test_empty_string_raises(self):
        with pytest.raises(MT5InstrumentError):
            parse_currency("")

    def test_whitespace_only_raises(self):
        with pytest.raises(MT5InstrumentError):
            parse_currency("   ")


# ═════════════════════════════════════════════════════════════════════════════
# 7. parse_symbol_info() — FX (CurrencyPair)
# ═════════════════════════════════════════════════════════════════════════════

class TestParseSymbolInfoFX:

    def test_eurusd_returns_currency_pair(self):
        info = make_symbol_info("EURUSD")
        inst = parse_symbol_info(info)
        assert isinstance(inst, CurrencyPair)

    def test_eurusd_instrument_id(self):
        inst = parse_symbol_info(make_symbol_info("EURUSD"))
        assert str(inst.id) == "EURUSD.MT5"

    def test_eurusd_base_currency(self):
        inst = parse_symbol_info(make_symbol_info("EURUSD"))
        assert inst.base_currency.code == "EUR"

    def test_eurusd_quote_currency(self):
        inst = parse_symbol_info(make_symbol_info("EURUSD"))
        assert inst.quote_currency.code == "USD"

    def test_eurusd_price_precision(self):
        inst = parse_symbol_info(make_symbol_info("EURUSD", digits=5))
        assert inst.price_precision == 5

    def test_eurusd_size_precision(self):
        inst = parse_symbol_info(make_symbol_info("EURUSD", volume_step=0.01))
        assert inst.size_precision == 2

    def test_eurusd_price_increment(self):
        inst = parse_symbol_info(make_symbol_info("EURUSD", digits=5))
        assert str(inst.price_increment) == "0.00001"

    def test_eurusd_min_quantity(self):
        inst = parse_symbol_info(make_symbol_info("EURUSD", volume_min=0.01))
        assert float(inst.min_quantity) == pytest.approx(0.01)

    def test_eurusd_max_quantity(self):
        inst = parse_symbol_info(make_symbol_info("EURUSD", volume_max=1000.0))
        assert float(inst.max_quantity) == pytest.approx(1000.0)

    def test_eurusd_maker_fee_zero(self):
        inst = parse_symbol_info(make_symbol_info("EURUSD"))
        assert inst.maker_fee == Decimal("0")

    def test_eurusd_taker_fee_zero(self):
        inst = parse_symbol_info(make_symbol_info("EURUSD"))
        assert inst.taker_fee == Decimal("0")

    def test_gbpusd_base_currency(self):
        inst = parse_symbol_info(make_symbol_info("GBPUSD",
            currency_base="GBP", currency_profit="USD"))
        assert inst.base_currency.code == "GBP"

    def test_usdjpy(self):
        inst = parse_symbol_info(make_symbol_info("USDJPY",
            digits=3, currency_base="USD", currency_profit="JPY",
            volume_step=0.01))
        assert isinstance(inst, CurrencyPair)
        assert inst.price_precision == 3
        assert inst.base_currency.code == "USD"
        assert inst.quote_currency.code == "JPY"

    def test_eurusd_margin_init(self):
        inst = parse_symbol_info(make_symbol_info("EURUSD", margin_initial=3.0))
        assert float(inst.margin_init) == pytest.approx(0.03, abs=1e-4)

    def test_margin_maint_zero_uses_default(self):
        inst = parse_symbol_info(make_symbol_info("EURUSD", margin_maintenance=0.0))
        assert float(inst.margin_maint) == pytest.approx(0.01, abs=1e-4)

    def test_venue_is_mt5(self):
        inst = parse_symbol_info(make_symbol_info("EURUSD"))
        assert inst.id.venue == MT5_VENUE


# ═════════════════════════════════════════════════════════════════════════════
# 8. parse_symbol_info() — Metals (Cfd)
# ═════════════════════════════════════════════════════════════════════════════

class TestParseSymbolInfoMetals:

    def test_xauusd_returns_cfd(self):
        info = make_symbol_info("XAUUSD", digits=2, currency_base="XAU",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=100.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert isinstance(inst, Cfd)

    def test_xauusd_price_precision_uses_override(self):
        # digits=3 but override forces precision=2
        info = make_symbol_info("XAUUSD", digits=3, currency_base="XAU",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=100.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert inst.price_precision == 2

    def test_xauusd_instrument_id(self):
        info = make_symbol_info("XAUUSD", digits=2, currency_base="XAU",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=100.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert str(inst.id) == "XAUUSD.MT5"

    def test_xauusd_quote_currency(self):
        info = make_symbol_info("XAUUSD", digits=2, currency_base="XAU",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=100.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert inst.quote_currency.code == "USD"

    def test_xauusd_asset_class_commodity(self):
        info = make_symbol_info("XAUUSD", digits=2, currency_base="XAU",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=100.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert inst.asset_class == AssetClass.COMMODITY

    def test_xagusd_returns_cfd(self):
        info = make_symbol_info("XAGUSD", digits=3, currency_base="XAG",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=5000.0, margin_initial=2.0)
        inst = parse_symbol_info(info)
        assert isinstance(inst, Cfd)


# ═════════════════════════════════════════════════════════════════════════════
# 9. parse_symbol_info() — Energies (Cfd)
# ═════════════════════════════════════════════════════════════════════════════

class TestParseSymbolInfoEnergies:

    def test_usoil_returns_cfd(self):
        info = make_symbol_info("USOIL", digits=2, currency_base="USD",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=100.0, margin_initial=5.0)
        inst = parse_symbol_info(info)
        assert isinstance(inst, Cfd)

    def test_usoil_asset_class_commodity(self):
        info = make_symbol_info("USOIL", digits=2, currency_base="USD",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=100.0, margin_initial=5.0)
        inst = parse_symbol_info(info)
        assert inst.asset_class == AssetClass.COMMODITY

    def test_ukoil_instrument_id(self):
        info = make_symbol_info("UKOIL", digits=2, currency_base="GBP",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=100.0, margin_initial=5.0)
        inst = parse_symbol_info(info)
        assert str(inst.id) == "UKOIL.MT5"


# ═════════════════════════════════════════════════════════════════════════════
# 10. parse_symbol_info() — Indices (Cfd)
# ═════════════════════════════════════════════════════════════════════════════

class TestParseSymbolInfoIndices:

    def test_us500_returns_cfd(self):
        info = make_symbol_info("US500", digits=1, currency_base="USD",
                                currency_profit="USD", volume_step=0.1,
                                trade_contract_size=1.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert isinstance(inst, Cfd)

    def test_us500_precision_uses_override(self):
        # override forces precision=1
        info = make_symbol_info("US500", digits=2, currency_base="USD",
                                currency_profit="USD", volume_step=0.1,
                                trade_contract_size=1.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert inst.price_precision == 1

    def test_us500_asset_class_index(self):
        info = make_symbol_info("US500", digits=1, currency_base="USD",
                                currency_profit="USD", volume_step=0.1,
                                trade_contract_size=1.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert inst.asset_class == AssetClass.INDEX

    def test_de40_asset_class_index(self):
        info = make_symbol_info("DE40", digits=1, currency_base="EUR",
                                currency_profit="EUR", volume_step=0.1,
                                trade_contract_size=1.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert inst.asset_class == AssetClass.INDEX


# ═════════════════════════════════════════════════════════════════════════════
# 11. parse_symbol_info() — Crypto (CryptoPerpetual)
# ═════════════════════════════════════════════════════════════════════════════

class TestParseSymbolInfoCrypto:

    def test_btcusd_returns_crypto_perpetual(self):
        info = make_symbol_info("BTCUSD", digits=2, currency_base="BTC",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=1.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert isinstance(inst, CryptoPerpetual)

    def test_btcusd_precision_uses_override(self):
        info = make_symbol_info("BTCUSD", digits=5, currency_base="BTC",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=1.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert inst.price_precision == 2

    def test_btcusd_base_currency(self):
        info = make_symbol_info("BTCUSD", digits=2, currency_base="BTC",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=1.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert inst.base_currency.code == "BTC"

    def test_btcusd_quote_currency(self):
        info = make_symbol_info("BTCUSD", digits=2, currency_base="BTC",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=1.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert inst.quote_currency.code == "USD"

    def test_btcusd_not_inverse(self):
        info = make_symbol_info("BTCUSD", digits=2, currency_base="BTC",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=1.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert inst.is_inverse is False

    def test_ethusd_returns_crypto_perpetual(self):
        info = make_symbol_info("ETHUSD", digits=2, currency_base="ETH",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=1.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert isinstance(inst, CryptoPerpetual)

    def test_btcusd_instrument_id(self):
        info = make_symbol_info("BTCUSD", digits=2, currency_base="BTC",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=1.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert str(inst.id) == "BTCUSD.MT5"


# ═════════════════════════════════════════════════════════════════════════════
# 12. parse_symbol_info() — Unknown → Cfd fallback
# ═════════════════════════════════════════════════════════════════════════════

class TestParseSymbolInfoUnknown:

    def test_unknown_symbol_returns_cfd(self):
        info = make_symbol_info("AAPL", digits=2, currency_base="USD",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=1.0, margin_initial=5.0)
        inst = parse_symbol_info(info)
        assert isinstance(inst, Cfd)

    def test_unknown_asset_class_alternative(self):
        info = make_symbol_info("AAPL", digits=2, currency_base="USD",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=1.0, margin_initial=5.0)
        inst = parse_symbol_info(info)
        assert inst.asset_class == AssetClass.ALTERNATIVE

    def test_unknown_instrument_id_format(self):
        info = make_symbol_info("RANDOMX", digits=2, currency_base="USD",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=1.0, margin_initial=5.0)
        inst = parse_symbol_info(info)
        assert str(inst.id) == "RANDOMX.MT5"


# ═════════════════════════════════════════════════════════════════════════════
# 13. parse_symbol_info() — Error cases
# ═════════════════════════════════════════════════════════════════════════════

class TestParseSymbolInfoErrors:

    def test_none_raises_instrument_error(self):
        with pytest.raises(MT5InstrumentError) as exc_info:
            parse_symbol_info(None)
        assert "None" in str(exc_info.value)

    def test_helpful_none_message_mentions_market_watch(self):
        with pytest.raises(MT5InstrumentError) as exc_info:
            parse_symbol_info(None)
        assert "Market Watch" in str(exc_info.value)

    def test_empty_base_currency_raises(self):
        info = make_symbol_info("EURUSD", currency_base="")
        with pytest.raises(MT5InstrumentError):
            parse_symbol_info(info)

    def test_empty_profit_currency_raises(self):
        info = make_symbol_info("EURUSD", currency_profit="")
        with pytest.raises(MT5InstrumentError):
            parse_symbol_info(info)


# ═════════════════════════════════════════════════════════════════════════════
# 14. parse_quote_tick()
# ═════════════════════════════════════════════════════════════════════════════

class TestParseQuoteTick:

    @pytest.fixture
    def eurusd(self):
        return parse_symbol_info(make_symbol_info("EURUSD"))

    def test_returns_quote_tick(self, eurusd):
        tick = make_tick(bid=1.08500, ask=1.08502)
        result = parse_quote_tick(tick, eurusd)
        assert isinstance(result, QuoteTick)

    def test_instrument_id_matches(self, eurusd):
        tick = make_tick()
        result = parse_quote_tick(tick, eurusd)
        assert result.instrument_id == eurusd.id

    def test_bid_price_correct(self, eurusd):
        tick = make_tick(bid=1.08500)
        result = parse_quote_tick(tick, eurusd)
        assert float(result.bid_price) == pytest.approx(1.08500)

    def test_ask_price_correct(self, eurusd):
        tick = make_tick(ask=1.08502)
        result = parse_quote_tick(tick, eurusd)
        assert float(result.ask_price) == pytest.approx(1.08502)

    def test_bid_price_precision(self, eurusd):
        tick = make_tick(bid=1.08500)
        result = parse_quote_tick(tick, eurusd)
        assert result.bid_price.precision == 5

    def test_ask_price_precision(self, eurusd):
        tick = make_tick(ask=1.08502)
        result = parse_quote_tick(tick, eurusd)
        assert result.ask_price.precision == 5

    def test_ts_event_in_nanoseconds(self):
        info = make_symbol_info("EURUSD")
        inst = parse_symbol_info(info)
        tick = make_tick(time_s=1700000000)
        result = parse_quote_tick(tick, inst)
        expected_ns = 1700000000 * 1_000_000_000
        assert result.ts_event == expected_ns

    def test_bid_size_nominal(self, eurusd):
        """MT5 has no depth — bid_size should be a large nominal value."""
        tick = make_tick()
        result = parse_quote_tick(tick, eurusd)
        assert float(result.bid_size) == 1_000_000

    def test_ask_size_nominal(self, eurusd):
        tick = make_tick()
        result = parse_quote_tick(tick, eurusd)
        assert float(result.ask_size) == 1_000_000

    def test_gold_tick_precision(self):
        """Gold uses price_precision=2 from override."""
        info = make_symbol_info("XAUUSD", digits=2, currency_base="XAU",
                                currency_profit="USD", volume_step=0.01,
                                trade_contract_size=100.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        tick = make_tick(bid=1985.50, ask=1985.75)
        result = parse_quote_tick(tick, inst)
        assert result.bid_price.precision == 2
        assert float(result.bid_price) == pytest.approx(1985.50)


# ═════════════════════════════════════════════════════════════════════════════
# 15. parse_bar()
# ═════════════════════════════════════════════════════════════════════════════

class TestParseBar:

    @pytest.fixture
    def eurusd(self):
        return parse_symbol_info(make_symbol_info("EURUSD"))

    def test_returns_bar(self, eurusd):
        rate = make_rate()
        result = parse_bar(rate, eurusd, timeframe=16385)  # H1
        assert isinstance(result, Bar)

    def test_open_price(self, eurusd):
        rate = make_rate(open_=1.085)
        result = parse_bar(rate, eurusd, timeframe=16385)
        assert float(result.open) == pytest.approx(1.085)

    def test_high_price(self, eurusd):
        rate = make_rate(high=1.090)
        result = parse_bar(rate, eurusd, timeframe=16385)
        assert float(result.high) == pytest.approx(1.090)

    def test_low_price(self, eurusd):
        rate = make_rate(low=1.080)
        result = parse_bar(rate, eurusd, timeframe=16385)
        assert float(result.low) == pytest.approx(1.080)

    def test_close_price(self, eurusd):
        rate = make_rate(close=1.088)
        result = parse_bar(rate, eurusd, timeframe=16385)
        assert float(result.close) == pytest.approx(1.088)

    def test_volume(self, eurusd):
        rate = make_rate(tick_volume=1500)
        result = parse_bar(rate, eurusd, timeframe=16385)
        assert float(result.volume) == pytest.approx(1500)

    def test_ts_event_nanoseconds(self, eurusd):
        rate = make_rate(time_s=1700000000)
        result = parse_bar(rate, eurusd, timeframe=16385)
        assert result.ts_event == 1700000000 * 1_000_000_000

    def test_bar_type_instrument_id(self, eurusd):
        rate = make_rate()
        result = parse_bar(rate, eurusd, timeframe=16385)
        assert result.bar_type.instrument_id == eurusd.id

    def test_m1_aggregation(self, eurusd):
        rate = make_rate()
        result = parse_bar(rate, eurusd, timeframe=1)
        assert result.bar_type.spec.aggregation == BarAggregation.MINUTE
        assert result.bar_type.spec.step == 1

    def test_h1_aggregation(self, eurusd):
        rate = make_rate()
        result = parse_bar(rate, eurusd, timeframe=16385)
        assert result.bar_type.spec.aggregation == BarAggregation.HOUR
        assert result.bar_type.spec.step == 1

    def test_h4_aggregation(self, eurusd):
        rate = make_rate()
        result = parse_bar(rate, eurusd, timeframe=16388)
        assert result.bar_type.spec.aggregation == BarAggregation.HOUR
        assert result.bar_type.spec.step == 4

    def test_d1_aggregation(self, eurusd):
        rate = make_rate()
        result = parse_bar(rate, eurusd, timeframe=16408)
        assert result.bar_type.spec.aggregation == BarAggregation.DAY
        assert result.bar_type.spec.step == 1

    def test_unknown_timeframe_falls_back_to_d1(self, eurusd):
        rate = make_rate()
        result = parse_bar(rate, eurusd, timeframe=99999)
        assert result.bar_type.spec.aggregation == BarAggregation.DAY


# ═════════════════════════════════════════════════════════════════════════════
# 16. Timeframe mapping completeness
# ═════════════════════════════════════════════════════════════════════════════

class TestTimeframeMapping:

    def test_all_minute_timeframes_present(self):
        minute_tfs = [1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30]
        for tf in minute_tfs:
            assert tf in _MT5_TIMEFRAME_MAP, f"M{tf} timeframe missing"

    def test_all_hour_timeframes_present(self):
        hour_tfs = [16385, 16386, 16387, 16388, 16390, 16392, 16396]
        for tf in hour_tfs:
            assert tf in _MT5_TIMEFRAME_MAP, f"Hour TF {tf} missing"

    def test_d1_present(self):   assert 16408 in _MT5_TIMEFRAME_MAP
    def test_w1_present(self):   assert 32769 in _MT5_TIMEFRAME_MAP
    def test_mn1_present(self):  assert 49153 in _MT5_TIMEFRAME_MAP

    def test_all_aggregations_are_valid(self):
        valid = {BarAggregation.MINUTE, BarAggregation.HOUR,
                 BarAggregation.DAY, BarAggregation.WEEK, BarAggregation.MONTH}
        for tf, (step, agg) in _MT5_TIMEFRAME_MAP.items():
            assert agg in valid
            assert step >= 1


# ═════════════════════════════════════════════════════════════════════════════
# 17. Margin edge cases
# ═════════════════════════════════════════════════════════════════════════════

class TestMarginEdgeCases:

    def test_margin_init_stored_as_fraction(self):
        inst = parse_symbol_info(make_symbol_info("EURUSD", margin_initial=3.0))
        assert float(inst.margin_init) < 1.0  # must be fraction not percentage

    def test_both_margins_zero_both_use_default(self):
        inst = parse_symbol_info(make_symbol_info(
            "EURUSD", margin_initial=0.0, margin_maintenance=0.0
        ))
        assert float(inst.margin_init)  == pytest.approx(0.01, abs=1e-4)
        assert float(inst.margin_maint) == pytest.approx(0.01, abs=1e-4)

    def test_margin_maint_set_correctly(self):
        inst = parse_symbol_info(make_symbol_info(
            "EURUSD", margin_initial=3.0, margin_maintenance=2.0
        ))
        assert float(inst.margin_maint) == pytest.approx(0.02, abs=1e-4)


# ═════════════════════════════════════════════════════════════════════════════
# 18. Price increment consistency
# ═════════════════════════════════════════════════════════════════════════════

class TestPriceIncrementConsistency:

    @pytest.mark.parametrize("symbol,digits,expected_inc_str", [
        ("EURUSD",  5, "0.00001"),
        ("USDJPY",  3, "0.001"),
        ("XAUUSD",  2, "0.01"),    # override → 2
        ("US500",   1, "0.1"),     # override → 1
        ("BTCUSD",  2, "0.01"),    # override → 2
    ])
    def test_price_increment_matches_precision(self, symbol, digits, expected_inc_str):
        # Build appropriate currency info for each symbol type
        currency_map = {
            "EURUSD": ("EUR", "USD"),
            "USDJPY": ("USD", "JPY"),
            "XAUUSD": ("XAU", "USD"),
            "US500":  ("USD", "USD"),
            "BTCUSD": ("BTC", "USD"),
        }
        base, profit = currency_map[symbol]
        info = make_symbol_info(symbol, digits=digits, currency_base=base,
                                currency_profit=profit, volume_step=0.01,
                                trade_contract_size=1.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert str(inst.price_increment) == expected_inc_str


# ═════════════════════════════════════════════════════════════════════════════
# 19. Instrument ID format
# ═════════════════════════════════════════════════════════════════════════════

class TestInstrumentIdFormat:

    @pytest.mark.parametrize("symbol", [
        "EURUSD", "GBPUSD", "XAUUSD", "BTCUSD", "US500", "USOIL"
    ])
    def test_id_format_is_symbol_dot_mt5(self, symbol):
        currency_map = {
            "EURUSD": ("EUR", "USD"),
            "GBPUSD": ("GBP", "USD"),
            "XAUUSD": ("XAU", "USD"),
            "BTCUSD": ("BTC", "USD"),
            "US500":  ("USD", "USD"),
            "USOIL":  ("USD", "USD"),
        }
        base, profit = currency_map[symbol]
        info = make_symbol_info(symbol, digits=2, currency_base=base,
                                currency_profit=profit, volume_step=0.01,
                                trade_contract_size=1.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert str(inst.id) == f"{symbol}.MT5"


# ═════════════════════════════════════════════════════════════════════════════
# 20. Size precision derived from volume_step
# ═════════════════════════════════════════════════════════════════════════════

class TestSizePrecision:

    @pytest.mark.parametrize("volume_step,expected_precision", [
        (0.01,  2),
        (0.1,   1),
        (1.0,   0),
    ])
    def test_size_precision_from_volume_step(self, volume_step, expected_precision):
        info = make_symbol_info("EURUSD", volume_step=volume_step)
        inst = parse_symbol_info(info)
        assert inst.size_precision == expected_precision

    def test_min_quantity_precision_matches_size_precision(self):
        info = make_symbol_info("EURUSD", volume_step=0.01, volume_min=0.01)
        inst = parse_symbol_info(info)
        assert inst.min_quantity.precision == inst.size_precision


# ═════════════════════════════════════════════════════════════════════════════
# BROKER SUFFIX NORMALISATION (added for multi-broker support)
# ═════════════════════════════════════════════════════════════════════════════

class TestNormalizeSymbol:
    """Tests for the normalize_symbol() function in constants.py."""

    from nautilus_mt5.constants import normalize_symbol

    # Exness 'm' suffix (standard accounts)
    def test_eurusdm_normalized(self):
        from nautilus_mt5.constants import normalize_symbol
        assert normalize_symbol("EURUSDm") == "EURUSD"

    def test_xauusdm_normalized(self):
        from nautilus_mt5.constants import normalize_symbol
        assert normalize_symbol("XAUUSDm") == "XAUUSD"

    def test_btcusdm_normalized(self):
        from nautilus_mt5.constants import normalize_symbol
        assert normalize_symbol("BTCUSDm") == "BTCUSD"

    def test_ustecm_normalized(self):
        from nautilus_mt5.constants import normalize_symbol
        assert normalize_symbol("USTECm") == "USTEC"

    def test_btcjpym_normalized(self):
        from nautilus_mt5.constants import normalize_symbol
        assert normalize_symbol("BTCJPYm") == "BTCJPY"

    # No suffix (IC Markets, Pepperstone, Exness zero)
    def test_eurusd_no_suffix_unchanged(self):
        from nautilus_mt5.constants import normalize_symbol
        assert normalize_symbol("EURUSD") == "EURUSD"

    def test_xauusd_no_suffix_unchanged(self):
        from nautilus_mt5.constants import normalize_symbol
        assert normalize_symbol("XAUUSD") == "XAUUSD"

    # 'c' suffix
    def test_eurusdc_normalized(self):
        from nautilus_mt5.constants import normalize_symbol
        assert normalize_symbol("EURUSDc") == "EURUSD"

    # Trailing dot
    def test_eurusd_dot_normalized(self):
        from nautilus_mt5.constants import normalize_symbol
        assert normalize_symbol("EURUSD.") == "EURUSD"

    # Output is always uppercase
    def test_output_always_uppercase(self):
        from nautilus_mt5.constants import normalize_symbol
        assert normalize_symbol("eurusdm") == "EURUSD"
        assert normalize_symbol("EurUsdM") == "EURUSD"

    # Whitespace stripped
    def test_whitespace_stripped(self):
        from nautilus_mt5.constants import normalize_symbol
        assert normalize_symbol("  EURUSDm  ") == "EURUSD"


class TestDetectInstrumentTypeBrokerSuffixes:
    """Instrument type detection works with broker-suffixed symbol names."""

    # Exness 'm' suffix
    def test_eurusdm_is_fx(self):        assert detect_instrument_type("EURUSDm")  == "fx"
    def test_gbpusdm_is_fx(self):        assert detect_instrument_type("GBPUSDm")  == "fx"
    def test_usdjpym_is_fx(self):        assert detect_instrument_type("USDJPYm")  == "fx"
    def test_xauusdm_is_metal(self):     assert detect_instrument_type("XAUUSDm")  == "metal"
    def test_xagusdm_is_metal(self):     assert detect_instrument_type("XAGUSDm")  == "metal"
    def test_btcusdm_is_crypto(self):    assert detect_instrument_type("BTCUSDm")  == "crypto"
    def test_ethusdm_is_crypto(self):    assert detect_instrument_type("ETHUSDm")  == "crypto"
    def test_ustecm_is_index(self):      assert detect_instrument_type("USTECm")   == "index"
    def test_btcjpym_is_crypto(self):    assert detect_instrument_type("BTCJPYm")  == "crypto"

    # Trailing dot suffix
    def test_eurusd_dot_is_fx(self):     assert detect_instrument_type("EURUSD.")  == "fx"
    def test_xauusd_dot_is_metal(self):  assert detect_instrument_type("XAUUSD.")  == "metal"

    # No suffix — still works
    def test_eurusd_no_suffix(self):     assert detect_instrument_type("EURUSD")   == "fx"
    def test_xauusd_no_suffix(self):     assert detect_instrument_type("XAUUSD")   == "metal"
    def test_btcusd_no_suffix(self):     assert detect_instrument_type("BTCUSD")   == "crypto"


class TestResolvePricePrecisionBrokerSuffixes:
    """Price precision overrides work with broker-suffixed symbol names."""

    def test_xauusdm_gets_override(self):
        assert resolve_price_precision("XAUUSDm", 3) == 2  # override=2, not digits=3

    def test_btcusdm_gets_override(self):
        assert resolve_price_precision("BTCUSDm", 5) == 2

    def test_eurusdm_uses_digits(self):
        # No override for EURUSD — use MT5 digits
        assert resolve_price_precision("EURUSDm", 5) == 5

    def test_ustecm_gets_override(self):
        assert resolve_price_precision("USTECm", 2) == 1


class TestParseSymbolInfoBrokerSuffixes:
    """Full parse_symbol_info works end-to-end with broker-suffixed names."""

    def test_eurusdm_parses_as_currency_pair(self):
        info = make_symbol_info("EURUSDm", digits=5,
                                currency_base="EUR", currency_profit="USD")
        inst = parse_symbol_info(info)
        assert isinstance(inst, CurrencyPair)

    def test_eurusdm_instrument_id_preserves_broker_name(self):
        """The instrument ID uses the BROKER name, not the canonical name."""
        info = make_symbol_info("EURUSDm", digits=5,
                                currency_base="EUR", currency_profit="USD")
        inst = parse_symbol_info(info)
        assert str(inst.id) == "EURUSDm.MT5"

    def test_xauusdm_parses_as_cfd(self):
        info = make_symbol_info("XAUUSDm", digits=2,
                                currency_base="XAU", currency_profit="USD",
                                trade_contract_size=100.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert isinstance(inst, Cfd)

    def test_xauusdm_precision_override_applied(self):
        info = make_symbol_info("XAUUSDm", digits=3,  # MT5 says 3
                                currency_base="XAU", currency_profit="USD",
                                trade_contract_size=100.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert inst.price_precision == 2  # override forces 2

    def test_btcusdm_parses_as_crypto(self):
        info = make_symbol_info("BTCUSDm", digits=2,
                                currency_base="BTC", currency_profit="USD",
                                trade_contract_size=1.0, margin_initial=1.0)
        inst = parse_symbol_info(info)
        assert isinstance(inst, CryptoPerpetual)

    def test_ustecm_parses_as_cfd_index(self):
        from nautilus_trader.model.enums import AssetClass
        info = make_symbol_info("USTECm", digits=1,
                                currency_base="USD", currency_profit="USD",
                                trade_contract_size=1.0, margin_initial=1.0,
                                volume_step=0.1)
        inst = parse_symbol_info(info)
        assert isinstance(inst, Cfd)
        assert inst.asset_class == AssetClass.INDEX