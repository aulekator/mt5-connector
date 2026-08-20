"""Auth and terminal routes for the MT5 server."""
from flask import Blueprint, jsonify, request

auth_bp = Blueprint("auth", __name__)


def _import_mt5():
    import MetaTrader5 as mt5

    return mt5


@auth_bp.route("/login", methods=["POST"])
def login():
    mt5 = _import_mt5()
    body = request.get_json(silent=True) or {}
    ok = mt5.login(int(body.get("account", 0)), body.get("password", ""),
                   body.get("server", ""))
    if ok:
        return jsonify({"ok": True, "error": None})
    code, msg = mt5.last_error()
    return jsonify({"ok": False, "error": {"code": code, "message": msg}})


@auth_bp.route("/logout", methods=["POST"])
def logout():
    mt5 = _import_mt5()
    mt5.shutdown()
    return jsonify({"ok": True})


@auth_bp.route("/terminal_info", methods=["GET"])
def terminal_info():
    mt5 = _import_mt5()
    info = mt5.terminal_info()
    if info is None:
        return jsonify({"error": "terminal info unavailable"}), 404
    return jsonify(info._asdict())
