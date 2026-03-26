"""
nautilus_mt5/factories.py

MT5LiveDataClientFactory and MT5LiveExecClientFactory — the glue between
NautilusTrader's component wiring system and the MT5 adapter clients.

NautilusTrader uses these factories to instantiate data and execution
clients inside its LiveTradingNode. You register them in your node config
and NautilusTrader calls them at startup.

Usage (typical)
---------------
    from mt5connect.config   import MT5Config
    from mt5connect.factories import build_mt5_node_config

    config = MT5Config(
        account=12345678,
        password="your_password",
        server="Exness-MT5Trial9",
        symbols=["EURUSDm", "XAUUSDm"],
    )

    node_config = build_mt5_node_config(config)  # <-- single call, that's it

    node = TradingNode(config=node_config)
    node.trader.add_strategy(YourStrategy(config=YourStrategyConfig(...)))
    node.run()

Advanced (manual wiring)
------------------------
    from mt5connect.factories import MT5LiveDataClientFactory, MT5LiveExecClientFactory

    data_config = LiveDataEngineConfig(
        data_client_configs={
            MT5_VENUE: DataClientConfig(factory=MT5LiveDataClientFactory(...)),
        }
    )

See examples/live_simple_strategy.py for a complete runnable example.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.config import (
    LiveDataClientConfig,
    LiveExecClientConfig,
    LiveRiskEngineConfig,
    RoutingConfig,
    TradingNodeConfig,
    InstrumentProviderConfig,
)
from nautilus_trader.live.factories import LiveDataClientFactory, LiveExecClientFactory
from nautilus_trader.model.identifiers import AccountId

from mt5connect.config import MT5Config
from mt5connect.connection import MT5Connection
from mt5connect.constants import MT5_VENUE
from mt5connect.data import MT5DataClient
from mt5connect.execution import MT5LiveExecutionClient
from mt5connect.providers import MT5InstrumentProvider

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SHARED CONNECTION REGISTRY
#
# Both factories need to share a single MT5Connection and
# MT5InstrumentProvider instance — one IPC channel to the terminal,
# one instrument cache. This registry creates them once per (config, loop)
# pair and reuses them on subsequent calls.
# ─────────────────────────────────────────────────────────────────────────────

_connection_registry: dict[int, tuple[MT5Connection, MT5InstrumentProvider]] = {}

# Side-channel registry for MT5Config + factory refs, keyed by venue string.
# Used by build_mt5_node_config() since NT's config structs are frozen (msgspec)
# and don't accept arbitrary extra kwargs like `factory` or `custom`.
_mt5_config_registry: dict[str, dict] = {}


def _get_or_create_connection(
    config: MT5Config,
    loop: asyncio.AbstractEventLoop,
) -> tuple[MT5Connection, MT5InstrumentProvider]:
    """
    Return the shared MT5Connection and MT5InstrumentProvider for this config.
    Creates and connects them on first call; reuses on subsequent calls.

    The registry key is based on account + server to allow multiple adapters
    (e.g. two brokers) to coexist in the same NautilusTrader node.
    """
    registry_key = hash((config.account, config.server))

    if registry_key not in _connection_registry:
        logger.info(
            f"MT5 factories: creating connection for account={config.account} "
            f"server={config.server}"
        )
        conn     = MT5Connection(config)
        provider = MT5InstrumentProvider(conn)

        conn.connect()

        _connection_registry[registry_key] = (conn, provider)
        logger.info(f"MT5 factories: connection established — {conn}")

    return _connection_registry[registry_key]


def clear_connection_registry() -> None:
    """
    Remove all cached connections. Useful in tests and when
    restarting a node without process restart.

    Calls disconnect() on each registered connection before clearing.
    """
    for conn, _ in _connection_registry.values():
        try:
            conn.disconnect()
        except Exception:
            pass
    _connection_registry.clear()


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLIENT FACTORY
# ─────────────────────────────────────────────────────────────────────────────

class MT5LiveDataClientFactory(LiveDataClientFactory):
    """
    Factory that builds MT5DataClient instances for NautilusTrader.

    NautilusTrader calls create() once per venue during node startup.
    The factory grabs (or creates) the shared MT5Connection and provider,
    then constructs the data client.

    You don't instantiate this class directly — pass it to
    LiveDataClientConfig or use build_mt5_node_config() for convenience.

    Parameters
    ----------
    config : MT5Config
        Your broker / account configuration.
    """

    def __init__(self, config: MT5Config) -> None:
        self._config = config

    @classmethod
    def create(
        cls,
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: "LiveDataClientConfig",
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> MT5DataClient:
        """
        Called by NautilusTrader's engine during node startup.

        Parameters
        ----------
        loop    : asyncio event loop
        name    : client name string (e.g. "MT5")
        config  : LiveDataClientConfig carrying our MT5Config in .custom
        msgbus  : NautilusTrader message bus
        cache   : NautilusTrader cache
        clock   : NautilusTrader live clock

        Returns
        -------
        MT5DataClient
        """
        mt5_config: MT5Config = (
            config.custom["mt5_config"]
            if hasattr(config, "custom") and isinstance(getattr(config, "custom", None), dict)
            else _mt5_config_registry.get(MT5_VENUE.value, {}).get("mt5_config")
        )
        if mt5_config is None:
            raise RuntimeError("MT5LiveDataClientFactory: no MT5Config found.")
        conn, provider = _get_or_create_connection(mt5_config, loop)

        return MT5DataClient(
            loop=loop,
            connection=conn,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=mt5_config,
        )


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION CLIENT FACTORY
# ─────────────────────────────────────────────────────────────────────────────

class MT5LiveExecClientFactory(LiveExecClientFactory):
    """
    Factory that builds MT5LiveExecutionClient instances for NautilusTrader.

    Works identically to MT5LiveDataClientFactory — grabs the shared
    connection, constructs the execution client.

    Parameters
    ----------
    config : MT5Config
        Your broker / account configuration.
    """

    def __init__(self, config: MT5Config) -> None:
        self._config = config

    @classmethod
    def create(
        cls,
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: "LiveExecClientConfig",
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> MT5LiveExecutionClient:
        """
        Called by NautilusTrader's engine during node startup.

        Parameters
        ----------
        loop    : asyncio event loop
        name    : client name string (e.g. "MT5")
        config  : LiveExecClientConfig carrying our MT5Config in .custom
        msgbus  : NautilusTrader message bus
        cache   : NautilusTrader cache
        clock   : NautilusTrader live clock

        Returns
        -------
        MT5LiveExecutionClient
        """
        mt5_config: MT5Config = (
            config.custom["mt5_config"]
            if hasattr(config, "custom") and isinstance(getattr(config, "custom", None), dict)
            else _mt5_config_registry.get(MT5_VENUE.value, {}).get("mt5_config")
        )
        if mt5_config is None:
            raise RuntimeError("MT5LiveExecClientFactory: no MT5Config found.")
        conn, provider = _get_or_create_connection(mt5_config, loop)

        account_id = AccountId(f"MT5-{mt5_config.account}")

        return MT5LiveExecutionClient(
            loop=loop,
            connection=conn,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=mt5_config,
            account_id=account_id,
        )


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_mt5_node_config(
    mt5_config: MT5Config,
    risk_engine_config: LiveRiskEngineConfig | None = None,
    logging_config=None,
) -> TradingNodeConfig:
    """
    Build a complete NautilusTrader TradingNodeConfig for MT5.

    This is the highest-level convenience function — it wires up the
    data engine, execution engine, instrument provider, and factories
    from a single MT5Config.

    Parameters
    ----------
    mt5_config : MT5Config
        Your broker / account / symbol configuration.
    risk_engine_config : LiveRiskEngineConfig, optional
        Custom risk engine config. If None, uses NautilusTrader defaults.
    logging_config : optional
        Custom logging config. If None, uses NautilusTrader defaults.

    Returns
    -------
    TradingNodeConfig
        Pass directly to TradingNode(config=...).

    Notes
    -----
    NautilusTrader 1.224+ requires strategies to be added via
    node.trader.add_strategy(instance) AFTER node construction. Do NOT pass
    (StrategyClass, StrategyConfig) tuples into TradingNodeConfig.

    Examples
    --------
        node = TradingNode(config=build_mt5_node_config(mt5_config))
        node.trader.add_strategy(YourStrategy(config=YourStrategyConfig(...)))
        node.run()
    """
    venue_str = MT5_VENUE.value  # "MT5"

    # ── Side-channel registry (used by factory create() methods) ─────────────
    _mt5_config_registry[venue_str] = {
        "mt5_config":    mt5_config,
        "data_factory":  MT5LiveDataClientFactory,
        "exec_factory":  MT5LiveExecClientFactory,
        "account_id":    f"MT5-{mt5_config.account}",
        "load_ids":      [f"{s}.{venue_str}" for s in mt5_config.symbols],
    }

    # ── Client configs — tell NT that an MT5 client exists for this venue ─────
    # RoutingConfig(default=True) means all unrouted data/orders go to MT5.
    # The factory classes are registered on the node via
    # node.add_data_client_factory() / node.add_exec_client_factory()
    # in live_simple_strategy.py (or wherever the node is constructed).
    # TradingNodeConfig just needs the client config stubs so build() knows
    # which names to iterate over.
    data_client_cfg = LiveDataClientConfig(
        instrument_provider=InstrumentProviderConfig(load_all=True),
        routing=RoutingConfig(default=True, venues=frozenset({venue_str})),
    )
    exec_client_cfg = LiveExecClientConfig(
        instrument_provider=InstrumentProviderConfig(load_all=True),
        routing=RoutingConfig(default=True, venues=frozenset({venue_str})),
    )

    # ── Assemble TradingNodeConfig ────────────────────────────────────────────
    kwargs: dict = {
        "data_clients": {venue_str: data_client_cfg},
        "exec_clients": {venue_str: exec_client_cfg},
    }
    if risk_engine_config is not None:
        kwargs["risk_engine"] = risk_engine_config
    if logging_config is not None:
        kwargs["logging"] = logging_config

    return TradingNodeConfig(**kwargs)
