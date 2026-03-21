"""
tests/test_factories.py

Tests for MT5LiveDataClientFactory, MT5LiveExecClientFactory,
_get_or_create_connection, clear_connection_registry, and
build_mt5_node_config.
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch, call

from nautilus_mt5.config import MT5Config
from nautilus_mt5.factories import (
    MT5LiveDataClientFactory,
    MT5LiveExecClientFactory,
    _get_or_create_connection,
    _connection_registry,
    clear_connection_registry,
    build_mt5_node_config,
)
from nautilus_mt5.data import MT5DataClient
from nautilus_mt5.execution import MT5LiveExecutionClient
from nautilus_mt5.constants import MT5_VENUE


def make_config(account=12345678, server="Exness-MT5Trial1", symbols=None):
    return MT5Config(
        account=account,
        password="test_password",
        server=server,
        symbols=symbols or ["EURUSD"],
    )


def make_nt_components():
    from nautilus_trader.test_kit.stubs.component import TestComponentStubs
    from nautilus_trader.common.component import LiveClock
    return TestComponentStubs.msgbus(), TestComponentStubs.cache(), LiveClock()


def make_live_data_config(mt5_config):
    cfg = MagicMock()
    cfg.custom = {"mt5_config": mt5_config}
    return cfg


def make_live_exec_config(mt5_config):
    cfg = MagicMock()
    cfg.custom = {"mt5_config": mt5_config}
    return cfg


@pytest.fixture(autouse=True)
def clean_registry():
    from nautilus_mt5.factories import _mt5_config_registry
    clear_connection_registry()
    _mt5_config_registry.clear()
    yield
    clear_connection_registry()
    _mt5_config_registry.clear()


@pytest.fixture
def mock_mt5_conn():
    with patch("nautilus_mt5.factories.MT5Connection") as MockConn:
        instance = MagicMock()
        instance.connect    = MagicMock()
        instance.disconnect = MagicMock()
        instance.is_connected = True
        instance.ensure_connected = MagicMock()
        MockConn.return_value = instance
        yield MockConn, instance


@pytest.fixture
def mock_provider():
    from nautilus_mt5.providers import MT5InstrumentProvider as RealProvider
    from nautilus_trader.common.providers import InstrumentProvider

    real_instance = RealProvider.__new__(RealProvider)
    InstrumentProvider.__init__(real_instance)
    real_instance._conn = MagicMock()
    real_instance._failed_symbols = []
    real_instance.get_instrument = MagicMock(return_value=None)

    with patch("nautilus_mt5.factories.MT5InstrumentProvider") as MockProv:
        MockProv.return_value = real_instance
        yield MockProv, real_instance


class TestGetOrCreateConnection:
    def test_creates_connection_on_first_call(self, mock_mt5_conn, mock_provider):
        MockConn, _ = mock_mt5_conn
        config = make_config()
        loop   = asyncio.new_event_loop()
        _get_or_create_connection(config, loop)
        MockConn.assert_called_once_with(config)

    def test_calls_connect_on_first_call(self, mock_mt5_conn, mock_provider):
        _, conn_inst = mock_mt5_conn
        config = make_config()
        loop   = asyncio.new_event_loop()
        _get_or_create_connection(config, loop)
        conn_inst.connect.assert_called_once()

    def test_reuses_connection_on_second_call(self, mock_mt5_conn, mock_provider):
        config = make_config()
        loop   = asyncio.new_event_loop()
        conn1, prov1 = _get_or_create_connection(config, loop)
        conn2, prov2 = _get_or_create_connection(config, loop)
        assert conn1 is conn2
        assert prov1 is prov2

    def test_connect_called_only_once_for_same_config(self, mock_mt5_conn, mock_provider):
        _, conn_inst = mock_mt5_conn
        config = make_config()
        loop   = asyncio.new_event_loop()
        _get_or_create_connection(config, loop)
        _get_or_create_connection(config, loop)
        conn_inst.connect.assert_called_once()

    def test_returns_tuple_of_connection_and_provider(self, mock_mt5_conn, mock_provider):
        _, conn_inst = mock_mt5_conn
        _, prov_inst = mock_provider
        config = make_config()
        loop   = asyncio.new_event_loop()
        conn, provider = _get_or_create_connection(config, loop)
        assert conn     is conn_inst
        assert provider is prov_inst


class TestConnectionRegistryIsolation:
    def test_different_accounts_get_separate_connections(self, mock_mt5_conn, mock_provider):
        MockConn, _ = mock_mt5_conn
        loop = asyncio.new_event_loop()
        _get_or_create_connection(make_config(account=11111111), loop)
        _get_or_create_connection(make_config(account=22222222), loop)
        assert MockConn.call_count == 2

    def test_different_servers_get_separate_connections(self, mock_mt5_conn, mock_provider):
        MockConn, _ = mock_mt5_conn
        loop = asyncio.new_event_loop()
        _get_or_create_connection(make_config(server="BrokerA-Demo"), loop)
        _get_or_create_connection(make_config(server="BrokerB-Demo"), loop)
        assert MockConn.call_count == 2

    def test_same_account_different_server_is_separate(self, mock_mt5_conn, mock_provider):
        MockConn, _ = mock_mt5_conn
        loop = asyncio.new_event_loop()
        _get_or_create_connection(make_config(account=12345678, server="ServerA"), loop)
        _get_or_create_connection(make_config(account=12345678, server="ServerB"), loop)
        assert MockConn.call_count == 2

    def test_registry_grows_with_each_new_config(self, mock_mt5_conn, mock_provider):
        loop = asyncio.new_event_loop()
        _get_or_create_connection(make_config(account=10000001), loop)
        _get_or_create_connection(make_config(account=10000002), loop)
        _get_or_create_connection(make_config(account=10000003), loop)
        assert len(_connection_registry) == 3


class TestClearConnectionRegistry:
    def test_clears_all_entries(self, mock_mt5_conn, mock_provider):
        config = make_config()
        loop   = asyncio.new_event_loop()
        _get_or_create_connection(config, loop)
        assert len(_connection_registry) == 1
        clear_connection_registry()
        assert len(_connection_registry) == 0

    def test_calls_disconnect_on_each_connection(self, mock_mt5_conn, mock_provider):
        _, conn_inst = mock_mt5_conn
        config = make_config()
        loop   = asyncio.new_event_loop()
        _get_or_create_connection(config, loop)
        clear_connection_registry()
        conn_inst.disconnect.assert_called_once()

    def test_safe_to_call_when_already_empty(self):
        clear_connection_registry()
        clear_connection_registry()

    def test_disconnect_exception_does_not_prevent_clear(self, mock_mt5_conn, mock_provider):
        _, conn_inst = mock_mt5_conn
        conn_inst.disconnect.side_effect = RuntimeError("oops")
        config = make_config()
        loop   = asyncio.new_event_loop()
        _get_or_create_connection(config, loop)
        clear_connection_registry()
        assert len(_connection_registry) == 0

    def test_new_connection_created_after_clear(self, mock_mt5_conn, mock_provider):
        MockConn, _ = mock_mt5_conn
        config = make_config()
        loop   = asyncio.new_event_loop()
        _get_or_create_connection(config, loop)
        clear_connection_registry()
        _get_or_create_connection(config, loop)
        assert MockConn.call_count == 2


class TestDataClientFactory:
    def test_create_returns_mt5_data_client(self, mock_mt5_conn, mock_provider):
        config = make_config()
        loop   = asyncio.new_event_loop()
        msgbus, cache, clock = make_nt_components()
        client = MT5LiveDataClientFactory.create(loop, "MT5", make_live_data_config(config), msgbus, cache, clock)
        assert isinstance(client, MT5DataClient)

    def test_create_reads_mt5_config_from_custom(self, mock_mt5_conn, mock_provider):
        config = make_config(account=99998888)
        loop   = asyncio.new_event_loop()
        msgbus, cache, clock = make_nt_components()
        client = MT5LiveDataClientFactory.create(loop, "MT5", make_live_data_config(config), msgbus, cache, clock)
        assert client._config.account == 99998888

    def test_create_uses_registry_connection(self, mock_mt5_conn, mock_provider):
        _, conn_inst = mock_mt5_conn
        config = make_config()
        loop   = asyncio.new_event_loop()
        msgbus, cache, clock = make_nt_components()
        client = MT5LiveDataClientFactory.create(loop, "MT5", make_live_data_config(config), msgbus, cache, clock)
        assert client._conn is conn_inst

    def test_create_twice_uses_same_connection(self, mock_mt5_conn, mock_provider):
        config = make_config()
        loop   = asyncio.new_event_loop()
        msgbus, cache, clock = make_nt_components()
        c1 = MT5LiveDataClientFactory.create(loop, "MT5", make_live_data_config(config), msgbus, cache, clock)
        msgbus2, cache2, clock2 = make_nt_components()
        c2 = MT5LiveDataClientFactory.create(loop, "MT5", make_live_data_config(config), msgbus2, cache2, clock2)
        assert c1._conn is c2._conn


class TestExecClientFactory:
    def test_create_returns_mt5_exec_client(self, mock_mt5_conn, mock_provider):
        config = make_config()
        loop   = asyncio.new_event_loop()
        msgbus, cache, clock = make_nt_components()
        client = MT5LiveExecClientFactory.create(loop, "MT5", make_live_exec_config(config), msgbus, cache, clock)
        assert isinstance(client, MT5LiveExecutionClient)

    def test_create_reads_mt5_config_from_custom(self, mock_mt5_conn, mock_provider):
        config = make_config(account=77776666)
        loop   = asyncio.new_event_loop()
        msgbus, cache, clock = make_nt_components()
        client = MT5LiveExecClientFactory.create(loop, "MT5", make_live_exec_config(config), msgbus, cache, clock)
        assert client._config.account == 77776666

    def test_create_uses_registry_connection(self, mock_mt5_conn, mock_provider):
        _, conn_inst = mock_mt5_conn
        config = make_config()
        loop   = asyncio.new_event_loop()
        msgbus, cache, clock = make_nt_components()
        client = MT5LiveExecClientFactory.create(loop, "MT5", make_live_exec_config(config), msgbus, cache, clock)
        assert client._conn is conn_inst

    def test_account_id_derived_from_config(self, mock_mt5_conn, mock_provider):
        config = make_config(account=55554444)
        loop   = asyncio.new_event_loop()
        msgbus, cache, clock = make_nt_components()
        client = MT5LiveExecClientFactory.create(loop, "MT5", make_live_exec_config(config), msgbus, cache, clock)
        # NT 1.224: account_id lives on the parent C-level property, not _account_id
        assert client.account_id.value == "MT5-55554444"


class TestFactoriesShareConnection:
    def test_data_and_exec_factories_share_connection(self, mock_mt5_conn, mock_provider):
        _, conn_inst = mock_mt5_conn
        config = make_config()
        loop   = asyncio.new_event_loop()
        msgbus, cache, clock   = make_nt_components()
        msgbus2, cache2, clock2 = make_nt_components()
        data_client = MT5LiveDataClientFactory.create(loop, "MT5", make_live_data_config(config), msgbus, cache, clock)
        exec_client = MT5LiveExecClientFactory.create(loop, "MT5", make_live_exec_config(config), msgbus2, cache2, clock2)
        assert data_client._conn is exec_client._conn

    def test_connection_created_only_once_for_both_factories(self, mock_mt5_conn, mock_provider):
        MockConn, _ = mock_mt5_conn
        config = make_config()
        loop   = asyncio.new_event_loop()
        msgbus, cache, clock   = make_nt_components()
        msgbus2, cache2, clock2 = make_nt_components()
        MT5LiveDataClientFactory.create(loop, "MT5", make_live_data_config(config), msgbus, cache, clock)
        MT5LiveExecClientFactory.create(loop, "MT5", make_live_exec_config(config), msgbus2, cache2, clock2)
        assert MockConn.call_count == 1


class TestBuildMt5NodeConfig:

    def test_returns_trading_node_config(self):
        from nautilus_trader.config import TradingNodeConfig
        result = build_mt5_node_config(make_config(symbols=["EURUSD", "XAUUSD"]))
        assert isinstance(result, TradingNodeConfig)

    def test_registry_keyed_by_venue_string(self):
        from nautilus_mt5.factories import _mt5_config_registry
        build_mt5_node_config(make_config())
        assert "MT5" in _mt5_config_registry

    def test_data_factory_stored_in_registry(self):
        from nautilus_mt5.factories import _mt5_config_registry
        build_mt5_node_config(make_config())
        assert _mt5_config_registry["MT5"]["data_factory"] is MT5LiveDataClientFactory

    def test_exec_factory_stored_in_registry(self):
        from nautilus_mt5.factories import _mt5_config_registry
        build_mt5_node_config(make_config())
        assert _mt5_config_registry["MT5"]["exec_factory"] is MT5LiveExecClientFactory

    def test_mt5_config_stored_in_registry(self):
        from nautilus_mt5.factories import _mt5_config_registry
        config = make_config()
        build_mt5_node_config(config)
        assert _mt5_config_registry["MT5"]["mt5_config"] is config

    def test_account_id_stored_in_registry(self):
        from nautilus_mt5.factories import _mt5_config_registry
        build_mt5_node_config(make_config(account=12345678))
        assert _mt5_config_registry["MT5"]["account_id"] == "MT5-12345678"

    def test_load_ids_stored_in_registry(self):
        from nautilus_mt5.factories import _mt5_config_registry
        build_mt5_node_config(make_config(symbols=["EURUSD", "XAUUSD"]))
        assert "EURUSD.MT5" in _mt5_config_registry["MT5"]["load_ids"]
        assert "XAUUSD.MT5" in _mt5_config_registry["MT5"]["load_ids"]

    def test_load_ids_count_matches_symbols(self):
        from nautilus_mt5.factories import _mt5_config_registry
        build_mt5_node_config(make_config(symbols=["EURUSD", "XAUUSD", "BTCUSD"]))
        assert len(_mt5_config_registry["MT5"]["load_ids"]) == 3

    def test_single_symbol_load_ids(self):
        from nautilus_mt5.factories import _mt5_config_registry
        build_mt5_node_config(make_config(symbols=["EURUSD"]))
        assert _mt5_config_registry["MT5"]["load_ids"] == ["EURUSD.MT5"]

    def test_optional_risk_engine_config_is_wired(self):
        from nautilus_trader.config import LiveRiskEngineConfig
        risk_config = LiveRiskEngineConfig()
        result = build_mt5_node_config(make_config(), risk_engine_config=risk_config)
        assert result.risk_engine is risk_config

    def test_strategies_kwarg_not_accepted(self):
        # NT 1.224+: strategies must be added via node.trader.add_strategy(instance)
        # AFTER node construction. build_mt5_node_config never accepts strategies.
        with pytest.raises(TypeError, match="strategies"):
            build_mt5_node_config(make_config(), strategies=[MagicMock()])

    def test_node_config_has_no_strategies_by_default(self):
        # TradingNodeConfig should carry no strategies —
        # wired in by caller via node.trader.add_strategy().
        result = build_mt5_node_config(make_config())
        assert not result.strategies

    def test_data_clients_keyed_by_venue(self):
        result = build_mt5_node_config(make_config())
        assert "MT5" in result.data_clients

    def test_exec_clients_keyed_by_venue(self):
        result = build_mt5_node_config(make_config())
        assert "MT5" in result.exec_clients

    def test_data_client_routing_is_default(self):
        result = build_mt5_node_config(make_config())
        assert result.data_clients["MT5"].routing.default is True

    def test_exec_client_routing_is_default(self):
        result = build_mt5_node_config(make_config())
        assert result.exec_clients["MT5"].routing.default is True

    def test_registry_updated_on_repeated_call(self):
        from nautilus_mt5.factories import _mt5_config_registry
        config1 = make_config(account=11111111)
        config2 = make_config(account=22222222)
        build_mt5_node_config(config1)
        build_mt5_node_config(config2)
        assert _mt5_config_registry["MT5"]["mt5_config"] is config2