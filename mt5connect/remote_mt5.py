"""MetaTrader5-API-compatible remote shim backed by the MT5 server REST API.

Mirrors the subset of the MetaTrader5 Python library used by mt5connect.
Structs support attribute access and ``_asdict()`` so existing call sites
and parsing.py work unchanged. The live tick stream uses the same ``Tick``
struct via WebSocket (see mt5connect.ws_stream).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, fields
from datetime import datetime

logger = logging.getLogger(__name__)

_server_url: str | None = None
_ws_url: str | None = None
_last_error: tuple[int, str] = (0, "No error")


def configure(server_url: str, ws_url: str | None = None) -> None:
    global _server_url, _ws_url
    _server_url = server_url.rstrip("/")
    _ws_url = ws_url


# --- Constants (values match the official MetaTrader5 Python library) ---
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT = 2, 3
ORDER_TYPE_BUY_STOP, ORDER_TYPE_SELL_STOP = 4, 5
ORDER_TYPE_BUY_STOP_LIMIT, ORDER_TYPE_SELL_STOP_LIMIT = 6, 7
TRADE_ACTION_DEAL, TRADE_ACTION_PENDING = 1, 5
TRADE_ACTION_SLTP, TRADE_ACTION_MODIFY, TRADE_ACTION_REMOVE = 6, 7, 8
ORDER_TIME_GTC, ORDER_TIME_DAY, ORDER_TIME_SPECIFIED = 0, 1, 2
TRADE_RETCODE_DONE, TRADE_RETCODE_PLACED = 10009, 10008
TRADE_RETCODE_DONE_PARTIAL = 10010
DEAL_TYPE_BUY, DEAL_TYPE_SELL = 0, 1
POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30 = 1, 5, 15, 30
TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 16385, 16388, 16408
COPY_TICKS_ALL, COPY_TICKS_INFO, COPY_TICKS_TRADE = 0, 1, 2


class MT5Struct:
    """Base class for shim structs: attribute access plus ``_asdict()``."""

    def _asdict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass
class Tick(MT5Struct):
    time: int = 0
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: int = 0
    time_msc: int = 0
    flags: int = 0
    volume_real: float = 0.0


@dataclass
class SymbolInfo(MT5Struct):
    name: str = ""
    digits: int = 0
    volume_step: float = 0.0
    volume_min: float = 0.0
    volume_max: float = 0.0
    trade_contract_size: float = 0.0
    margin_initial: float = 0.0
    margin_maintenance: float = 0.0
    currency_base: str = ""
    currency_profit: str = ""
    filling_mode: int = 0
    description: str = ""
    trade_mode: int = 0
    visible: bool = False
    select: bool = False
    volume: float = 0.0
    spread: int = 0
    bid: float = 0.0
    ask: float = 0.0
    point: float = 0.0


@dataclass
class AccountInfo(MT5Struct):
    login: int = 0
    trade_mode: int = 0
    leverage: int = 0
    limit_orders: int = 0
    margin_so_mode: int = 0
    trade_allowed: bool = False
    trade_expert: bool = False
    margin_mode: int = 0
    currency_digits: int = 0
    fifo_close: bool = False
    balance: float = 0.0
    credit: float = 0.0
    profit: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    margin_free: float = 0.0
    margin_level: float = 0.0
    margin_so_call: float = 0.0
    margin_so_so: float = 0.0
    margin_initial: float = 0.0
    margin_maintenance: float = 0.0
    assets: float = 0.0
    liabilities: float = 0.0
    commission_blocked: float = 0.0
    name: str = ""
    server: str = ""
    currency: str = ""
    company: str = ""


@dataclass
class TerminalInfo(MT5Struct):
    name: str = ""
    company: str = ""
    version: int = 0
    build: int = 0
    terminal_path: str = ""
    data_path: str = ""
    trade_allowed: bool = False
    tradeapi_disabled: bool = False
    expert_enabled: bool = False
    margin_mode: int = 0
    connection: int = 0
    volume_limit: int = 0


@dataclass
class Order(MT5Struct):
    ticket: int = 0
    time_setup: int = 0
    time_setup_msc: int = 0
    time_done: int = 0
    time_done_msc: int = 0
    time_expiration: int = 0
    type: int = 0
    type_time: int = 0
    type_filling: int = 0
    state: int = 0
    magic: int = 0
    position_id: int = 0
    position_by_id: int = 0
    reason: int = 0
    volume_current: float = 0.0
    volume_initial: float = 0.0
    price_open: float = 0.0
    price_stop_limit: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    price_current: float = 0.0
    symbol: str = ""
    comment: str = ""
    external_id: str = ""


@dataclass
class Position(MT5Struct):
    ticket: int = 0
    time: int = 0
    time_msc: int = 0
    time_update: int = 0
    time_update_msc: int = 0
    type: int = 0
    magic: int = 0
    identifier: int = 0
    reason: int = 0
    volume: float = 0.0
    price_open: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    price_current: float = 0.0
    swap: float = 0.0
    profit: float = 0.0
    symbol: str = ""
    comment: str = ""
    external_id: str = ""


@dataclass
class Deal(MT5Struct):
    ticket: int = 0
    order: int = 0
    time: int = 0
    time_msc: int = 0
    type: int = 0
    entry: int = 0
    magic: int = 0
    position_id: int = 0
    reason: int = 0
    volume: float = 0.0
    price: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    profit: float = 0.0
    fee: float = 0.0
    symbol: str = ""
    comment: str = ""
    external_id: str = ""


@dataclass
class OrderResult(MT5Struct):
    retcode: int = 0
    deal: int = 0
    order: int = 0
    volume: float = 0.0
    price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    comment: str = ""
    request_id: int = 0
    retcode_external: int = 0


def from_dict(cls, data: dict | None):
    """Build a struct from a JSON dict, dropping unknown keys."""
    if data is None:
        return None
    allowed = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in allowed})


def tick_from_ws(payload: dict) -> Tick:
    """Convert an EA WebSocket tick message (all-string JSON) to a Tick.

    The EA message has no ``type`` key, stringifies all numerics, and
    names the epoch millisecond field ``time_msec``. The formatted
    ``time`` string is ignored; epoch seconds are derived from
    ``time_msec``.
    """
    time_msc = int(payload.get("time_msec", "0"))
    return Tick(
        time=time_msc // 1000,
        bid=float(payload.get("bid", "0.0")),
        ask=float(payload.get("ask", "0.0")),
        last=float(payload.get("last", "0.0")),
        volume=int(float(payload.get("volume", "0"))),
        time_msc=time_msc,
        flags=int(payload.get("flags", "0")),
        volume_real=float(payload.get("volume_real", "0.0")),
    )


def last_error() -> tuple[int, str]:
    """Return the most recent error recorded from a failed server call."""
    return _last_error


def _request(method: str, path: str, params: dict | None = None,
             json_body: dict | None = None, timeout: float | None = None):
    """Call the server; returns parsed JSON (dict/list) or None on failure."""
    global _last_error
    url = f"{_server_url}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method=method)
    if json_body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(json_body).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout or 10) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except Exception as exc:
        _last_error = (5, f"MT5 server request failed: {exc}")
        logger.warning("remote_mt5: %s %s failed: %s", method, path, exc)
        return None


def from_list(cls, data: list | None):
    """Build a list of structs from a JSON list, or None."""
    if data is None:
        return None
    return [from_dict(cls, d) for d in data]


def initialize() -> bool:
    data = _request("GET", "/health")
    return bool(data.get("mt5_initialized", False)) if data else False


def login(login: int, password: str, server: str, timeout: int | None = None) -> bool:
    global _last_error
    data = _request("POST", "/login", json_body={
        "account": login, "password": password, "server": server},
        timeout=timeout / 1000 if timeout else None)
    if data is None:
        return False
    if not data.get("ok", False):
        err = data.get("error") or {}
        _last_error = (int(err.get("code", 1)), str(err.get("message", "login failed")))
        return False
    return True


def shutdown() -> None:
    _request("POST", "/logout")


def account_info() -> AccountInfo | None:
    return from_dict(AccountInfo, _request("GET", "/account"))


def terminal_info() -> TerminalInfo | None:
    return from_dict(TerminalInfo, _request("GET", "/terminal_info"))


def symbols_get() -> list[SymbolInfo] | None:
    return from_list(SymbolInfo, _request("GET", "/mt5/symbols_get"))


def symbol_select(symbol: str, enable: bool = True) -> bool:
    data = _request("POST", "/mt5/symbol_select",
                    json_body={"symbol": symbol, "enabled": bool(enable)})
    return bool(data and data.get("ok", False))


def symbol_info(symbol: str) -> SymbolInfo | None:
    return from_dict(
        SymbolInfo, _request("GET", f"/mt5/symbol_info/{urllib.parse.quote(symbol)}"))


def symbol_info_tick(symbol: str) -> Tick | None:
    return from_dict(
        Tick, _request("GET", f"/mt5/symbol_info_tick/{urllib.parse.quote(symbol)}"))


def _dt_param(value) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(int(value))


def copy_rates_range(symbol: str, timeframe: int, start, end) -> list[dict] | None:
    return _request("GET", "/mt5/copy_rates_range", params={
        "symbol": symbol, "timeframe": timeframe,
        "start": _dt_param(start), "end": _dt_param(end)})


def copy_ticks_range(symbol: str, start, end, flags: int) -> list[Tick] | None:
    data = _request("GET", "/mt5/copy_ticks_range", params={
        "symbol": symbol, "start": _dt_param(start), "end": _dt_param(end),
        "flags": flags})
    return from_list(Tick, data)


def order_send(request: dict) -> OrderResult | None:
    return from_dict(OrderResult, _request("POST", "/mt5/order_send", json_body=request))


def orders_get(ticket: int | None = None, symbol: str | None = None) -> list[Order] | None:
    params = {}
    if ticket is not None:
        params["ticket"] = ticket
    if symbol is not None:
        params["symbol"] = symbol
    return from_list(Order, _request("GET", "/mt5/orders_get", params=params or None))


def positions_get(ticket: int | None = None) -> list[Position] | None:
    params = {"ticket": ticket} if ticket is not None else None
    return from_list(Position, _request("GET", "/mt5/positions_get", params=params))


def history_deals_get(from_date, to_date, position: int | None = None) -> list[Deal] | None:
    params = {"from": _dt_param(from_date)}
    if to_date is not None:
        params["to"] = _dt_param(to_date)
    if position is not None:
        params["position"] = position
    return from_list(Deal, _request("GET", "/mt5/history_deals_get", params=params))


def history_orders_get(ticket: int | None = None) -> list[Order] | None:
    params = {"ticket": ticket} if ticket is not None else None
    return from_list(Order, _request("GET", "/mt5/history_orders_get", params=params))
