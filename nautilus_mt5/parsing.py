"""
nautilus_mt5/parsing.py

Converts MT5 raw data structures into NautilusTrader domain objects.

This is the most detail-critical file in the adapter.
Every number must be exact — wrong price precision or lot size
means your backtest runs with wrong fills and your live orders
get rejected by the broker.

Three main conversion functions:
  parse_symbol_info()   -> InstrumentAny  (the main entry point)
  parse_quote_tick()    -> QuoteTick      (used in data.py polling loop)
  parse_bar()          -> Bar             (used in downloader.py)

Instrument type decision tree (based on MT5 symbol name + calc_mode):
  EURUSD, GBPUSD ...  -> CurrencyPair   (FX spot)
  XAUUSD, XAGUSD ...  -> Cfd            (metals)
  USOIL, UKOIL ...    -> Cfd            (energies)
  US500, DE40  ...    -> Cfd            (indices)
  BTCUSD, ETHUSD ...  -> CryptoPerpetual
  everything else     -> Cfd            (safe fallback)
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Union

from nautilus_trader.model.currencies import Currency
from nautilus_trader.model.enums import AssetClass, BarAggregation, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import Cfd, CryptoPerpetual, CurrencyPair
from nautilus_trader.model.data import Bar, BarType, BarSpecification, QuoteTick
from nautilus_trader.model.objects import Price, Quantity

from nautilus_mt5.constants import (
    MT5_VENUE,
    CRYPTO_SYMBOLS,
    ENERGY_SYMBOLS,
    FX_SYMBOLS,
    INDEX_SYMBOLS,
    METAL_SYMBOLS,
    PRICE_PRECISION_OVERRIDES,
    normalize_symbol,
)
from nautilus_mt5.errors import MT5InstrumentError

# Union type for any instrument this adapter produces
InstrumentAny = Union[CurrencyPair, Cfd, CryptoPerpetual]

# MT5 calc_mode constants (from MQL5 docs)
_CALC_MODE_FOREX   = 0
_CALC_MODE_FUTURES = 1
_CALC_MODE_CFD     = 2


# ─────────────────────────────────────────────────────────────────────────────
# INSTRUMENT TYPE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_instrument_type(symbol: str) -> str:
    """
    Determine what NautilusTrader instrument type to create for a given symbol.

    Returns one of: 'fx', 'metal', 'energy', 'index', 'crypto', 'cfd'

    Strips broker-specific suffixes before classification so that
    "EURUSDm" (Exness), "EURUSD." (some brokers), and "EURUSD" (IC Markets)
    all correctly resolve to 'fx'.

    The original broker symbol name is preserved for all MT5 API calls —
    this function only affects classification.
    """
    # Strip broker suffix for classification only
    canonical = normalize_symbol(symbol)

    if canonical in FX_SYMBOLS:
        return "fx"
    if canonical in METAL_SYMBOLS:
        return "metal"
    if canonical in ENERGY_SYMBOLS:
        return "energy"
    if canonical in INDEX_SYMBOLS:
        return "index"
    if canonical in CRYPTO_SYMBOLS:
        return "crypto"

    return "cfd"


# ─────────────────────────────────────────────────────────────────────────────
# PRECISION AND INCREMENT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def resolve_price_precision(symbol: str, digits: int) -> int:
    """
    Return the price precision (decimal places) for a symbol.

    Strips broker suffixes before checking overrides so that
    "XAUUSDm" correctly resolves to the XAUUSD override of 2.

    Uses PRICE_PRECISION_OVERRIDES first, then MT5's symbol_info().digits.
    """
    canonical = normalize_symbol(symbol)
    return PRICE_PRECISION_OVERRIDES.get(canonical, digits)


def make_price_increment(price_precision: int) -> Price:
    """
    Build the minimum price increment (tick size) from price precision.

    Examples:
        precision=5  ->  Price(0.00001, 5)   [EURUSD]
        precision=2  ->  Price(0.01,    2)   [XAUUSD]
        precision=1  ->  Price(0.1,     1)   [US500]
        precision=0  ->  Price(1.0,     0)   [rare]
    """
    increment = 10 ** -price_precision
    return Price(increment, price_precision)


def make_size_increment(volume_step: float) -> tuple[Quantity, int]:
    """
    Derive the size increment and size precision from MT5's volume_step.

    MT5 volume_step is the minimum lot change (e.g. 0.01 = micro lots).
    Returns (size_increment, size_precision).

    Examples:
        volume_step=0.01  ->  (Quantity(0.01, 2), 2)
        volume_step=0.1   ->  (Quantity(0.1,  1), 1)
        volume_step=1.0   ->  (Quantity(1.0,  0), 0)
    """
    # Count decimal places in volume_step
    step_str = f"{volume_step:.10f}".rstrip("0")
    if "." in step_str:
        size_precision = len(step_str.split(".")[1])
    else:
        size_precision = 0

    return Quantity(volume_step, size_precision), size_precision


def make_margin(margin_value: float) -> Decimal:
    """
    Convert MT5 margin percentage (0–100) to NautilusTrader Decimal (0.0–1.0).

    MT5 stores margin_initial as a percentage: 3.0 means 3%.
    NautilusTrader expects a fraction: 0.03 means 3%.

    If the value is 0 (no margin data from broker), use a safe default of 1%
    to avoid division-by-zero issues in NautilusTrader's margin engine.
    """
    if margin_value <= 0:
        return Decimal("0.01")  # safe 1% default
    # MT5 can return 100.0 for 100% margin (no leverage on this symbol)
    # NautilusTrader expects fraction, so divide by 100
    fraction = margin_value / 100.0
    return Decimal(str(round(fraction, 6)))


# ─────────────────────────────────────────────────────────────────────────────
# CURRENCY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def parse_currency(code: str) -> Currency:
    """
    Parse a currency code string into a NautilusTrader Currency object.

    Handles edge cases:
    - Empty string -> raises MT5InstrumentError
    - Unknown crypto codes (e.g. 'BTC', 'ETH') -> uses Currency.from_str()
      which handles crypto codes natively in NautilusTrader

    """
    code = code.strip().upper()
    if not code:
        raise MT5InstrumentError(
            "Empty currency code. Check symbol_info().currency_base/profit."
        )
    try:
        return Currency.from_str(code)
    except Exception as exc:
        raise MT5InstrumentError(
            f"Cannot parse currency code '{code}': {exc}"
        ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# MAIN INSTRUMENT PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_symbol_info(info) -> InstrumentAny:
    """
    Convert an MT5 symbol_info() namedtuple into a NautilusTrader instrument.

    This is the main entry point called by MT5InstrumentProvider for each symbol.

    Parameters
    ----------
    info : MT5 SymbolInfo namedtuple
        The raw object returned by mt5.symbol_info(symbol).

    Returns
    -------
    CurrencyPair | Cfd | CryptoPerpetual

    Raises
    ------
    MT5InstrumentError
        If required fields are missing or unparseable.
    """
    if info is None:
        raise MT5InstrumentError(
            "mt5.symbol_info() returned None. "
            "Check the symbol exists and is added to Market Watch."
        )

    symbol = info.name
    instrument_type = detect_instrument_type(symbol)

    # Resolve precision
    price_precision = resolve_price_precision(symbol, info.digits)
    price_increment = make_price_increment(price_precision)
    size_increment, size_precision = make_size_increment(info.volume_step)

    # Build identifiers
    instrument_id = InstrumentId(Symbol(symbol), MT5_VENUE)
    raw_symbol    = Symbol(symbol)

    # Lot sizes
    min_qty = Quantity(info.volume_min,  size_precision)
    max_qty = Quantity(info.volume_max,  size_precision)
    lot_size = Quantity(info.trade_contract_size, 0) if info.trade_contract_size >= 1 else Quantity(1.0, 0)

    # Margins
    margin_init  = make_margin(info.margin_initial)
    margin_maint = make_margin(info.margin_maintenance)

    # Timestamp
    ts_now = time.time_ns()

    if instrument_type == "fx":
        return _parse_fx(
            instrument_id, raw_symbol, info,
            price_precision, price_increment,
            size_precision, size_increment,
            min_qty, max_qty, lot_size,
            margin_init, margin_maint, ts_now,
        )

    elif instrument_type == "crypto":
        return _parse_crypto(
            instrument_id, raw_symbol, info,
            price_precision, price_increment,
            size_precision, size_increment,
            min_qty, max_qty,
            margin_init, margin_maint, ts_now,
        )

    else:
        # metal, energy, index, cfd — all become Cfd
        return _parse_cfd(
            instrument_id, raw_symbol, info,
            instrument_type,
            price_precision, price_increment,
            size_precision, size_increment,
            min_qty, max_qty, lot_size,
            margin_init, margin_maint, ts_now,
        )


# ─────────────────────────────────────────────────────────────────────────────
# PER-TYPE BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_fx(
    instrument_id, raw_symbol, info,
    price_precision, price_increment,
    size_precision, size_increment,
    min_qty, max_qty, lot_size,
    margin_init, margin_maint, ts_now,
) -> CurrencyPair:
    """Build a CurrencyPair for FX spot symbols (EURUSD, GBPUSD, etc.)"""
    try:
        base_currency  = parse_currency(info.currency_base)
        quote_currency = parse_currency(info.currency_profit)
    except MT5InstrumentError as exc:
        raise MT5InstrumentError(
            f"FX symbol '{info.name}' has invalid currency: {exc}"
        ) from exc

    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=raw_symbol,
        base_currency=base_currency,
        quote_currency=quote_currency,
        price_precision=price_precision,
        size_precision=size_precision,
        price_increment=price_increment,
        size_increment=size_increment,
        lot_size=lot_size,
        max_quantity=max_qty,
        min_quantity=min_qty,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=margin_init,
        margin_maint=margin_maint,
        maker_fee=Decimal("0"),
        taker_fee=Decimal("0"),
        ts_event=ts_now,
        ts_init=ts_now,
    )


def _parse_cfd(
    instrument_id, raw_symbol, info,
    instrument_type,
    price_precision, price_increment,
    size_precision, size_increment,
    min_qty, max_qty, lot_size,
    margin_init, margin_maint, ts_now,
) -> Cfd:
    """Build a Cfd for metals, energies, indices, and unknown CFDs."""
    # Map instrument type to NautilusTrader AssetClass
    asset_class_map = {
        "metal":  AssetClass.COMMODITY,
        "energy": AssetClass.COMMODITY,
        "index":  AssetClass.INDEX,
        "cfd":    AssetClass.ALTERNATIVE,
    }
    asset_class = asset_class_map.get(instrument_type, AssetClass.ALTERNATIVE)

    try:
        quote_currency = parse_currency(info.currency_profit)
    except MT5InstrumentError as exc:
        raise MT5InstrumentError(
            f"CFD symbol '{info.name}' has invalid quote currency: {exc}"
        ) from exc

    return Cfd(
        instrument_id=instrument_id,
        raw_symbol=raw_symbol,
        asset_class=asset_class,
        quote_currency=quote_currency,
        price_precision=price_precision,
        size_precision=size_precision,
        price_increment=price_increment,
        size_increment=size_increment,
        lot_size=lot_size,
        max_quantity=max_qty,
        min_quantity=min_qty,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=margin_init,
        margin_maint=margin_maint,
        maker_fee=Decimal("0"),
        taker_fee=Decimal("0"),
        ts_event=ts_now,
        ts_init=ts_now,
    )


def _parse_crypto(
    instrument_id, raw_symbol, info,
    price_precision, price_increment,
    size_precision, size_increment,
    min_qty, max_qty,
    margin_init, margin_maint, ts_now,
) -> CryptoPerpetual:
    """Build a CryptoPerpetual for crypto CFD symbols (BTCUSD, ETHUSD, etc.)"""
    try:
        base_currency       = parse_currency(info.currency_base)
        quote_currency      = parse_currency(info.currency_profit)
        settlement_currency = parse_currency(info.currency_profit)
    except MT5InstrumentError as exc:
        raise MT5InstrumentError(
            f"Crypto symbol '{info.name}' has invalid currency: {exc}"
        ) from exc

    return CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=raw_symbol,
        base_currency=base_currency,
        quote_currency=quote_currency,
        settlement_currency=settlement_currency,
        is_inverse=False,  # Exness crypto CFDs are all linear (USD-settled)
        price_precision=price_precision,
        size_precision=size_precision,
        price_increment=price_increment,
        size_increment=size_increment,
        max_quantity=max_qty,
        min_quantity=min_qty,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=margin_init,
        margin_maint=margin_maint,
        maker_fee=Decimal("0"),
        taker_fee=Decimal("0"),
        ts_event=ts_now,
        ts_init=ts_now,
    )


# ─────────────────────────────────────────────────────────────────────────────
# TICK PARSER  (used by MT5DataClient polling loop)
# ─────────────────────────────────────────────────────────────────────────────

def parse_quote_tick(symbol_info_tick, instrument: InstrumentAny) -> QuoteTick:
    """
    Convert an MT5 tick into a NautilusTrader QuoteTick.

    Handles two sources:
    - mt5.symbol_info_tick(symbol)   → namedtuple  (live polling)
    - mt5.copy_ticks_range(...)      → numpy structured array row (downloader)

    Both are accessed the same way: numpy void supports both
    attribute-style (row.bid) and key-style (row["bid"]) access
    via numpy's structured array interface. We use key-style to be
    safe with both numpy rows and MagicMock objects in tests.

    Parameters
    ----------
    symbol_info_tick : MT5 Tick namedtuple or numpy.void row
        Has fields: bid, ask, time (epoch seconds).
    instrument : CurrencyPair | Cfd | CryptoPerpetual
        The instrument this tick belongs to (for precision info).

    Returns
    -------
    QuoteTick
    """
    pp = instrument.price_precision

    # numpy structured array rows (from copy_ticks_range) are numpy.void type
    # namedtuples and MagicMocks use attribute access
    if type(symbol_info_tick).__name__ == "void":
        # numpy structured array row — use key access
        bid  = float(symbol_info_tick["bid"])
        ask  = float(symbol_info_tick["ask"])
        ts_s = int(symbol_info_tick["time"])
    else:
        # namedtuple (live polling) or MagicMock (tests) — use attribute access
        bid  = float(symbol_info_tick.bid)
        ask  = float(symbol_info_tick.ask)
        ts_s = int(symbol_info_tick.time)

    return QuoteTick(
        instrument_id=instrument.id,
        bid_price=Price(bid, pp),
        ask_price=Price(ask, pp),
        bid_size=Quantity(1_000_000, 0),   # MT5 doesn't expose depth
        ask_size=Quantity(1_000_000, 0),
        ts_event=ts_s * 1_000_000_000,     # seconds → nanoseconds
        ts_init=time.time_ns(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# BAR PARSER  (used by MT5DataClient and downloader.py)
# ─────────────────────────────────────────────────────────────────────────────

# Map MT5 timeframe integers to NautilusTrader BarAggregation + step
_MT5_TIMEFRAME_MAP: dict[int, tuple[int, BarAggregation]] = {
    1:     (1,  BarAggregation.MINUTE),   # M1
    2:     (2,  BarAggregation.MINUTE),   # M2
    3:     (3,  BarAggregation.MINUTE),   # M3
    4:     (4,  BarAggregation.MINUTE),   # M4
    5:     (5,  BarAggregation.MINUTE),   # M5
    6:     (6,  BarAggregation.MINUTE),   # M6
    10:    (10, BarAggregation.MINUTE),   # M10
    12:    (12, BarAggregation.MINUTE),   # M12
    15:    (15, BarAggregation.MINUTE),   # M15
    20:    (20, BarAggregation.MINUTE),   # M20
    30:    (30, BarAggregation.MINUTE),   # M30
    16385: (1,  BarAggregation.HOUR),     # H1
    16386: (2,  BarAggregation.HOUR),     # H2
    16387: (3,  BarAggregation.HOUR),     # H3
    16388: (4,  BarAggregation.HOUR),     # H4
    16390: (6,  BarAggregation.HOUR),     # H6
    16392: (8,  BarAggregation.HOUR),     # H8
    16396: (12, BarAggregation.HOUR),     # H12
    16408: (1,  BarAggregation.DAY),      # D1
    32769: (1,  BarAggregation.WEEK),     # W1
    49153: (1,  BarAggregation.MONTH),    # MN1
}


def parse_bar(mt5_rate, instrument: InstrumentAny, timeframe: int) -> Bar:
    """
    Convert one row from mt5.copy_rates_range() into a NautilusTrader Bar.

    Parameters
    ----------
    mt5_rate : numpy structured array row
        Fields: time, open, high, low, close, tick_volume, spread, real_volume
    instrument : InstrumentAny
        For instrument_id and price_precision.
    timeframe : int
        MT5 timeframe constant (e.g. mt5.TIMEFRAME_H1 = 16385).

    Returns
    -------
    Bar
    """
    step, aggregation = _MT5_TIMEFRAME_MAP.get(
        timeframe, (1, BarAggregation.DAY)  # safe fallback
    )
    pp = instrument.price_precision

    bar_type = BarType(
        instrument_id=instrument.id,
        bar_spec=BarSpecification(step, aggregation, PriceType.LAST),
    )

    ts_event = int(mt5_rate["time"]) * 1_000_000_000  # seconds → nanoseconds

    return Bar(
        bar_type=bar_type,
        open=Price(mt5_rate["open"],  pp),
        high=Price(mt5_rate["high"],  pp),
        low=Price(mt5_rate["low"],    pp),
        close=Price(mt5_rate["close"], pp),
        volume=Quantity(float(mt5_rate["tick_volume"]), 0),
        ts_event=ts_event,
        ts_init=time.time_ns(),
    )