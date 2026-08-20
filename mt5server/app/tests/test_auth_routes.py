import sys

from conftest import Fake
from routes.auth import auth_bp


def test_login_ok(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    monkeypatch.setattr(mt5, "login", lambda *a, **k: True)
    client = client_factory(auth_bp)
    resp = client.post("/login", json={"account": 12345678, "password": "p", "server": "s"})
    assert resp.status_code == 200
    assert resp.json["ok"] is True


def test_login_fail_reports_error(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    monkeypatch.setattr(mt5, "login", lambda *a, **k: False)
    monkeypatch.setattr(mt5, "last_error", lambda: (7, "invalid password"))
    client = client_factory(auth_bp)
    resp = client.post("/login", json={"account": 1, "password": "x", "server": "s"})
    assert resp.status_code == 200
    assert resp.json["ok"] is False
    assert resp.json["error"]["code"] == 7


def test_logout(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    monkeypatch.setattr(mt5, "shutdown", lambda: True)
    client = client_factory(auth_bp)
    assert client.post("/logout").json["ok"] is True


def test_terminal_info(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    info = Fake(name="MetaTrader 5", expert_enabled=True, trade_allowed=True)
    monkeypatch.setattr(mt5, "terminal_info", lambda: info)
    client = client_factory(auth_bp)
    resp = client.get("/terminal_info")
    assert resp.json["name"] == "MetaTrader 5"
    assert resp.json["expert_enabled"] is True


def test_last_error(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    monkeypatch.setattr(mt5, "last_error", lambda: (5, "failed"))
    client = client_factory(auth_bp)
    assert client.get("/last_error").json == {"code": 5, "message": "failed"}
