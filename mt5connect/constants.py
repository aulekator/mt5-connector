"""
nautilus_mt5/constants.py

All fixed values for the nautilus-mt5 adapter.
No logic here — just constants referenced across all modules.
"""

from nautilus_trader.model.identifiers import Venue


# ─────────────────────────────────────────────────────────────────────────────
# VENUE
# ─────────────────────────────────────────────────────────────────────────────

MT5_VENUE = Venue("MT5")

# Used as the unique identifier for orders placed by this adapter.
# Change this if you run multiple bots simultaneously to avoid collisions.
MT5_MAGIC_NUMBER: int = 510


# ─────────────────────────────────────────────────────────────────────────────
# POLLING
# ─────────────────────────────────────────────────────────────────────────────

# Default interval (milliseconds) for the live tick polling loop.
# 100ms = 10 ticks/second per symbol — balanced between freshness and CPU usage.
DEFAULT_POLL_INTERVAL_MS: int = 100

# Interval (milliseconds) for polling open positions to detect fills.
DEFAULT_EXEC_POLL_INTERVAL_MS: int = 250


# ─────────────────────────────────────────────────────────────────────────────
# RECONNECT
# ─────────────────────────────────────────────────────────────────────────────

RECONNECT_INITIAL_DELAY_S: float = 1.0
RECONNECT_MAX_DELAY_S: float     = 60.0
RECONNECT_MULTIPLIER: float      = 2.0
RECONNECT_MAX_ATTEMPTS: int      = 20


# ─────────────────────────────────────────────────────────────────────────────
# MT5 ORDER FILLING MODES
# ─────────────────────────────────────────────────────────────────────────────

import MetaTrader5 as mt5  # noqa: E402

FILLING_MODE = mt5.ORDER_FILLING_IOC


# ─────────────────────────────────────────────────────────────────────────────
# BROKER SYMBOL SUFFIXES
#
# Different brokers append suffixes to standard symbol names:
#   Exness standard accounts  → "m"   (EURUSDm, XAUUSDm)
#   Exness zero/raw accounts  → no suffix (EURUSD)
#   IC Markets                → no suffix (EURUSD)
#   Pepperstone               → no suffix (EURUSD)
#   Some brokers              → "." prefix (EURUSD., XAUUSD.)
#   Some brokers              → "_SB" suffix
#
# The adapter strips these suffixes ONLY for instrument type detection
# (FX vs metal vs crypto etc). The original broker symbol name is always
# preserved for all actual MT5 API calls.
# ─────────────────────────────────────────────────────────────────────────────

# Known suffixes to strip for classification purposes only.
# Lowercase — comparison is done after lowercasing the suffix portion.
BROKER_SYMBOL_SUFFIXES: tuple[str, ...] = (
    "m",     # Exness standard accounts (EURUSDm)
    "c",     # Some brokers (EURUSDc)
    "_sb",   # Spread betting variants
    ".",     # Trailing dot (EURUSD.)
    "_raw",  # Raw spread accounts
    "_ecn",  # ECN accounts
    "_pro",  # Pro accounts
)

# Known prefixes to strip for classification
BROKER_SYMBOL_PREFIXES: tuple[str, ...] = (
    ".",     # Leading dot (.EURUSD on some brokers)
)


def normalize_symbol(symbol: str) -> str:
    """
    Strip broker-specific suffixes/prefixes to get the canonical symbol name
    used for instrument type classification.

    This ONLY affects classification — the original symbol string is always
    used for actual MT5 API calls (symbol_info, copy_ticks_range, etc.)

    Examples
    --------
    normalize_symbol("EURUSDm")   → "EURUSD"   (Exness standard)
    normalize_symbol("XAUUSDm")   → "XAUUSD"   (Exness gold)
    normalize_symbol("BTCUSDm")   → "BTCUSD"   (Exness crypto)
    normalize_symbol("EURUSD.")   → "EURUSD"   (trailing dot)
    normalize_symbol("EURUSDc")   → "EURUSD"   (c suffix)
    normalize_symbol("EURUSD")    → "EURUSD"   (no suffix, unchanged)
    normalize_symbol("USTECm")    → "USTEC"    (Exness US100)
    normalize_symbol("BTCJPYm")   → "BTCJPY"   (cross crypto)
    """
    s = symbol.strip()

    # Strip known prefixes first
    for prefix in BROKER_SYMBOL_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break

    # Strip known suffixes (case-insensitive match on the suffix portion)
    for suffix in BROKER_SYMBOL_SUFFIXES:
        if s.lower().endswith(suffix.lower()) and len(s) > len(suffix):
            s = s[: len(s) - len(suffix)]
            break

    return s.upper()


