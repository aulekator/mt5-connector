"""/mt5/* passthrough routes: turn MetaTrader5 calls into JSON."""
import sys
from datetime import datetime

import numpy as np
from flask import Blueprint, jsonify, request

mt5_bp = Blueprint("mt5", __name__)


def _json_num(v):
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def _rows(records, names):
    # records.tolist() yields plain tuples; map positionally via names.
    return [{n: _json_num(r[i]) for i, n in enumerate(names)} for r in records]


def _dt(value):
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.utcfromtimestamp(int(value))


def _maybe(result):
    if result is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(result._asdict())


def _import_mt5():
    import MetaTrader5 as mt5

    return mt5


@mt5_bp.route("/mt5/symbols_get", methods=["GET"])
def mt5_symbols_get():
    mt5 = _import_mt5()
    syms = mt5.symbols_get()
    if syms is None:
        return jsonify([])
    return jsonify([s._asdict() for s in syms])


@mt5_bp.route("/mt5/symbol_select", methods=["POST"])
def mt5_symbol_select():
    mt5 = _import_mt5()
    body = request.get_json(silent=True) or {}
    ok = mt5.symbol_select(body.get("symbol"), body.get("enabled", True))
    return jsonify({"ok": bool(ok)})


@mt5_bp.route("/mt5/symbol_info/<symbol>", methods=["GET"])
def mt5_symbol_info(symbol):
    mt5 = _import_mt5()
    return _maybe(mt5.symbol_info(symbol))


@mt5_bp.route("/mt5/symbol_info_tick/<symbol>", methods=["GET"])
def mt5_symbol_info_tick(symbol):
    mt5 = _import_mt5()
    return _maybe(mt5.symbol_info_tick(symbol))


@mt5_bp.route("/mt5/copy_rates_range", methods=["GET"])
def mt5_copy_rates_range():
    mt5 = _import_mt5()
    records = mt5.copy_rates_range(
        request.args.get("symbol"), int(request.args.get("timeframe", 0)),
        _dt(request.args.get("start")), _dt(request.args.get("end")))
    if records is None:
        return jsonify([])
    return jsonify(_rows(records.tolist(), records.dtype.names))


@mt5_bp.route("/mt5/copy_ticks_range", methods=["GET"])
def mt5_copy_ticks_range():
    mt5 = _import_mt5()
    records = mt5.copy_ticks_range(
        request.args.get("symbol"), _dt(request.args.get("start")),
        _dt(request.args.get("end")), int(request.args.get("flags", 0)))
    if records is None:
        return jsonify([])
    return jsonify(_rows(records.tolist(), records.dtype.names))


@mt5_bp.route("/mt5/order_send", methods=["POST"])
def mt5_order_send():
    mt5 = _import_mt5()
    result = mt5.order_send(request.get_json(silent=True) or {})
    return _maybe(result)


@mt5_bp.route("/mt5/orders_get", methods=["GET"])
def mt5_orders_get():
    mt5 = _import_mt5()
    orders = mt5.orders_get(
        ticket=_int_arg("ticket"), symbol=request.args.get("symbol"))
    if orders is None:
        return jsonify([])
    return jsonify([o._asdict() for o in orders])


@mt5_bp.route("/mt5/positions_get", methods=["GET"])
def mt5_positions_get():
    mt5 = _import_mt5()
    positions = mt5.positions_get(ticket=_int_arg("ticket"))
    if positions is None:
        return jsonify([])
    return jsonify([p._asdict() for p in positions])


@mt5_bp.route("/mt5/history_deals_get", methods=["GET"])
def mt5_history_deals_get():
    mt5 = _import_mt5()
    deals = mt5.history_deals_get(
        _dt(request.args.get("from")),
        _dt(request.args.get("to")) if request.args.get("to") else None,
        position=_int_arg("position"))
    if deals is None:
        return jsonify([])
    return jsonify([d._asdict() for d in deals])


@mt5_bp.route("/mt5/history_orders_get", methods=["GET"])
def mt5_history_orders_get():
    mt5 = _import_mt5()
    orders = mt5.history_orders_get(ticket=_int_arg("ticket"))
    if orders is None:
        return jsonify([])
    return jsonify([o._asdict() for o in orders])


def _int_arg(name):
    raw = request.args.get(name)
    return int(raw) if raw else None
