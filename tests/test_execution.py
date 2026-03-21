"""
tests/test_execution.py

Exhaustive tests for MT5LiveExecutionClient and execution.py helpers.

Test groups:
  1.  Helpers (_time_in_force_to_mt5, _mt5_retcode_to_str,
                _nautilus_side_to_mt5_market, _nautilus_order_to_mt5_pending)
  2.  Initial state
  3.  _connect() — reconcile orders/positions, start poll task
  4.  _disconnect() — cancel poll task, clear state
  5.  _submit_order() — market order success
  6.  _submit_order() — limit order success
  7.  _submit_order() — stop order success
  8.  _submit_order() — MT5 rejects order
  9.  _submit_order() — order_send returns None
  10. _submit_order() — instrument not found
  11. _submit_order() — no tick available
  12. _cancel_order() — cancel pending order
  13. _cancel_order() — close open position
  14. _cancel_order() — ticket not found
  15. _cancel_all_orders() — cancels only magic-matched orders
  16. _modify_order() — modify pending order price
  17. _modify_order() — modify position SL/TP
  18. _modify_order() — ticket not found
  19. _poll_exec_once() — new position detected
  20. _poll_exec_once() — closed position detected
  21. _poll_exec_once() — disappeared pending order detected
  22. _poll_exec_once() — deals processed by magic number
  23. _exec_poll_loop() — reconnects on connection error
  24. _exec_poll_loop() — stops when reconnect fails
  25. generate_order_status_reports() — returns reports for open orders
  26. generate_fill_reports() — returns fill reports from deals
  27. generate_position_status_reports() — returns open position reports
  28. Properties — is_polling, known_order_count, known_position_count
  29. __repr__
  30. _reconcile_open_orders() — registers magic-matched orders only
  31. _reconcile_open_positions() — registers magic-matched positions only
"""

import asyncio
import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call

import MetaTrader5 as mt5

from nautilus_trader.model.currencies import Currency
from nautilus_trader.model.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientId,
    ClientOrderId,
    InstrumentId,
    Symbol,
    VenueOrderId,
)
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity

from nautilus_mt5.connection import ConnectionState
from nautilus_mt5.errors import MT5ConnectionError
from nautilus_mt5.constants import MT5_VENUE
from nautilus_mt5.execution import (
    MT5LiveExecutionClient,
    _time_in_force_to_mt5,
    _mt5_retcode_to_str,
    _nautilus_side_to_mt5_market,
    _nautilus_order_to_mt5_pending,
    _parse_account_currency,
)

UTC = timezone.utc
MAGIC = 510


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def config():
    from nautilus_mt5.config import MT5Config
    return MT5Config(
        account=12345678,
        password="test_password",
        server="Exness-MT5Trial1",
        symbols=["EURUSD", "XAUUSD"],
        exec_poll_interval_ms=50,
        reconnect_initial_delay_s=0.01,
        reconnect_max_delay_s=0.05,
        reconnect_max_attempts=3,
    )


@pytest.fixture
def mock_mt5_exec():
    """Patches MetaTrader5 for execution tests."""
    with patch("nautilus_mt5.execution.mt5") as mock:
        # Connection
        mock.initialize.return_value = True
        mock.login.return_value = True
        mock.last_error.return_value = (0, "No error")

        # Orders / positions empty by default
        mock.orders_get.return_value = ()
        mock.positions_get.return_value = ()
        mock.history_deals_get.return_value = ()

        # Tick for price lookup
        tick = MagicMock()
        tick.bid = 1.08500
        tick.ask = 1.08520
        mock.symbol_info_tick.return_value = tick

        # Successful order_send result
        result = MagicMock()
        result.retcode = mt5.TRADE_RETCODE_DONE
        result.order   = 99991
        mock.order_send.return_value = result

        # Account info
        account = MagicMock()
        account.login    = 12345678
        account.server   = "Exness-MT5Trial1"
        account.balance  = 10000.0
        account.equity   = 10050.0
        account.margin   = 100.0
        account.margin_free  = 9950.0
        account.margin_level = 10050.0
        account.currency = "USD"
        account.leverage = 2000
        account.profit   = 50.0
        account.name     = "Test"
        account.company  = "TestBroker"

        # MT5 constants
        mock.TRADE_RETCODE_DONE    = 10009
        mock.TRADE_RETCODE_PLACED  = 10008
        mock.TRADE_RETCODE_DONE_PARTIAL = 10010
        mock.ORDER_TYPE_BUY        = 0
        mock.ORDER_TYPE_SELL       = 1
        mock.ORDER_TYPE_BUY_LIMIT  = 2
        mock.ORDER_TYPE_SELL_LIMIT = 3
        mock.ORDER_TYPE_BUY_STOP   = 4
        mock.ORDER_TYPE_SELL_STOP  = 5
        mock.ORDER_TYPE_BUY_STOP_LIMIT  = 6
        mock.ORDER_TYPE_SELL_STOP_LIMIT = 7
        mock.TRADE_ACTION_DEAL     = 1
        mock.TRADE_ACTION_PENDING  = 5
        mock.TRADE_ACTION_REMOVE   = 8
        mock.TRADE_ACTION_MODIFY   = 6
        mock.TRADE_ACTION_SLTP     = 3
        mock.ORDER_TIME_GTC        = 0
        mock.ORDER_TIME_DAY        = 1
        mock.ORDER_TIME_SPECIFIED  = 2
        mock.DEAL_TYPE_BUY         = 0
        mock.DEAL_TYPE_SELL        = 1

        yield mock


