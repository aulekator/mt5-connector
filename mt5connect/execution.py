"""
nautilus_mt5/execution.py

MT5LiveExecutionClient — submits, modifies, and cancels orders on MT5,
then polls for fills and position changes and emits execution reports
back into NautilusTrader.

MT5 has no push/event API for order state. Everything is polled.

Architecture
------------
  _connect()
    └─ verifies connection
    └─ starts _exec_poll_loop() as asyncio Task

  _exec_poll_loop()  (runs every exec_poll_interval_ms, default 250ms)
    └─ mt5.orders_get()      → detect new/removed pending orders
    └─ mt5.positions_get()   → detect new fills / position changes
    └─ mt5.history_deals_get() → detect closed deals (fills)
    └─ generate_order_status_report() / generate_fill_report()

  submit_order()
    └─ mt5.order_send(ORDER_TYPE_BUY / ORDER_TYPE_SELL)
    └─ emits OrderAccepted / OrderRejected

  cancel_order()
    └─ mt5.order_send(ACTION_REMOVE) for pending orders
    └─ mt5.order_send(ACTION_DEAL, opposite side) for market positions

  modify_order()
    └─ mt5.order_send(ACTION_SLTP) for SL/TP updates

Order type support
------------------
  Market orders  → ACTION_DEAL   (immediate fill at current price)
  Limit orders   → ACTION_PENDING (ORDER_TYPE_BUY_LIMIT / SELL_LIMIT)
  Stop orders    → ACTION_PENDING (ORDER_TYPE_BUY_STOP  / SELL_STOP)
  Stop-limit     → ACTION_PENDING (ORDER_TYPE_BUY_STOP_LIMIT / SELL_STOP_LIMIT)

  All order types support SL/TP at submission time.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import MetaTrader5 as mt5

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.execution.messages import (
    CancelAllOrders,
    CancelOrder,
    ModifyOrder,
    SubmitOrder,
)
from nautilus_trader.execution.reports import (
    FillReport,
    OrderStatusReport,
    PositionStatusReport,
)
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.enums import (
    AccountType,
    LiquiditySide,
    OmsType,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
    TrailingOffsetType,
    TriggerType,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientId,
    ClientOrderId,
    InstrumentId,
    StrategyId,
    Symbol,
    TradeId,
    VenueOrderId,
)
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.currencies import USD

from mt5connect.constants import MT5_MAGIC_NUMBER, MT5_VENUE
from mt5connect.errors import MT5ConnectionError, MT5OrderError

if TYPE_CHECKING:
    from mt5connect.config import MT5Config
    from mt5connect.connection import MT5Connection
    from mt5connect.providers import MT5InstrumentProvider

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# FILLING MODE AUTO-DETECT
#
# Different brokers and account types support different MT5 order filling
# modes. Sending the wrong one causes retcode=10030 "Invalid type of order
# filling". This function reads the symbol's actual supported filling modes
# at runtime and picks the correct one — works across Raw Spread, Pro,
# Standard, and Zero account types without any manual configuration.
#
#   symbol_info().filling_mode bitmask:
#     bit 0 (1) = ORDER_FILLING_FOK    supported
#     bit 1 (2) = ORDER_FILLING_IOC    supported
#     bit 2 (4) = ORDER_FILLING_RETURN supported
#
#   Priority: IOC > FOK > RETURN
#   IOC works on Raw Spread, Pro, and Standard Exness accounts (the most
#   common case). FOK is used as fallback for Zero accounts that only
#   support FOK. RETURN is the last resort for brokers that only allow it.
# ─────────────────────────────────────────────────────────────────────────────

def _get_filling_mode(symbol: str) -> int:
    """Auto-detect the correct MT5 order filling mode for a symbol."""
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_FOK  # safe fallback if symbol info unavailable

    bitmask = info.filling_mode

    if bitmask & 2:    # IOC supported
        return mt5.ORDER_FILLING_IOC
    elif bitmask & 1:  # FOK supported
        return mt5.ORDER_FILLING_FOK
    elif bitmask & 4:  # RETURN supported
        return mt5.ORDER_FILLING_RETURN
    else:
        return mt5.ORDER_FILLING_FOK  # absolute fallback


# ─────────────────────────────────────────────────────────────────────────────
# ORDER TYPE MAPPING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _nautilus_side_to_mt5_market(side: OrderSide) -> int:
    """Map NautilusTrader OrderSide to MT5 market order type."""
    if side == OrderSide.BUY:
        return mt5.ORDER_TYPE_BUY
    return mt5.ORDER_TYPE_SELL


def _nautilus_order_to_mt5_pending(order_type: OrderType, side: OrderSide) -> int:
    """Map NautilusTrader OrderType + OrderSide to MT5 pending order type."""
    mapping = {
        (OrderType.LIMIT, OrderSide.BUY):  mt5.ORDER_TYPE_BUY_LIMIT,
        (OrderType.LIMIT, OrderSide.SELL): mt5.ORDER_TYPE_SELL_LIMIT,
        (OrderType.STOP_MARKET, OrderSide.BUY):  mt5.ORDER_TYPE_BUY_STOP,
        (OrderType.STOP_MARKET, OrderSide.SELL): mt5.ORDER_TYPE_SELL_STOP,
        (OrderType.STOP_LIMIT, OrderSide.BUY):  mt5.ORDER_TYPE_BUY_STOP_LIMIT,
        (OrderType.STOP_LIMIT, OrderSide.SELL): mt5.ORDER_TYPE_SELL_STOP_LIMIT,
    }
    key = (order_type, side)
    if key not in mapping:
        raise MT5OrderError(
            f"Unsupported order type/side combination: {order_type.name} {side.name}. "
            "MT5 supports: MARKET, LIMIT, STOP_MARKET, STOP_LIMIT."
        )
    return mapping[key]


def _mt5_order_type_to_nautilus_side(mt5_order_type: int) -> OrderSide:
    """Map MT5 order type integer back to NautilusTrader OrderSide."""
    buy_types = {
        mt5.ORDER_TYPE_BUY,
        mt5.ORDER_TYPE_BUY_LIMIT,
        mt5.ORDER_TYPE_BUY_STOP,
        mt5.ORDER_TYPE_BUY_STOP_LIMIT,
    }
    return OrderSide.BUY if mt5_order_type in buy_types else OrderSide.SELL


def _mt5_retcode_to_str(retcode: int) -> str:
    """Human-readable string for common MT5 return codes."""
    _RETCODES = {
        10004: "Requote",
        10006: "Request rejected",
        10007: "Request cancelled by trader",
        10008: "Order placed",
        10009: "Request completed",
        10010: "Only part of the request was completed",
        10011: "Request processing error",
        10012: "Request cancelled by timeout",
        10013: "Invalid request",
        10014: "Invalid volume",
        10015: "Invalid price",
        10016: "Invalid stops",
        10017: "Trade is disabled",
        10018: "Market is closed",
        10019: "Insufficient funds",
        10020: "Prices changed",
        10021: "No quotes to process request",
        10022: "Invalid order expiration date",
        10023: "Order state changed",
        10024: "Too frequent requests",
        10025: "No changes in request",
        10026: "Auto trading disabled by server",
        10027: "Auto trading disabled by client terminal",
        10028: "Request locked for processing",
        10029: "Order or position frozen",
        10030: "Invalid type of order filling",
        10031: "No connection to the trade server",
        10032: "Operation is allowed only for live accounts",
        10033: "Pending orders limit reached",
        10034: "Volume of orders and positions limit reached",
        10035: "Incorrect or prohibited order type",
        10036: "Position with the specified ID already closed",
        10038: "Close volume exceeds open volume",
        10039: "A close order already exists",
        10040: "Positions limit not reached",
        10041: "Pending order activation pending",
        10042: "Pending orders are not allowed for this symbol",
        10043: "Request declined — settlement in progress",
        10044: "SL/TP levels invalid",
    }
    return _RETCODES.get(retcode, f"Unknown retcode {retcode}")


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class MT5LiveExecutionClient(LiveExecutionClient):
    """
    Live execution client for MT5. Submits orders and polls for fills.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
    connection : MT5Connection
    msgbus : MessageBus
    cache : Cache
    clock : LiveClock
    instrument_provider : MT5InstrumentProvider
    config : MT5Config
    account_id : AccountId, optional
        If omitted, built from the MT5 account number in config.

    Notes
    -----
    MT5 has no WebSocket push API. This client polls at exec_poll_interval_ms
    (default 250ms) for order state changes and deal history.

    The magic_number in MT5Config is used to tag all orders sent by this
    adapter. Only orders with the matching magic_number are tracked —
    manually placed orders in the MT5 terminal are ignored. This makes it
    safe to run the adapter alongside manual trading.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        connection: "MT5Connection",
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: "MT5InstrumentProvider",
        config: "MT5Config",
        account_id: AccountId | None = None,
    ) -> None:
        if account_id is None:
            account_id = AccountId(f"MT5-{config.account}")

        super().__init__(
            loop=loop,
            client_id=ClientId(MT5_VENUE.value),
            venue=MT5_VENUE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=None,   # MT5 accounts are multi-currency
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
        )
        # Register with parent via _set_account_id so the C-level property is set.
        self._set_account_id(account_id)
        self._conn     = connection
        self._config   = config
        self._provider = instrument_provider

        # asyncio polling task — created in _connect, cancelled in _disconnect
        self._exec_poll_task: asyncio.Task | None = None

        # Track known pending order tickets (MT5 ticket int) to detect changes
        self._known_order_tickets: set[int] = set()

        # Track known open position tickets to detect new fills / closures
        self._known_position_tickets: set[int] = set()

        # Track last deal time to only process new deals on each poll
        self._last_deal_time: int = 0

        # Set of (deal.time, deal.ticket) tuples already emitted into NT.
        # Prevents double-counting when the same deal appears in multiple polls.
        self._processed_deal_keys: set[tuple[int, int]] = set()

        # Map from ClientOrderId string → MT5 ticket int for fast lookup
        self._client_order_id_to_ticket: dict[str, int] = {}

        # Reverse map: MT5 ticket → ClientOrderId string
        self._ticket_to_client_order_id: dict[int, str] = {}

    # ── Required: connect / disconnect ───────────────────────────────────────

    async def _connect(self) -> None:
        """
        Called by NautilusTrader on node startup.

        Sequence:
          1. Verify connection
          2. Generate initial account state report
          3. Reconcile any open orders and positions
          4. Start execution polling loop
        """
        self._conn.ensure_connected()

        # Initial account state
        await self._generate_account_state()

        # Reconcile existing open orders and positions at startup
        await self._reconcile_open_orders()
        await self._reconcile_open_positions()

        # Start execution polling loop
        self._exec_poll_task = asyncio.get_event_loop().create_task(
            self._exec_poll_loop(),
            name="MT5LiveExecutionClient._exec_poll_loop",
        )
        self._log.info(
            f"MT5LiveExecutionClient: connected — polling every "
            f"{self._config.exec_poll_interval_ms}ms"
        )

    async def _disconnect(self) -> None:
        """Cancel the execution polling loop cleanly."""
        if self._exec_poll_task and not self._exec_poll_task.done():
            self._exec_poll_task.cancel()
            try:
                await self._exec_poll_task
            except asyncio.CancelledError:
                pass
            self._exec_poll_task = None

        self._known_order_tickets.clear()
        self._known_position_tickets.clear()
        self._client_order_id_to_ticket.clear()
        self._ticket_to_client_order_id.clear()
        self._processed_deal_keys.clear()
        self._log.info("MT5LiveExecutionClient: disconnected")

    # ── Order submission ──────────────────────────────────────────────────────

    async def _submit_order(self, command: SubmitOrder) -> None:
        """
        Submit an order to MT5.

        Supports:
          - MarketOrder    → ACTION_DEAL
          - LimitOrder     → ACTION_PENDING + ORDER_TYPE_BUY/SELL_LIMIT
          - StopMarketOrder → ACTION_PENDING + ORDER_TYPE_BUY/SELL_STOP
          - StopLimitOrder  → ACTION_PENDING + ORDER_TYPE_BUY/SELL_STOP_LIMIT

        On success: emits OrderAccepted (pending/stop) or OrderFilled (market).
        On failure: emits OrderRejected.
        """
        order  = command.order
        symbol = order.instrument_id.symbol.value

        self._conn.ensure_connected()

        instrument = self._provider.get_instrument(symbol)
        if instrument is None:
            self._generate_order_rejected(
                order, f"Instrument not found for symbol '{symbol}'"
            )
            return

        # ── Build the MT5 trade request ───────────────────────────────────

        # Get current price for market orders
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            self._generate_order_rejected(order, f"Cannot get price for '{symbol}'")
            return

        price = float(order.price) if hasattr(order, "price") and order.price else 0.0
        sl    = float(order.trigger_price) if hasattr(order, "trigger_price") and order.trigger_price else 0.0

        # For stop-limit: price = limit price, stoplimit_price = stop trigger
        stoplimit_price = 0.0
        if order.order_type == OrderType.STOP_LIMIT:
            stoplimit_price = price
            price = float(order.trigger_price) if order.trigger_price else 0.0

        # Market order: use current ask/bid
        if order.order_type == OrderType.MARKET:
            price = tick.ask if order.side == OrderSide.BUY else tick.bid
            action    = mt5.TRADE_ACTION_DEAL
            mt5_order_type = _nautilus_side_to_mt5_market(order.side)
        else:
            action    = mt5.TRADE_ACTION_PENDING
            mt5_order_type = _nautilus_order_to_mt5_pending(order.order_type, order.side)

        # Auto-detect the correct filling mode for this symbol/account type
        filling_mode = _get_filling_mode(symbol)

        # Build request dict
        request = {
            "action":       action,
            "symbol":       symbol,
            "volume":       float(order.quantity),
            "type":         mt5_order_type,
            "price":        price,
            "sl":           0.0,      # set below if order has sl
            "tp":           0.0,      # set below if order has tp
            "deviation":    20,       # max price deviation (points) for market orders
            "magic":        self._config.magic_number,
            "comment":      str(order.client_order_id),
            "type_filling": filling_mode,
            "type_time":    _time_in_force_to_mt5(order.time_in_force),
        }

        if stoplimit_price:
            request["stoplimit"] = stoplimit_price

        # Attach SL/TP if the order carries them
        if hasattr(order, "sl_trigger_price") and order.sl_trigger_price:
            request["sl"] = float(order.sl_trigger_price)
        if hasattr(order, "tp_price") and order.tp_price:
            request["tp"] = float(order.tp_price)

        # ── Send to MT5 ───────────────────────────────────────────────────

        result = mt5.order_send(request)

        if result is None:
            code, msg = mt5.last_error()
            self._generate_order_rejected(
                order, f"mt5.order_send() returned None — error {code}: {msg}"
            )
            return

        if result.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED,
                                   mt5.TRADE_RETCODE_DONE_PARTIAL, 10008):
            reason = _mt5_retcode_to_str(result.retcode)
            self._generate_order_rejected(
                order,
                f"MT5 rejected order: {reason} (retcode={result.retcode})"
            )
            return

        # ── Success — record the ticket ───────────────────────────────────

        ticket = result.order
        client_order_id_str = str(order.client_order_id)
        self._client_order_id_to_ticket[client_order_id_str] = ticket
        self._ticket_to_client_order_id[ticket] = client_order_id_str

        self._log.info(
            f"MT5LiveExecutionClient: order sent "
            f"ticket={ticket} client_order_id={client_order_id_str} "
            f"retcode={result.retcode}"
        )

        # NautilusTrader will receive fill reports from the polling loop.
        # For now just emit OrderAccepted.
        self._generate_order_accepted(order, VenueOrderId(str(ticket)))

    async def _cancel_order(self, command: CancelOrder) -> None:
        """
        Cancel a pending order or close a market position.

        For pending orders → ACTION_REMOVE.
        For open positions → ACTION_DEAL with opposite side at market price.
        """
        self._conn.ensure_connected()

        client_order_id_str = str(command.client_order_id)
        ticket = self._client_order_id_to_ticket.get(client_order_id_str)

        if ticket is None:
            # Try to find by venue order id
            if command.venue_order_id:
                try:
                    ticket = int(command.venue_order_id.value)
                except (ValueError, AttributeError):
                    pass

        if ticket is None:
            self._log.warning(
                f"MT5LiveExecutionClient: cannot cancel — ticket not found for "
                f"{client_order_id_str}"
            )
            return

        symbol = command.instrument_id.symbol.value

        # Check if it's a pending order
        orders = mt5.orders_get(ticket=ticket)
        if orders:
            # It's a pending order — remove it
            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order":  ticket,
                "comment": f"cancel:{client_order_id_str}",
            }
            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                code = result.retcode if result else -1
                self._log.error(
                    f"MT5LiveExecutionClient: cancel failed for ticket={ticket} "
                    f"retcode={code}: {_mt5_retcode_to_str(code)}"
                )
                return
            self._log.info(f"MT5LiveExecutionClient: pending order {ticket} cancelled")
            return

        # Check if it's an open position
        positions = mt5.positions_get(ticket=ticket)
        if positions:
            pos = positions[0]
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                self._log.error(
                    f"MT5LiveExecutionClient: cannot close position {ticket} — no tick"
                )
                return

            # Opposite side to close
            if pos.type == mt5.ORDER_TYPE_BUY:
                close_type  = mt5.ORDER_TYPE_SELL
                close_price = tick.bid
            else:
                close_type  = mt5.ORDER_TYPE_BUY
                close_price = tick.ask

            # Auto-detect the correct filling mode for this symbol/account type
            filling_mode = _get_filling_mode(symbol)

            request = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       symbol,
                "volume":       pos.volume,
                "type":         close_type,
                "position":     ticket,
                "price":        close_price,
                "deviation":    20,
                "magic":        self._config.magic_number,
                "comment":      f"close:{client_order_id_str}",
                "type_filling": filling_mode,
            }
            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                code = result.retcode if result else -1
                self._log.error(
                    f"MT5LiveExecutionClient: close failed for position {ticket} "
                    f"retcode={code}: {_mt5_retcode_to_str(code)}"
                )
            else:
                self._log.info(
                    f"MT5LiveExecutionClient: position {ticket} closed"
                )
            return

        self._log.warning(
            f"MT5LiveExecutionClient: ticket {ticket} not found as order or position"
        )

    async def _cancel_all_orders(self, command: CancelAllOrders) -> None:
        """
        Cancel all pending orders for a given instrument.
        Open positions are NOT closed by this command.
        """
        self._conn.ensure_connected()

        symbol = command.instrument_id.symbol.value
        orders = mt5.orders_get(symbol=symbol)
        if not orders:
            return

        for order in orders:
            if order.magic != self._config.magic_number:
                continue  # not ours
            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order":  order.ticket,
                "comment": "cancel_all",
            }
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                self._log.info(
                    f"MT5LiveExecutionClient: cancelled order ticket={order.ticket}"
                )
            else:
                code = result.retcode if result else -1
                self._log.warning(
                    f"MT5LiveExecutionClient: failed to cancel ticket={order.ticket} "
                    f"retcode={code}"
                )

    async def _modify_order(self, command: ModifyOrder) -> None:
        """
        Modify price, SL, or TP on an existing pending order.
        Uses ACTION_MODIFY for pending orders, ACTION_SLTP for open positions.
        """
        self._conn.ensure_connected()

        client_order_id_str = str(command.client_order_id)
        ticket = self._client_order_id_to_ticket.get(client_order_id_str)
        if ticket is None and command.venue_order_id:
            try:
                ticket = int(command.venue_order_id.value)
            except (ValueError, AttributeError):
                pass

        if ticket is None:
            self._log.warning(
                f"MT5LiveExecutionClient: cannot modify — ticket not found for "
                f"{client_order_id_str}"
            )
            return

        new_price = float(command.price) if command.price else 0.0
        new_sl    = float(command.trigger_price) if command.trigger_price else 0.0
        new_tp    = 0.0  # NautilusTrader doesn't pass tp in ModifyOrder currently

        # Check pending order vs open position
        orders = mt5.orders_get(ticket=ticket)
        if orders:
            order = orders[0]
            request = {
                "action": mt5.TRADE_ACTION_MODIFY,
                "order":  ticket,
                "price":  new_price or order.price_open,
                "sl":     new_sl,
                "tp":     new_tp or order.tp,
                "type_time": order.type_time,
                "expiration": order.time_expiration,
            }
        else:
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                self._log.warning(
                    f"MT5LiveExecutionClient: modify target {ticket} not found"
                )
                return
            pos = positions[0]
            request = {
                "action":   mt5.TRADE_ACTION_SLTP,
                "symbol":   pos.symbol,
                "position": ticket,
                "sl":       new_sl or pos.sl,
                "tp":       new_tp or pos.tp,
            }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = result.retcode if result else -1
            self._log.error(
                f"MT5LiveExecutionClient: modify failed ticket={ticket} "
                f"retcode={code}: {_mt5_retcode_to_str(code)}"
            )
        else:
            self._log.info(f"MT5LiveExecutionClient: order {ticket} modified")

    # ── Reconciliation (called at startup) ───────────────────────────────────

    async def _reconcile_open_orders(self) -> None:
        """
        Load all currently open pending orders at startup and register them
        so the polling loop can detect state changes.

        Only processes orders with our magic_number.
        """
        orders = mt5.orders_get()
        if not orders:
            return

        for order in orders:
            if order.magic != self._config.magic_number:
                continue
            self._known_order_tickets.add(order.ticket)
            self._log.debug(
                f"MT5LiveExecutionClient: reconciled pending order ticket={order.ticket} "
                f"symbol={order.symbol}"
            )

        self._log.info(
            f"MT5LiveExecutionClient: reconciled {len(self._known_order_tickets)} "
            f"open pending orders"
        )

    async def _reconcile_open_positions(self) -> None:
        """
        Load all open positions at startup. Register them so the polling
        loop can detect closures.

        Only processes positions with our magic_number.
        """
        positions = mt5.positions_get()
        if not positions:
            return

        for pos in positions:
            if pos.magic != self._config.magic_number:
                continue
            self._known_position_tickets.add(pos.ticket)
            self._log.debug(
                f"MT5LiveExecutionClient: reconciled position ticket={pos.ticket} "
                f"symbol={pos.symbol} volume={pos.volume}"
            )

        self._log.info(
            f"MT5LiveExecutionClient: reconciled {len(self._known_position_tickets)} "
            f"open positions"
        )

    # ── Execution polling loop ────────────────────────────────────────────────

    async def _exec_poll_loop(self) -> None:
        """
        Poll MT5 for order and position state changes.

        On each iteration:
          1. Check for filled / cancelled pending orders
          2. Check for new deals (fills) in history
          3. Refresh account state periodically

        On connection error → attempt reconnect.
        On reconnect failure → stop loop (node must be restarted).
        """
        self._log.info("MT5LiveExecutionClient: exec poll loop started")
        _account_refresh_counter = 0

        while True:
            try:
                await self._poll_exec_once()

                # Refresh account state every ~10 seconds (40 polls × 250ms)
                _account_refresh_counter += 1
                if _account_refresh_counter >= 40:
                    await self._generate_account_state()
                    _account_refresh_counter = 0

                await asyncio.sleep(self._config.exec_poll_interval_s)

            except asyncio.CancelledError:
                self._log.info("MT5LiveExecutionClient: exec poll loop cancelled")
                break

            except MT5ConnectionError as exc:
                self._log.warning(f"MT5LiveExecutionClient: connection lost — {exc}")
                ok = await self._conn.reconnect_async()
                if not ok:
                    self._log.error(
                        "MT5LiveExecutionClient: reconnect failed — stopping exec loop"
                    )
                    break
                self._log.info("MT5LiveExecutionClient: reconnected")

            except Exception as exc:
                self._log.error(
                    f"MT5LiveExecutionClient: unexpected exec poll error — {exc}"
                )
                await asyncio.sleep(1.0)

        self._log.info("MT5LiveExecutionClient: exec poll loop stopped")

    async def _poll_exec_once(self) -> None:
        """
        Single execution poll iteration.

        1. Fetch pending orders — detect new, removed, or state-changed orders.
        2. Fetch recent deals from history — emit FillReports for new fills.
        3. Fetch open positions — detect newly opened or closed positions.
        """
        self._conn.ensure_connected()

        # ── 1. Pending orders ─────────────────────────────────────────────

        current_orders = mt5.orders_get() or ()
        current_tickets = {
            o.ticket for o in current_orders
            if o.magic == self._config.magic_number
        }

        # Detect orders that disappeared (filled or cancelled)
        disappeared = self._known_order_tickets - current_tickets
        for ticket in disappeared:
            self._log.debug(
                f"MT5LiveExecutionClient: pending order {ticket} disappeared "
                "(filled or cancelled)"
            )
        self._known_order_tickets = current_tickets

        # ── 2. Deal history (fills) ───────────────────────────────────────

        # Look back from today (UTC midnight) to catch all deals this session.
        # We track _processed_deal_keys (set of (time, ticket) tuples) so each
        # deal is emitted into NT exactly once regardless of poll frequency.
        now     = datetime.now(timezone.utc)
        from_dt = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

        deals = mt5.history_deals_get(from_dt, now)
        if deals:
            for deal in deals:
                if deal.magic != self._config.magic_number:
                    continue
                deal_key = (deal.time, deal.ticket)
                if deal_key in self._processed_deal_keys:
                    continue

                self._processed_deal_keys.add(deal_key)
                self._last_deal_time = max(self._last_deal_time, deal.time)

                self._log.info(
                    f"MT5LiveExecutionClient: new deal ticket={deal.ticket} "
                    f"symbol={deal.symbol} volume={deal.volume} "
                    f"price={deal.price} profit={deal.profit}"
                )

                # Emit fill into NautilusTrader execution engine
                await self._emit_fill(deal)

        # ── 3. Open positions ─────────────────────────────────────────────

        current_positions = mt5.positions_get() or ()
        current_pos_tickets = {
            p.ticket for p in current_positions
            if p.magic == self._config.magic_number
        }

        # New positions since last poll
        new_positions = current_pos_tickets - self._known_position_tickets
        for ticket in new_positions:
            self._log.debug(
                f"MT5LiveExecutionClient: new position ticket={ticket}"
            )

        # Closed positions since last poll
        closed_positions = self._known_position_tickets - current_pos_tickets
        for ticket in closed_positions:
            self._log.debug(
                f"MT5LiveExecutionClient: position {ticket} closed"
            )

        self._known_position_tickets = current_pos_tickets

    # ── Fill emission ─────────────────────────────────────────────────────────

    async def _emit_fill(self, deal) -> None:
        """
        Convert a single MT5 deal into a NautilusTrader OrderFilled event
        and push it into the execution engine.

        MT5 deal types:
            DEAL_TYPE_BUY  (0) — opening a long or closing a short
            DEAL_TYPE_SELL (1) — opening a short or closing a long
            DEAL_TYPE_BALANCE, DEAL_TYPE_CREDIT, etc. — skip these

        We skip non-trade deals (balance adjustments, commissions paid
        as separate entries, etc.) by checking deal.type is BUY or SELL.

        The ClientOrderId is recovered from our ticket→client_order_id map
        if we placed the order this session. For orders placed in a previous
        session (e.g. a pending order left overnight) we synthesise a
        ClientOrderId from the MT5 order ticket so NT can still track it.
        """
        # Skip non-trade deal types (balance, credit, correction, etc.)
        if deal.type not in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL):
            return

        # Skip zero-volume deals (e.g. commission-only entries)
        if deal.volume <= 0:
            return

        symbol     = deal.symbol
        instrument = self._provider.get_instrument(symbol)
        if instrument is None:
            self._log.warning(
                f"MT5LiveExecutionClient: no instrument for deal symbol {symbol!r} — "
                "skipping fill emission"
            )
            return

        pp = instrument.price_precision
        sp = instrument.size_precision

        # Recover or synthesise ClientOrderId
        client_order_id_str = self._ticket_to_client_order_id.get(deal.order)
        if client_order_id_str:
            client_order_id = ClientOrderId(client_order_id_str)
        else:
            # Order placed in a previous session or externally; synthesise one
            client_order_id = ClientOrderId(f"MT5-{deal.order}")

        # Skip deals from previous sessions that have no order in the cache.
        # NT reconciliation already handles the open position — emitting a fill
        # for an unknown order just produces ERROR noise with no benefit.
        if not self._ticket_to_client_order_id.get(deal.order):
            if self._cache.order(client_order_id) is None:
                self._log.debug(
                    f"MT5LiveExecutionClient: skipping pre-session deal "
                    f"ticket={deal.ticket} order={deal.order} — no cached order"
                )
                return

        venue_order_id = VenueOrderId(str(deal.order))
        trade_id       = TradeId(str(deal.ticket))

        order_side = (
            OrderSide.BUY
            if deal.type == mt5.DEAL_TYPE_BUY
            else OrderSide.SELL
        )

        # ================================================================
        # FIXED: Get account currency safely for commission
        # ================================================================
        try:
            # Try to get currency from deal first
            deal_currency = getattr(deal, 'currency', None)
            if deal_currency:
                currency = _parse_account_currency(deal_currency)
            else:
                # Fallback: get account info
                account_info = mt5.account_info()
                if account_info and hasattr(account_info, 'currency'):
                    currency = _parse_account_currency(account_info.currency)
                else:
                    currency = USD
        except Exception:
            currency = USD

        commission = Money(abs(deal.commission or 0.0), currency)

        ts_event = int(deal.time) * 1_000_000_000   # seconds → nanoseconds

        # Recover strategy_id from cache if order is known, else use EXTERNAL
        strategy_id = StrategyId("EXTERNAL-001")
        cached_order = self._cache.order(client_order_id)
        if cached_order is not None:
            strategy_id = cached_order.strategy_id

        try:
            self.generate_order_filled(
                strategy_id,
                InstrumentId(Symbol(symbol), MT5_VENUE),
                client_order_id,
                venue_order_id,
                None,                        # venue_position_id
                trade_id,
                order_side,
                OrderType.MARKET,
                Quantity(deal.volume, sp),
                Price(deal.price, pp),
                instrument.quote_currency,
                commission,
                LiquiditySide.TAKER,
                ts_event,
            )
            self._log.info(
                f"MT5LiveExecutionClient: fill emitted — "
                f"order={deal.order} deal={deal.ticket} "
                f"{order_side.name} {deal.volume} {symbol} @ {deal.price}"
            )
        except Exception as exc:
            self._log.error(
                f"MT5LiveExecutionClient: failed to emit fill for deal {deal.ticket}: {exc}"
            )

    # ── Account state ────────────────────────────────────────────────────────

    async def _generate_account_state(self) -> None:
        """
        Push account balance/equity/margin to NautilusTrader's account engine.
        """
        try:
            snapshot = self._conn.get_account_info()
        except MT5ConnectionError as exc:
            self._log.warning(f"MT5LiveExecutionClient: cannot refresh account state: {exc}")
            return

        try:
            currency = _parse_account_currency(snapshot.currency)
        except Exception:
            currency = USD

        balances = [
            _make_account_balance(snapshot.balance, snapshot.equity, currency)
        ]

        self.generate_account_state(
            balances=balances,
            margins=[],
            reported=True,
            ts_event=self._clock.timestamp_ns(),
        )

    # ── Required report generators ───────────────────────────────────────────

    async def generate_order_status_report(
        self,
        instrument_id: InstrumentId,
        client_order_id: ClientOrderId | None = None,
        venue_order_id: VenueOrderId | None = None,
    ) -> OrderStatusReport | None:
        """
        Generate an OrderStatusReport for a specific order.
        Used by NautilusTrader for reconciliation.
        """
        self._conn.ensure_connected()

        ticket: int | None = None
        if client_order_id:
            ticket = self._client_order_id_to_ticket.get(str(client_order_id))
        if ticket is None and venue_order_id:
            try:
                ticket = int(venue_order_id.value)
            except (ValueError, AttributeError):
                pass

        if ticket is None:
            return None

        # Check pending orders
        orders = mt5.orders_get(ticket=ticket)
        if orders:
            order = orders[0]
            return _build_order_status_report(
                order, instrument_id, client_order_id, venue_order_id,
                self._clock.timestamp_ns(),
            )

        return None

    async def generate_order_status_reports(
        self,
        command,  # GenerateOrderStatusReports command object (NT 1.224+)
    ) -> list[OrderStatusReport]:
        """Generate OrderStatusReports for all known open orders."""
        self._conn.ensure_connected()
        reports = []

        # Extract fields from the command object
        instrument_id = getattr(command, "instrument_id", None)

        kwargs = {}
        if instrument_id:
            kwargs["symbol"] = instrument_id.symbol.value

        orders = mt5.orders_get(**kwargs) or ()
        for order in orders:
            if order.magic != self._config.magic_number:
                continue
            client_order_id_str = self._ticket_to_client_order_id.get(order.ticket)
            client_order_id = ClientOrderId(client_order_id_str) if client_order_id_str else None
            venue_order_id  = VenueOrderId(str(order.ticket))
            iid = instrument_id or InstrumentId(Symbol(order.symbol), MT5_VENUE)
            report = _build_order_status_report(
                order, iid, client_order_id, venue_order_id,
                self._clock.timestamp_ns(),
            )
            reports.append(report)

        return reports

    async def generate_fill_reports(
        self,
        command,  # GenerateFillReports command object (NT 1.224+)
    ) -> list[FillReport]:
        """Generate FillReports from MT5 deal history."""
        self._conn.ensure_connected()

        # Extract fields from the command object
        instrument_id  = getattr(command, "instrument_id", None)
        venue_order_id = getattr(command, "venue_order_id", None)
        start          = getattr(command, "start", None)
        end            = getattr(command, "end", None)

        from_dt = start or datetime(
            datetime.now().year, datetime.now().month, datetime.now().day,
            tzinfo=timezone.utc,
        )
        to_dt = end or datetime.now(timezone.utc)

        deals = mt5.history_deals_get(from_dt, to_dt) or ()
        reports = []

        symbol_filter = instrument_id.symbol.value if instrument_id else None

        # ================================================================
        # FIXED: Get account currency once for all deals
        # ================================================================
        try:
            account_info = mt5.account_info()
            if account_info and hasattr(account_info, 'currency'):
                account_currency = _parse_account_currency(account_info.currency)
            else:
                account_currency = USD
        except Exception:
            account_currency = USD

        for deal in deals:
            if deal.magic != self._config.magic_number:
                continue
            if symbol_filter and deal.symbol != symbol_filter:
                continue

            client_order_id_str = self._ticket_to_client_order_id.get(deal.order)
            client_order_id = ClientOrderId(client_order_id_str) if client_order_id_str else None
            iid = InstrumentId(Symbol(deal.symbol), MT5_VENUE)
            instrument = self._provider.get_instrument(deal.symbol)
            if instrument is None:
                continue

            pp = instrument.price_precision

            # Use account currency for commission (fixed)
            commission_amount = abs(deal.commission or 0.0)
            commission = Money(commission_amount, account_currency)

            report = FillReport(
                account_id=self.account_id,
                instrument_id=iid,
                venue_order_id=VenueOrderId(str(deal.order)),
                trade_id=TradeId(str(deal.ticket)),
                order_side=OrderSide.BUY if deal.type == mt5.DEAL_TYPE_BUY else OrderSide.SELL,
                last_qty=Quantity(deal.volume, instrument.size_precision),
                last_px=Price(deal.price, pp),
                commission=commission,
                liquidity_side=LiquiditySide.TAKER,
                report_id=UUID4(),
                ts_event=int(deal.time) * 1_000_000_000,
                ts_init=self._clock.timestamp_ns(),
                client_order_id=client_order_id,
            )
            reports.append(report)

        return reports

    async def generate_position_status_reports(
        self,
        command,  # GeneratePositionStatusReports command object (NT 1.224+)
    ) -> list[PositionStatusReport]:
        """Generate PositionStatusReports for open positions."""
        self._conn.ensure_connected()

        # Extract fields from the command object
        instrument_id = getattr(command, "instrument_id", None)

        kwargs = {}
        if instrument_id:
            kwargs["symbol"] = instrument_id.symbol.value

        positions = mt5.positions_get(**kwargs) or ()
        reports = []

        for pos in positions:
            if pos.magic != self._config.magic_number:
                continue

            iid = instrument_id or InstrumentId(Symbol(pos.symbol), MT5_VENUE)
            instrument = self._provider.get_instrument(pos.symbol)
            if instrument is None:
                continue

            pp = instrument.price_precision

            side = OrderSide.BUY if pos.type == mt5.ORDER_TYPE_BUY else OrderSide.SELL
            report = PositionStatusReport(
                account_id=self.account_id,
                instrument_id=iid,
                position_side=_order_side_to_position_side(side),
                quantity=Quantity(pos.volume, instrument.size_precision),
                report_id=UUID4(),
                ts_last=int(pos.time) * 1_000_000_000,
                ts_init=self._clock.timestamp_ns(),
            )
            reports.append(report)

        return reports

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _generate_order_accepted(self, order, venue_order_id: VenueOrderId) -> None:
        """Emit OrderAccepted into the execution engine."""
        self.generate_order_accepted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=venue_order_id,
            ts_event=self._clock.timestamp_ns(),
        )

    def _generate_order_rejected(self, order, reason: str) -> None:
        """Emit OrderRejected into the execution engine."""
        self._log.warning(
            f"MT5LiveExecutionClient: order rejected "
            f"client_order_id={order.client_order_id} reason={reason}"
        )
        self.generate_order_rejected(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            reason=reason,
            ts_event=self._clock.timestamp_ns(),
        )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_polling(self) -> bool:
        """True if the execution polling task is running."""
        return self._exec_poll_task is not None and not self._exec_poll_task.done()

    @property
    def known_order_count(self) -> int:
        return len(self._known_order_tickets)

    @property
    def known_position_count(self) -> int:
        return len(self._known_position_tickets)

    def __repr__(self) -> str:
        return (
            f"MT5LiveExecutionClient("
            f"account={self._config.account}, "
            f"orders={self.known_order_count}, "
            f"positions={self.known_position_count})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _time_in_force_to_mt5(tif: TimeInForce) -> int:
    """
    Convert NautilusTrader TimeInForce to MT5 order time-in-force constant.

    GTC → ORDER_TIME_GTC  (good till cancelled)
    DAY → ORDER_TIME_DAY  (good till end of day)
    IOC → ORDER_TIME_GTC  (closest MT5 equiv — IOC is handled by filling mode)
    FOK → ORDER_TIME_GTC  (same — handled by filling mode IOC/FOK)
    GTD → ORDER_TIME_SPECIFIED (needs expiration set separately)
    """
    mapping = {
        TimeInForce.GTC: mt5.ORDER_TIME_GTC,
        TimeInForce.DAY: mt5.ORDER_TIME_DAY,
        TimeInForce.IOC: mt5.ORDER_TIME_GTC,
        TimeInForce.FOK: mt5.ORDER_TIME_GTC,
        TimeInForce.GTD: mt5.ORDER_TIME_SPECIFIED,
    }
    return mapping.get(tif, mt5.ORDER_TIME_GTC)


def _parse_account_currency(code: str):
    """
    Parse account currency string, falling back to USD for unknown codes.

    NautilusTrader Currency.from_str() never raises — it auto-creates a
    crypto-type currency (iso4217=0) for anything it does not recognise.
    We guard against that by rejecting iso4217==0 codes unless they are
    known crypto currencies.
    """
    from nautilus_trader.model.currencies import Currency
    _KNOWN_CRYPTOS = frozenset({"BTC", "ETH", "XRP", "LTC", "BCH", "SOL", "ADA", "DOT"})
    code = code.strip().upper()
    try:
        currency = Currency.from_str(code)
        if currency.iso4217 == 0 and code not in _KNOWN_CRYPTOS:
            return USD
        return currency
    except Exception:
        return USD


def _make_account_balance(balance: float, equity: float, currency):
    """
    Build a NautilusTrader AccountBalance from MT5 account info snapshot.

    NautilusTrader enforces: total - locked == free (strict equality).
    MT5 exposes balance and equity separately but does not directly expose
    a "locked" margin figure in the AccountSnapshot we carry. We therefore
    use balance as both total and free with locked=0, which always satisfies
    the invariant. Unrealised P&L (equity - balance) is visible via positions.
    """
    from nautilus_trader.model.objects import AccountBalance
    return AccountBalance(
        total=Money(balance, currency),
        locked=Money(0.0, currency),
        free=Money(balance, currency),
    )


def _order_side_to_position_side(side: OrderSide):
    """Convert OrderSide to PositionSide."""
    from nautilus_trader.model.enums import PositionSide
    return PositionSide.LONG if side == OrderSide.BUY else PositionSide.SHORT


def _build_order_status_report(
    mt5_order,
    instrument_id: InstrumentId,
    client_order_id: ClientOrderId | None,
    venue_order_id: VenueOrderId | None,
    ts_init: int,
) -> OrderStatusReport:
    """Build an OrderStatusReport from a raw MT5 order namedtuple."""
    from nautilus_trader.execution.reports import OrderStatusReport

    side = _mt5_order_type_to_nautilus_side(mt5_order.type)

    # MT5 pending order types map to LIMIT or STOP
    pending_limit_types = {mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_SELL_LIMIT}
    pending_stop_types  = {mt5.ORDER_TYPE_BUY_STOP, mt5.ORDER_TYPE_SELL_STOP,
                           mt5.ORDER_TYPE_BUY_STOP_LIMIT, mt5.ORDER_TYPE_SELL_STOP_LIMIT}

    if mt5_order.type in pending_limit_types:
        order_type = OrderType.LIMIT
    elif mt5_order.type in pending_stop_types:
        order_type = OrderType.STOP_MARKET
    else:
        order_type = OrderType.MARKET

    return OrderStatusReport(
        account_id=AccountId(f"MT5-{mt5_order.magic}"),
        instrument_id=instrument_id,
        venue_order_id=venue_order_id or VenueOrderId(str(mt5_order.ticket)),
        order_side=side,
        order_type=order_type,
        time_in_force=TimeInForce.GTC,
        order_status=OrderStatus.ACCEPTED,
        quantity=Quantity(mt5_order.volume_initial, 2),
        filled_qty=Quantity(mt5_order.volume_initial - mt5_order.volume_current, 2),
        report_id=UUID4(),
        ts_accepted=int(mt5_order.time_setup) * 1_000_000_000,
        ts_last=int(mt5_order.time_setup) * 1_000_000_000,
        ts_init=ts_init,
        client_order_id=client_order_id,
        price=Price(mt5_order.price_open, 5) if mt5_order.price_open else None,
        post_only=False,
        reduce_only=False,
        cancel_reason=None,
    )

#fix2