# ─────────────────────────────────────────────────────────────────────────────
# SYMBOL TYPE SETS
# These use CANONICAL (no-suffix) names only.
# Always compare against normalize_symbol(your_broker_symbol).
# ─────────────────────────────────────────────────────────────────────────────

FX_SYMBOLS: frozenset[str] = frozenset({
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD",
    "USDCAD", "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY",
    "CHFJPY", "EURCHF", "EURAUD", "EURNZD", "EURCAD", "GBPAUD",
    "GBPNZD", "GBPCAD", "AUDNZD", "AUDCAD", "AUDCHF", "NZDCAD",
    "NZDCHF", "NZDJPY", "CADCHF", "GBPCHF", "USDNOK", "USDSEK",
    "USDDKK", "USDSGD", "USDHKD", "USDMXN", "USDTRY", "USDZAR",
    "USDCNH", "EURTRY", "EURZAR", "EURHUF", "EURPLN", "EURCZK",
    "USDRUB",
    # Additional cross pairs
    "EURSGD", "GBPSGD", "AUDSGD", "CADSGD",
    "EURHKD", "GBPHKD",
    "EURMXN", "GBPMXN",
})

METAL_SYMBOLS: frozenset[str] = frozenset({
    "XAUUSD",  # Gold
    "XAGUSD",  # Silver
    "XPTUSD",  # Platinum
    "XPDUSD",  # Palladium
    "XAUEUR",  # Gold vs EUR
    "XAGEUR",  # Silver vs EUR
    "XAUJPY",  # Gold vs JPY
    "XAUGBP",  # Gold vs GBP
})

ENERGY_SYMBOLS: frozenset[str] = frozenset({
    "USOIL", "UKOIL", "XBRUSD", "XTIUSD", "NGAS",
    "BRENT", "WTI",
    # Exness specific energy names
    "CRUDOIL",
})

INDEX_SYMBOLS: frozenset[str] = frozenset({
    "US500", "US30", "US100", "UK100", "DE40", "FR40",
    "JP225", "AU200", "EU50", "ES35", "HK50",
    "USDX",   # Dollar Index
    "USTEC",  # Exness name for US100/Nasdaq
    "SPXUSD", "NSXUSD", "DJUSD",
    "STOXX50",
    # Exness index names
    "AUS200", "GER40",
})

CRYPTO_SYMBOLS: frozenset[str] = frozenset({
    "BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD", "BCHUSD",
    "EOSUSD", "XLMUSD", "ADAUSD", "DOTUSD", "SOLUSD",
    "DOGEUSD", "MATICUSD", "LINKUSD", "UNIUSD", "AVAXUSD",
    # Cross-currency crypto (Exness)
    "BTCJPY", "BTCKRW", "BTCEUR", "BTCGBP",
    "ETHJPY", "ETHEUR",
    # Additional coins on Exness
    "AAVEUSD", "BATUSD", "FTTUSD",
})


# ─────────────────────────────────────────────────────────────────────────────
# PRICE PRECISION OVERRIDES
# Canonical (no-suffix) symbol names only.
# normalize_symbol() is applied before lookup.
# ─────────────────────────────────────────────────────────────────────────────

PRICE_PRECISION_OVERRIDES: dict[str, int] = {
    "XAUUSD": 2,
    "XAGUSD": 3,
    "BTCUSD": 2,
    "ETHUSD": 2,
    "US500":  1,
    "US30":   1,
    "US100":  1,
    "DE40":   1,
    "USTEC":  1,  # Exness US100
    "UK100":  1,
    "JP225":  0,
    "AU200":  1,
    "HK50":   0,
}