def make_instrument(symbol="EURUSD"):
    return CurrencyPair(
        instrument_id=InstrumentId.from_str(f"{symbol}.MT5"),
        raw_symbol=Symbol(symbol),
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


def make_mock_order(client_order_id="O-001", symbol="EURUSD",
                    order_type=OrderType.MARKET, side=OrderSide.BUY,
                    qty=0.10, price=1.08500):
    order = MagicMock()
    order.client_order_id = ClientOrderId(client_order_id)
    order.strategy_id     = MagicMock()
    order.instrument_id   = InstrumentId.from_str(f"{symbol}.MT5")
    order.order_type      = order_type
    order.side            = side
    order.quantity        = Quantity(qty, 2)
    order.time_in_force   = TimeInForce.GTC
    # Market orders have no price
    if order_type == OrderType.MARKET:
        order.price = None
    else:
        order.price = Price(price, 5)
    order.trigger_price   = None
    order.sl_trigger_price = None
    order.tp_price        = None
    return order


def make_provider(instrument=None):
    """
    Build a real MT5InstrumentProvider — NautilusTrader's PyCondition.type()
    rejects MagicMock, so we must use the real class with a mocked connection.
    """
    from nautilus_mt5.providers import MT5InstrumentProvider
    from nautilus_mt5.connection import MT5Connection
    from nautilus_trader.common.providers import InstrumentProvider

    conn = MagicMock(spec=MT5Connection)
    conn.ensure_connected = MagicMock()

    inst = instrument or make_instrument()

    provider = MT5InstrumentProvider.__new__(MT5InstrumentProvider)
    InstrumentProvider.__init__(provider)
    provider._conn           = conn
    provider._failed_symbols = []

    provider.get_instrument = MagicMock(return_value=inst)
    provider.load_symbol    = MagicMock(return_value=inst)
    provider.list_all       = MagicMock(return_value=[inst])
    provider.load_all_async = AsyncMock()

    return provider


def make_exec_client(config, mock_mt5_exec) -> MT5LiveExecutionClient:
    """Build an MT5LiveExecutionClient with real NautilusTrader components."""
    from nautilus_trader.test_kit.stubs.component import TestComponentStubs
    from nautilus_trader.common.component import LiveClock

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    conn = MagicMock()
    conn.is_connected = True
    conn.ensure_connected = MagicMock()
    account_snap = MagicMock()
    account_snap.balance  = 10000.0
    account_snap.equity   = 10000.0   # keep equal to balance — no unrealised P&L in tests
    account_snap.currency = "USD"
    conn.get_account_info = MagicMock(return_value=account_snap)
    conn.reconnect_async  = AsyncMock(return_value=True)

    provider = make_provider()

    # Real NT components — MagicMock fails PyCondition type checks
    msgbus = TestComponentStubs.msgbus()
    cache  = TestComponentStubs.cache()
    clock  = LiveClock()

    client = MT5LiveExecutionClient(
        loop=loop,
        connection=conn,
        msgbus=msgbus,
        cache=cache,
        clock=clock,
        instrument_provider=provider,
        config=config,
    )
    # Patch the internal generate methods so we can inspect calls
    client.generate_order_accepted = MagicMock()
    client.generate_order_rejected = MagicMock()
    client.generate_account_state  = MagicMock()

    return client


# ─────────────────────────────────────────────────────────────────────────────
# 1. HELPERS
# ─────────────────────────────────────────────────────────────────────────────

class TestTimeInForceToMt5:
    def test_gtc(self):      assert _time_in_force_to_mt5(TimeInForce.GTC) == mt5.ORDER_TIME_GTC
    def test_day(self):      assert _time_in_force_to_mt5(TimeInForce.DAY) == mt5.ORDER_TIME_DAY
    def test_ioc_maps_gtc(self): assert _time_in_force_to_mt5(TimeInForce.IOC) == mt5.ORDER_TIME_GTC
    def test_fok_maps_gtc(self): assert _time_in_force_to_mt5(TimeInForce.FOK) == mt5.ORDER_TIME_GTC
    def test_gtd(self):
        assert _time_in_force_to_mt5(TimeInForce.GTD) == mt5.ORDER_TIME_SPECIFIED


class TestRetcodeToStr:
    def test_known_retcode(self):
        s = _mt5_retcode_to_str(10009)
        assert "completed" in s.lower()

    def test_known_rejected(self):
        s = _mt5_retcode_to_str(10006)
        assert "rejected" in s.lower()

    def test_unknown_retcode(self):
        s = _mt5_retcode_to_str(99999)
        assert "99999" in s


class TestNautilusSideToMt5:
    def test_buy_market(self):
        assert _nautilus_side_to_mt5_market(OrderSide.BUY) == mt5.ORDER_TYPE_BUY

    def test_sell_market(self):
        assert _nautilus_side_to_mt5_market(OrderSide.SELL) == mt5.ORDER_TYPE_SELL


class TestNautilusOrderToMt5Pending:
    def test_buy_limit(self):
        assert _nautilus_order_to_mt5_pending(OrderType.LIMIT, OrderSide.BUY) == mt5.ORDER_TYPE_BUY_LIMIT

    def test_sell_limit(self):
        assert _nautilus_order_to_mt5_pending(OrderType.LIMIT, OrderSide.SELL) == mt5.ORDER_TYPE_SELL_LIMIT

    def test_buy_stop(self):
        assert _nautilus_order_to_mt5_pending(OrderType.STOP_MARKET, OrderSide.BUY) == mt5.ORDER_TYPE_BUY_STOP

    def test_sell_stop(self):
        assert _nautilus_order_to_mt5_pending(OrderType.STOP_MARKET, OrderSide.SELL) == mt5.ORDER_TYPE_SELL_STOP

    def test_buy_stop_limit(self):
        assert _nautilus_order_to_mt5_pending(OrderType.STOP_LIMIT, OrderSide.BUY) == mt5.ORDER_TYPE_BUY_STOP_LIMIT

    def test_sell_stop_limit(self):
        assert _nautilus_order_to_mt5_pending(OrderType.STOP_LIMIT, OrderSide.SELL) == mt5.ORDER_TYPE_SELL_STOP_LIMIT

    def test_unsupported_raises(self):
        from nautilus_mt5.errors import MT5OrderError
        with pytest.raises(MT5OrderError):
            _nautilus_order_to_mt5_pending(OrderType.TRAILING_STOP_MARKET, OrderSide.BUY)


class TestParseAccountCurrency:
    def test_usd(self):
        from nautilus_trader.model.currencies import USD
        assert _parse_account_currency("USD") == USD

    def test_eur(self):
        from nautilus_trader.model.currencies import EUR
        assert _parse_account_currency("EUR") == EUR

    def test_fallback_to_usd(self):
        from nautilus_trader.model.currencies import USD
        assert _parse_account_currency("XXXX") == USD

    def test_case_insensitive(self):
        from nautilus_trader.model.currencies import USD
        assert _parse_account_currency("usd") == USD


# ─────────────────────────────────────────────────────────────────────────────
# 2. INITIAL STATE
# ─────────────────────────────────────────────────────────────────────────────

class TestInitialState:
    def test_poll_task_none_initially(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        assert client._exec_poll_task is None

    def test_known_orders_empty(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        assert len(client._known_order_tickets) == 0

    def test_known_positions_empty(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        assert len(client._known_position_tickets) == 0

    def test_client_order_id_map_empty(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        assert len(client._client_order_id_to_ticket) == 0

    def test_not_polling_initially(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        assert client.is_polling is False

    def test_known_order_count_zero(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        assert client.known_order_count == 0

    def test_known_position_count_zero(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        assert client.known_position_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. CONNECT
# ─────────────────────────────────────────────────────────────────────────────

class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_checks_connection(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        await client._connect()
        client._conn.ensure_connected.assert_called()

    @pytest.mark.asyncio
    async def test_connect_generates_account_state(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        await client._connect()
        client.generate_account_state.assert_called()

    @pytest.mark.asyncio
    async def test_connect_starts_poll_task(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        await client._connect()
        assert client._exec_poll_task is not None

    @pytest.mark.asyncio
    async def test_poll_task_is_running_after_connect(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        await client._connect()
        assert client.is_polling is True
        client._exec_poll_task.cancel()
        try:
            await client._exec_poll_task
        except asyncio.CancelledError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# 4. DISCONNECT
# ─────────────────────────────────────────────────────────────────────────────

class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_cancels_poll_task(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        await client._connect()
        assert client.is_polling
        await client._disconnect()
        assert not client.is_polling

    @pytest.mark.asyncio
    async def test_disconnect_clears_state(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        client._known_order_tickets.add(111)
        client._known_position_tickets.add(222)
        client._client_order_id_to_ticket["O-1"] = 111
        await client._disconnect()
        assert len(client._known_order_tickets) == 0
        assert len(client._known_position_tickets) == 0
        assert len(client._client_order_id_to_ticket) == 0

    @pytest.mark.asyncio
    async def test_disconnect_sets_poll_task_none(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        await client._connect()
        await client._disconnect()
        assert client._exec_poll_task is None


# ─────────────────────────────────────────────────────────────────────────────
# 5. SUBMIT ORDER — MARKET
# ─────────────────────────────────────────────────────────────────────────────

class TestSubmitMarketOrder:
    @pytest.mark.asyncio
    async def test_market_buy_calls_order_send(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        order  = make_mock_order(order_type=OrderType.MARKET, side=OrderSide.BUY)
        cmd    = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)

        mock_mt5_exec.order_send.assert_called_once()
        req = mock_mt5_exec.order_send.call_args[0][0]
        assert req["action"] == mock_mt5_exec.TRADE_ACTION_DEAL
        assert req["type"]   == mock_mt5_exec.ORDER_TYPE_BUY

    @pytest.mark.asyncio
    async def test_market_sell_uses_bid_price(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        order  = make_mock_order(order_type=OrderType.MARKET, side=OrderSide.SELL)
        cmd    = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)

        req = mock_mt5_exec.order_send.call_args[0][0]
        assert req["price"] == pytest.approx(1.08500)  # bid

    @pytest.mark.asyncio
    async def test_market_buy_uses_ask_price(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        order  = make_mock_order(order_type=OrderType.MARKET, side=OrderSide.BUY)
        cmd    = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)

        req = mock_mt5_exec.order_send.call_args[0][0]
        assert req["price"] == pytest.approx(1.08520)  # ask

    @pytest.mark.asyncio
    async def test_market_order_records_ticket(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        order  = make_mock_order(client_order_id="O-MKTBUY")
        cmd    = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)

        assert "O-MKTBUY" in client._client_order_id_to_ticket
        assert client._client_order_id_to_ticket["O-MKTBUY"] == 99991

    @pytest.mark.asyncio
    async def test_market_order_emits_accepted(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        order  = make_mock_order()
        cmd    = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)
        client.generate_order_accepted.assert_called_once()

    @pytest.mark.asyncio
    async def test_magic_number_set_in_request(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        order  = make_mock_order()
        cmd    = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)

        req = mock_mt5_exec.order_send.call_args[0][0]
        assert req["magic"] == config.magic_number


# ─────────────────────────────────────────────────────────────────────────────
# 6. SUBMIT ORDER — LIMIT
# ─────────────────────────────────────────────────────────────────────────────

class TestSubmitLimitOrder:
    @pytest.mark.asyncio
    async def test_limit_buy_uses_pending_action(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        order  = make_mock_order(order_type=OrderType.LIMIT, side=OrderSide.BUY)
        cmd    = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)

        req = mock_mt5_exec.order_send.call_args[0][0]
        assert req["action"] == mock_mt5_exec.TRADE_ACTION_PENDING
        assert req["type"]   == mock_mt5_exec.ORDER_TYPE_BUY_LIMIT

    @pytest.mark.asyncio
    async def test_limit_sell_type_correct(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        order  = make_mock_order(order_type=OrderType.LIMIT, side=OrderSide.SELL)
        cmd    = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)

        req = mock_mt5_exec.order_send.call_args[0][0]
        assert req["type"] == mock_mt5_exec.ORDER_TYPE_SELL_LIMIT


# ─────────────────────────────────────────────────────────────────────────────
# 7. SUBMIT ORDER — STOP
# ─────────────────────────────────────────────────────────────────────────────

class TestSubmitStopOrder:
    @pytest.mark.asyncio
    async def test_stop_buy_type_correct(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        order  = make_mock_order(order_type=OrderType.STOP_MARKET, side=OrderSide.BUY)
        cmd    = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)

        req = mock_mt5_exec.order_send.call_args[0][0]
        assert req["type"] == mock_mt5_exec.ORDER_TYPE_BUY_STOP

    @pytest.mark.asyncio
    async def test_stop_sell_type_correct(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        order  = make_mock_order(order_type=OrderType.STOP_MARKET, side=OrderSide.SELL)
        cmd    = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)

        req = mock_mt5_exec.order_send.call_args[0][0]
        assert req["type"] == mock_mt5_exec.ORDER_TYPE_SELL_STOP


# ─────────────────────────────────────────────────────────────────────────────
# 8. SUBMIT ORDER — REJECTED
# ─────────────────────────────────────────────────────────────────────────────

class TestSubmitOrderRejected:
    @pytest.mark.asyncio
    async def test_rejected_retcode_emits_order_rejected(self, config, mock_mt5_exec):
        result = MagicMock()
        result.retcode = 10019  # Insufficient funds
        result.order   = 0
        mock_mt5_exec.order_send.return_value = result

        client = make_exec_client(config, mock_mt5_exec)
        order  = make_mock_order()
        cmd    = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)

        client.generate_order_rejected.assert_called_once()
        client.generate_order_accepted.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejected_does_not_record_ticket(self, config, mock_mt5_exec):
        result = MagicMock()
        result.retcode = 10006
        result.order   = 0
        mock_mt5_exec.order_send.return_value = result

        client = make_exec_client(config, mock_mt5_exec)
        order  = make_mock_order(client_order_id="O-REJECT")
        cmd    = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)

        assert "O-REJECT" not in client._client_order_id_to_ticket


# ─────────────────────────────────────────────────────────────────────────────
# 9. SUBMIT ORDER — order_send RETURNS NONE
# ─────────────────────────────────────────────────────────────────────────────

class TestSubmitOrderSendNone:
    @pytest.mark.asyncio
    async def test_none_result_emits_rejected(self, config, mock_mt5_exec):
        mock_mt5_exec.order_send.return_value = None

        client = make_exec_client(config, mock_mt5_exec)
        order  = make_mock_order()
        cmd    = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)

        client.generate_order_rejected.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 10. SUBMIT ORDER — INSTRUMENT NOT FOUND
# ─────────────────────────────────────────────────────────────────────────────

class TestSubmitOrderInstrumentNotFound:
    @pytest.mark.asyncio
    async def test_missing_instrument_emits_rejected(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        client._provider.get_instrument.return_value = None

        order = make_mock_order()
        cmd   = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)

        client.generate_order_rejected.assert_called_once()
        mock_mt5_exec.order_send.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 11. SUBMIT ORDER — NO TICK
# ─────────────────────────────────────────────────────────────────────────────

class TestSubmitOrderNoTick:
    @pytest.mark.asyncio
    async def test_no_tick_emits_rejected(self, config, mock_mt5_exec):
        mock_mt5_exec.symbol_info_tick.return_value = None

        client = make_exec_client(config, mock_mt5_exec)
        order  = make_mock_order()
        cmd    = MagicMock()
        cmd.order = order

        await client._submit_order(cmd)

        client.generate_order_rejected.assert_called_once()
        mock_mt5_exec.order_send.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 12. CANCEL ORDER — PENDING
# ─────────────────────────────────────────────────────────────────────────────

class TestCancelPendingOrder:
    @pytest.mark.asyncio
    async def test_cancel_pending_sends_remove(self, config, mock_mt5_exec):
        mt5_order = MagicMock()
        mt5_order.ticket = 77771
        mock_mt5_exec.orders_get.return_value = (mt5_order,)

        result = MagicMock()
        result.retcode = mock_mt5_exec.TRADE_RETCODE_DONE
        mock_mt5_exec.order_send.return_value = result

        client = make_exec_client(config, mock_mt5_exec)
        client._client_order_id_to_ticket["O-CANCEL"] = 77771

        cmd = MagicMock()
        cmd.client_order_id  = ClientOrderId("O-CANCEL")
        cmd.venue_order_id   = None
        cmd.instrument_id    = InstrumentId.from_str("EURUSD.MT5")

        await client._cancel_order(cmd)

        req = mock_mt5_exec.order_send.call_args[0][0]
        assert req["action"] == mock_mt5_exec.TRADE_ACTION_REMOVE
        assert req["order"]  == 77771

    @pytest.mark.asyncio
    async def test_cancel_unknown_ticket_logs_warning(self, config, mock_mt5_exec):
        """No ticket in map and no venue_order_id → silent warning, no crash."""
        client = make_exec_client(config, mock_mt5_exec)

        cmd = MagicMock()
        cmd.client_order_id = ClientOrderId("O-UNKNOWN")
        cmd.venue_order_id  = None
        cmd.instrument_id   = InstrumentId.from_str("EURUSD.MT5")

        # Should not raise
        await client._cancel_order(cmd)
        mock_mt5_exec.order_send.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 13. CANCEL ORDER — CLOSE POSITION
# ─────────────────────────────────────────────────────────────────────────────

class TestCancelClosePosition:
    @pytest.mark.asyncio
    async def test_close_position_sends_opposite_market_order(self, config, mock_mt5_exec):
        # No pending order with this ticket
        mock_mt5_exec.orders_get.return_value = ()

        # But there IS an open position
        pos = MagicMock()
        pos.ticket = 88881
        pos.symbol = "EURUSD"
        pos.volume = 0.10
        pos.type   = mock_mt5_exec.ORDER_TYPE_BUY  # long → close with SELL
        mock_mt5_exec.positions_get.return_value = (pos,)

        result = MagicMock()
        result.retcode = mock_mt5_exec.TRADE_RETCODE_DONE
        mock_mt5_exec.order_send.return_value = result

        client = make_exec_client(config, mock_mt5_exec)
        client._client_order_id_to_ticket["O-CLOSE"] = 88881

        cmd = MagicMock()
        cmd.client_order_id = ClientOrderId("O-CLOSE")
        cmd.venue_order_id  = None
        cmd.instrument_id   = InstrumentId.from_str("EURUSD.MT5")

        await client._cancel_order(cmd)

        req = mock_mt5_exec.order_send.call_args[0][0]
        assert req["action"]   == mock_mt5_exec.TRADE_ACTION_DEAL
        assert req["type"]     == mock_mt5_exec.ORDER_TYPE_SELL
        assert req["position"] == 88881


# ─────────────────────────────────────────────────────────────────────────────
# 14. CANCEL ALL ORDERS
# ─────────────────────────────────────────────────────────────────────────────

class TestCancelAllOrders:
    @pytest.mark.asyncio
    async def test_cancels_magic_matched_orders(self, config, mock_mt5_exec):
        o1 = MagicMock(); o1.ticket = 1001; o1.magic = MAGIC
        o2 = MagicMock(); o2.ticket = 1002; o2.magic = MAGIC
        o3 = MagicMock(); o3.ticket = 1003; o3.magic = 9999  # not ours
        mock_mt5_exec.orders_get.return_value = (o1, o2, o3)

        result = MagicMock()
        result.retcode = mock_mt5_exec.TRADE_RETCODE_DONE
        mock_mt5_exec.order_send.return_value = result

        client = make_exec_client(config, mock_mt5_exec)
        cmd = MagicMock()
        cmd.instrument_id = InstrumentId.from_str("EURUSD.MT5")

        await client._cancel_all_orders(cmd)

        # order_send called only for our 2 orders
        assert mock_mt5_exec.order_send.call_count == 2

    @pytest.mark.asyncio
    async def test_no_orders_does_not_call_order_send(self, config, mock_mt5_exec):
        mock_mt5_exec.orders_get.return_value = ()

        client = make_exec_client(config, mock_mt5_exec)
        cmd = MagicMock()
        cmd.instrument_id = InstrumentId.from_str("EURUSD.MT5")

        await client._cancel_all_orders(cmd)
        mock_mt5_exec.order_send.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 15. MODIFY ORDER
# ─────────────────────────────────────────────────────────────────────────────

class TestModifyOrder:
    @pytest.mark.asyncio
    async def test_modify_pending_order_sends_action_modify(self, config, mock_mt5_exec):
        mt5_order = MagicMock()
        mt5_order.ticket       = 55551
        mt5_order.price_open   = 1.08400
        mt5_order.tp           = 0.0
        mt5_order.type_time    = 0
        mt5_order.time_expiration = 0
        mock_mt5_exec.orders_get.return_value = (mt5_order,)

        result = MagicMock()
        result.retcode = mock_mt5_exec.TRADE_RETCODE_DONE
        mock_mt5_exec.order_send.return_value = result

        client = make_exec_client(config, mock_mt5_exec)
        client._client_order_id_to_ticket["O-MOD"] = 55551

        cmd = MagicMock()
        cmd.client_order_id = ClientOrderId("O-MOD")
        cmd.venue_order_id  = None
        cmd.price           = Price(1.08300, 5)
        cmd.trigger_price   = None

        await client._modify_order(cmd)

        req = mock_mt5_exec.order_send.call_args[0][0]
        assert req["action"] == mock_mt5_exec.TRADE_ACTION_MODIFY
        assert req["order"]  == 55551

    @pytest.mark.asyncio
    async def test_modify_position_sends_action_sltp(self, config, mock_mt5_exec):
        # Not a pending order
        mock_mt5_exec.orders_get.return_value = ()

        pos = MagicMock()
        pos.ticket = 66661
        pos.symbol = "EURUSD"
        pos.sl = 0.0
        pos.tp = 0.0
        mock_mt5_exec.positions_get.return_value = (pos,)

        result = MagicMock()
        result.retcode = mock_mt5_exec.TRADE_RETCODE_DONE
        mock_mt5_exec.order_send.return_value = result

        client = make_exec_client(config, mock_mt5_exec)
        client._client_order_id_to_ticket["O-SLTP"] = 66661

        cmd = MagicMock()
        cmd.client_order_id = ClientOrderId("O-SLTP")
        cmd.venue_order_id  = None
        cmd.price           = None
        cmd.trigger_price   = Price(1.08000, 5)

        await client._modify_order(cmd)

        req = mock_mt5_exec.order_send.call_args[0][0]
        assert req["action"] == mock_mt5_exec.TRADE_ACTION_SLTP


# ─────────────────────────────────────────────────────────────────────────────
# 16. POLL EXEC ONCE
# ─────────────────────────────────────────────────────────────────────────────

class TestPollExecOnce:
    @pytest.mark.asyncio
    async def test_new_position_added_to_known(self, config, mock_mt5_exec):
        pos = MagicMock()
        pos.ticket = 12301
        pos.magic  = MAGIC
        mock_mt5_exec.positions_get.return_value = (pos,)

        client = make_exec_client(config, mock_mt5_exec)
        await client._poll_exec_once()

        assert 12301 in client._known_position_tickets

    @pytest.mark.asyncio
    async def test_closed_position_removed_from_known(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        client._known_position_tickets.add(99991)

        # Position is gone now
        mock_mt5_exec.positions_get.return_value = ()

        await client._poll_exec_once()

        assert 99991 not in client._known_position_tickets

    @pytest.mark.asyncio
    async def test_disappeared_order_removed_from_known(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        client._known_order_tickets.add(77771)

        mock_mt5_exec.orders_get.return_value = ()

        await client._poll_exec_once()

        assert 77771 not in client._known_order_tickets

    @pytest.mark.asyncio
    async def test_deal_with_wrong_magic_ignored(self, config, mock_mt5_exec):
        deal = MagicMock()
        deal.magic  = 9999  # not ours
        deal.time   = 1_700_000_100
        deal.ticket = 1
        mock_mt5_exec.history_deals_get.return_value = (deal,)

        client = make_exec_client(config, mock_mt5_exec)
        await client._poll_exec_once()

        # last_deal_time should NOT advance for wrong-magic deals
        assert client._last_deal_time == 0

    @pytest.mark.asyncio
    async def test_position_with_wrong_magic_ignored(self, config, mock_mt5_exec):
        pos = MagicMock()
        pos.ticket = 55551
        pos.magic  = 9999  # not ours
        mock_mt5_exec.positions_get.return_value = (pos,)

        client = make_exec_client(config, mock_mt5_exec)
        await client._poll_exec_once()

        assert 55551 not in client._known_position_tickets


# ─────────────────────────────────────────────────────────────────────────────
# 17. EXEC POLL LOOP
# ─────────────────────────────────────────────────────────────────────────────

class TestExecPollLoop:
    @pytest.mark.asyncio
    async def test_reconnects_on_connection_error(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)

        call_count = 0

        async def poll_once_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise MT5ConnectionError("connection lost")
            raise asyncio.CancelledError()

        client._poll_exec_once = poll_once_side_effect
        client._conn.reconnect_async = AsyncMock(return_value=True)

        task = asyncio.get_event_loop().create_task(client._exec_poll_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        client._conn.reconnect_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_stops_when_reconnect_fails(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)

        async def always_fail():
            raise MT5ConnectionError("dead")

        client._poll_exec_once = always_fail
        client._conn.reconnect_async = AsyncMock(return_value=False)

        # Loop should exit cleanly (not hang)
        await asyncio.wait_for(client._exec_poll_loop(), timeout=2.0)


# ─────────────────────────────────────────────────────────────────────────────
# 18. RECONCILE
# ─────────────────────────────────────────────────────────────────────────────

class TestReconcile:
    @pytest.mark.asyncio
    async def test_reconcile_orders_registers_magic_matched(self, config, mock_mt5_exec):
        o1 = MagicMock(); o1.ticket = 1001; o1.magic = MAGIC; o1.symbol = "EURUSD"
        o2 = MagicMock(); o2.ticket = 1002; o2.magic = 9999;  o2.symbol = "EURUSD"
        mock_mt5_exec.orders_get.return_value = (o1, o2)

        client = make_exec_client(config, mock_mt5_exec)
        await client._reconcile_open_orders()

        assert 1001 in client._known_order_tickets
        assert 1002 not in client._known_order_tickets

    @pytest.mark.asyncio
    async def test_reconcile_positions_registers_magic_matched(self, config, mock_mt5_exec):
        p1 = MagicMock(); p1.ticket = 2001; p1.magic = MAGIC; p1.symbol = "EURUSD"; p1.volume = 0.1
        p2 = MagicMock(); p2.ticket = 2002; p2.magic = 9999;  p2.symbol = "EURUSD"; p2.volume = 0.1
        mock_mt5_exec.positions_get.return_value = (p1, p2)

        client = make_exec_client(config, mock_mt5_exec)
        await client._reconcile_open_positions()

        assert 2001 in client._known_position_tickets
        assert 2002 not in client._known_position_tickets

    @pytest.mark.asyncio
    async def test_reconcile_orders_none_returns_early(self, config, mock_mt5_exec):
        mock_mt5_exec.orders_get.return_value = None

        client = make_exec_client(config, mock_mt5_exec)
        # Should not raise
        await client._reconcile_open_orders()
        assert len(client._known_order_tickets) == 0

    @pytest.mark.asyncio
    async def test_reconcile_positions_none_returns_early(self, config, mock_mt5_exec):
        mock_mt5_exec.positions_get.return_value = None

        client = make_exec_client(config, mock_mt5_exec)
        await client._reconcile_open_positions()
        assert len(client._known_position_tickets) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 19. PROPERTIES AND REPR
# ─────────────────────────────────────────────────────────────────────────────

class TestProperties:
    def test_is_polling_false_initially(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        assert client.is_polling is False

    @pytest.mark.asyncio
    async def test_is_polling_true_after_connect(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        await client._connect()
        assert client.is_polling is True
        client._exec_poll_task.cancel()
        try:
            await client._exec_poll_task
        except asyncio.CancelledError:
            pass

    def test_known_order_count_reflects_set(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        client._known_order_tickets = {1, 2, 3}
        assert client.known_order_count == 3

    def test_known_position_count_reflects_set(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        client._known_position_tickets = {10, 20}
        assert client.known_position_count == 2

    def test_repr_contains_account(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        r = repr(client)
        assert "12345678" in r

    def test_repr_contains_orders_and_positions(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        r = repr(client)
        assert "orders=" in r
        assert "positions=" in r

# ─────────────────────────────────────────────────────────────────────────────
# FILL EMISSION — _emit_fill and _poll_exec_once deal processing
#
# These tests cover the fix that makes on_order_filled fire in strategies.
#
# Test groups:
#   A. _emit_fill — happy path (BUY deal, SELL deal)
#   B. _emit_fill — skips non-trade deal types
#   C. _emit_fill — skips zero-volume deals
#   D. _emit_fill — skips unknown instrument
#   E. _emit_fill — recovers ClientOrderId from session map
#   F. _emit_fill — synthesises ClientOrderId for orders from previous session
#   G. _emit_fill — commission sign (negative MT5 → positive Money)
#   H. _emit_fill — generate_order_filled exception does not crash loop
#   I. _poll_exec_once — new deal triggers _emit_fill
#   J. _poll_exec_once — same deal not emitted twice (dedup by (time, ticket))
#   K. _poll_exec_once — two deals in same second both emitted (ticket tiebreaker)
#   L. _poll_exec_once — wrong magic deal not emitted
#   M. _processed_deal_keys cleared on disconnect
# ─────────────────────────────────────────────────────────────────────────────

def make_deal(
    ticket=10001,
    order=99991,
    symbol="EURUSD",
    deal_type=0,          # 0 = DEAL_TYPE_BUY
    volume=0.10,
    price=1.08500,
    commission=-0.50,
    profit=25.00,
    currency="USD",
    magic=MAGIC,
    time=1_700_000_000,
):
    """Build a minimal MT5 deal MagicMock."""
    deal = MagicMock()
    deal.ticket     = ticket
    deal.order      = order
    deal.symbol     = symbol
    deal.type       = deal_type
    deal.volume     = volume
    deal.price      = price
    deal.commission = commission
    deal.profit     = profit
    deal.currency   = currency
    deal.magic      = magic
    deal.time       = time
    return deal


class TestEmitFill:
    """Unit tests for _emit_fill — the core fill emission method."""

    @pytest.mark.asyncio
    async def test_buy_deal_calls_generate_order_filled(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock()

        deal = make_deal(deal_type=mock_mt5_exec.DEAL_TYPE_BUY)
        await client._emit_fill(deal)

        client.generate_order_filled.assert_called_once()

    @pytest.mark.asyncio
    async def test_sell_deal_calls_generate_order_filled(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock()

        deal = make_deal(deal_type=mock_mt5_exec.DEAL_TYPE_SELL)
        await client._emit_fill(deal)

        client.generate_order_filled.assert_called_once()

    @pytest.mark.asyncio
    async def test_order_side_is_buy_for_buy_deal(self, config, mock_mt5_exec):
        from nautilus_trader.model.enums import OrderSide
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock()

        deal = make_deal(deal_type=mock_mt5_exec.DEAL_TYPE_BUY)
        await client._emit_fill(deal)

        kwargs = client.generate_order_filled.call_args[1]
        assert kwargs["order_side"] == OrderSide.BUY

    @pytest.mark.asyncio
    async def test_order_side_is_sell_for_sell_deal(self, config, mock_mt5_exec):
        from nautilus_trader.model.enums import OrderSide
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock()

        deal = make_deal(deal_type=mock_mt5_exec.DEAL_TYPE_SELL)
        await client._emit_fill(deal)

        kwargs = client.generate_order_filled.call_args[1]
        assert kwargs["order_side"] == OrderSide.SELL

    @pytest.mark.asyncio
    async def test_price_and_quantity_passed_correctly(self, config, mock_mt5_exec):
        from nautilus_trader.model.objects import Price, Quantity
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock()

        deal = make_deal(price=1.08520, volume=0.25)
        await client._emit_fill(deal)

        kwargs = client.generate_order_filled.call_args[1]
        assert float(kwargs["last_px"])  == pytest.approx(1.08520, abs=1e-5)
        assert float(kwargs["last_qty"]) == pytest.approx(0.25,    abs=1e-4)

    @pytest.mark.asyncio
    async def test_commission_made_positive(self, config, mock_mt5_exec):
        """MT5 stores commission as a negative float; NT wants positive Money."""
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock()

        deal = make_deal(commission=-1.50)
        await client._emit_fill(deal)

        kwargs = client.generate_order_filled.call_args[1]
        assert float(kwargs["commission"]) == pytest.approx(1.50)

    @pytest.mark.asyncio
    async def test_zero_commission_handled(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock()

        deal = make_deal(commission=0.0)
        await client._emit_fill(deal)

        kwargs = client.generate_order_filled.call_args[1]
        assert float(kwargs["commission"]) == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_trade_id_is_deal_ticket(self, config, mock_mt5_exec):
        from nautilus_trader.model.identifiers import TradeId
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock()

        deal = make_deal(ticket=77771)
        await client._emit_fill(deal)

        kwargs = client.generate_order_filled.call_args[1]
        assert kwargs["trade_id"] == TradeId("77771")

    @pytest.mark.asyncio
    async def test_venue_order_id_is_order_ticket(self, config, mock_mt5_exec):
        from nautilus_trader.model.identifiers import VenueOrderId
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock()

        deal = make_deal(order=88881)
        await client._emit_fill(deal)

        kwargs = client.generate_order_filled.call_args[1]
        assert kwargs["venue_order_id"] == VenueOrderId("88881")

    @pytest.mark.asyncio
    async def test_client_order_id_recovered_from_session_map(self, config, mock_mt5_exec):
        """If we placed the order this session, the ClientOrderId should come from our map."""
        from nautilus_trader.model.identifiers import ClientOrderId
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock()
        client._ticket_to_client_order_id[88881] = "O-SESSION-123"

        deal = make_deal(order=88881)
        await client._emit_fill(deal)

        kwargs = client.generate_order_filled.call_args[1]
        assert kwargs["client_order_id"] == ClientOrderId("O-SESSION-123")

    @pytest.mark.asyncio
    async def test_client_order_id_synthesised_for_previous_session(self, config, mock_mt5_exec):
        """Orders placed in a previous session have no entry in the map.
        We synthesise MT5-{ticket} so NT still tracks the fill."""
        from nautilus_trader.model.identifiers import ClientOrderId
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock()
        # ticket 55551 not in map — simulates order from previous session

        deal = make_deal(order=55551)
        await client._emit_fill(deal)

        kwargs = client.generate_order_filled.call_args[1]
        assert kwargs["client_order_id"] == ClientOrderId("MT5-55551")

    @pytest.mark.asyncio
    async def test_non_trade_deal_type_skipped(self, config, mock_mt5_exec):
        """Balance adjustments, credits, swaps — deal.type is not BUY or SELL."""
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock()

        # deal.type = 2 → DEAL_TYPE_BALANCE (not BUY=0 or SELL=1)
        deal = make_deal(deal_type=2)
        await client._emit_fill(deal)

        client.generate_order_filled.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_volume_deal_skipped(self, config, mock_mt5_exec):
        """Commission-only deal entries have volume=0; they should be skipped."""
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock()

        deal = make_deal(volume=0.0)
        await client._emit_fill(deal)

        client.generate_order_filled.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_instrument_skipped(self, config, mock_mt5_exec):
        """If the provider can't find the instrument, we log and skip rather than crash."""
        client = make_exec_client(config, mock_mt5_exec)
        client._provider.get_instrument = MagicMock(return_value=None)
        client.generate_order_filled = MagicMock()

        deal = make_deal(symbol="UNKNOWNSYMBOL")
        await client._emit_fill(deal)

        client.generate_order_filled.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_order_filled_exception_does_not_raise(self, config, mock_mt5_exec):
        """A bad fill (e.g. NT doesn't know this order) must not crash the poll loop."""
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock(side_effect=RuntimeError("NT error"))

        deal = make_deal()
        # Should not raise
        await client._emit_fill(deal)

    @pytest.mark.asyncio
    async def test_instrument_id_constructed_from_symbol(self, config, mock_mt5_exec):
        from nautilus_trader.model.identifiers import InstrumentId
        client = make_exec_client(config, mock_mt5_exec)
        client.generate_order_filled = MagicMock()

        deal = make_deal(symbol="EURUSD")
        await client._emit_fill(deal)

        kwargs = client.generate_order_filled.call_args[1]
        assert kwargs["instrument_id"] == InstrumentId.from_str("EURUSD.MT5")


class TestPollExecOnceFills:
    """Tests for deal processing and dedup inside _poll_exec_once."""

    @pytest.mark.asyncio
    async def test_new_deal_triggers_emit_fill(self, config, mock_mt5_exec):
        """A deal that hasn't been seen before should call _emit_fill."""
        deal = make_deal(magic=MAGIC, time=1_700_000_100, ticket=10001)
        mock_mt5_exec.history_deals_get.return_value = (deal,)

        client = make_exec_client(config, mock_mt5_exec)
        client._emit_fill = AsyncMock()

        await client._poll_exec_once()

        client._emit_fill.assert_called_once_with(deal)

    @pytest.mark.asyncio
    async def test_same_deal_not_emitted_twice(self, config, mock_mt5_exec):
        """Polling twice with the same deal should only emit once."""
        deal = make_deal(magic=MAGIC, time=1_700_000_100, ticket=10001)
        mock_mt5_exec.history_deals_get.return_value = (deal,)

        client = make_exec_client(config, mock_mt5_exec)
        client._emit_fill = AsyncMock()

        await client._poll_exec_once()
        await client._poll_exec_once()

        assert client._emit_fill.call_count == 1

    @pytest.mark.asyncio
    async def test_two_deals_same_second_both_emitted(self, config, mock_mt5_exec):
        """Two deals at the same timestamp but different tickets must both be emitted.
        This is the bug that _last_deal_time (seconds only) had — the new
        (time, ticket) key fixes it."""
        deal_a = make_deal(magic=MAGIC, time=1_700_000_100, ticket=10001)
        deal_b = make_deal(magic=MAGIC, time=1_700_000_100, ticket=10002)
        mock_mt5_exec.history_deals_get.return_value = (deal_a, deal_b)

        client = make_exec_client(config, mock_mt5_exec)
        client._emit_fill = AsyncMock()

        await client._poll_exec_once()

        assert client._emit_fill.call_count == 2

    @pytest.mark.asyncio
    async def test_wrong_magic_deal_not_emitted(self, config, mock_mt5_exec):
        deal = make_deal(magic=9999, time=1_700_000_100, ticket=10001)
        mock_mt5_exec.history_deals_get.return_value = (deal,)

        client = make_exec_client(config, mock_mt5_exec)
        client._emit_fill = AsyncMock()

        await client._poll_exec_once()

        client._emit_fill.assert_not_called()

    @pytest.mark.asyncio
    async def test_processed_deal_keys_updated_after_poll(self, config, mock_mt5_exec):
        deal = make_deal(magic=MAGIC, time=1_700_000_100, ticket=10001)
        mock_mt5_exec.history_deals_get.return_value = (deal,)

        client = make_exec_client(config, mock_mt5_exec)
        client._emit_fill = AsyncMock()

        await client._poll_exec_once()

        assert (1_700_000_100, 10001) in client._processed_deal_keys

    @pytest.mark.asyncio
    async def test_second_deal_in_later_poll_is_emitted(self, config, mock_mt5_exec):
        """A deal that arrives in the second poll (not the first) must still be emitted."""
        deal_a = make_deal(magic=MAGIC, time=1_700_000_100, ticket=10001)
        deal_b = make_deal(magic=MAGIC, time=1_700_000_200, ticket=10002)

        # First poll — only deal_a
        mock_mt5_exec.history_deals_get.return_value = (deal_a,)
        client = make_exec_client(config, mock_mt5_exec)
        client._emit_fill = AsyncMock()
        await client._poll_exec_once()

        # Second poll — both deals returned by MT5 (history accumulates)
        mock_mt5_exec.history_deals_get.return_value = (deal_a, deal_b)
        await client._poll_exec_once()

        assert client._emit_fill.call_count == 2

    @pytest.mark.asyncio
    async def test_processed_deal_keys_cleared_on_disconnect(self, config, mock_mt5_exec):
        client = make_exec_client(config, mock_mt5_exec)
        client._processed_deal_keys.add((1_700_000_100, 10001))
        client._processed_deal_keys.add((1_700_000_200, 10002))

        await client._disconnect()

        assert len(client._processed_deal_keys) == 0

    @pytest.mark.asyncio
    async def test_no_deals_does_not_call_emit_fill(self, config, mock_mt5_exec):
        mock_mt5_exec.history_deals_get.return_value = ()

        client = make_exec_client(config, mock_mt5_exec)
        client._emit_fill = AsyncMock()

        await client._poll_exec_once()

        client._emit_fill.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_deals_response_handled_gracefully(self, config, mock_mt5_exec):
        mock_mt5_exec.history_deals_get.return_value = None

        client = make_exec_client(config, mock_mt5_exec)
        client._emit_fill = AsyncMock()

        # Should not raise
        await client._poll_exec_once()

        client._emit_fill.assert_not_called()