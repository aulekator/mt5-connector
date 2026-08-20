import sys

from conftest import Fake, _tick
from routes.mt5 import mt5_bp


def test_symbol_info_tick(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    monkeypatch.setattr(mt5, "symbol_info_tick", lambda s: _tick())
    client = client_factory(mt5_bp)
    resp = client.get("/mt5/symbol_info_tick/EURUSD")
    assert resp.status_code == 200
    assert resp.json["bid"] == 1.08
    assert resp.json["time"] == 1704067200


def test_symbol_info_tick_none_returns_404(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    monkeypatch.setattr(mt5, "symbol_info_tick", lambda s: None)
    client = client_factory(mt5_bp)
    assert client.get("/mt5/symbol_info_tick/EURUSD").status_code == 404


def test_order_send_passthrough(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    monkeypatch.setattr(mt5, "order_send", lambda req: _tick(retcode=10009, comment="done"))
    client = client_factory(mt5_bp)
    resp = client.post("/mt5/order_send", json={"action": 1, "symbol": "EURUSD"})
    assert resp.status_code == 200
    assert resp.json["retcode"] == 10009


def test_copy_rates_range_raw_epochs(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]

    class FakeRates:
        dtype = type("DType", (), {"names": ["time", "open", "close"]})

        def tolist(self):
            return [(1704067200, 1.08, 1.085)]

    monkeypatch.setattr(mt5, "copy_rates_range", lambda *a, **k: FakeRates())
    client = client_factory(mt5_bp)
    resp = client.get("/mt5/copy_rates_range", query_string={
        "symbol": "EURUSD", "timeframe": 16385,
        "start": "2024-01-01T00:00:00+00:00", "end": "2024-01-02T00:00:00+00:00"})
    assert resp.status_code == 200
    assert resp.json[0]["time"] == 1704067200


def test_symbols_get(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    monkeypatch.setattr(mt5, "symbols_get", lambda: [Fake(name="EURUSD", digits=5)])
    client = client_factory(mt5_bp)
    resp = client.get("/mt5/symbols_get")
    assert resp.json == [{"name": "EURUSD", "digits": 5}]


def test_symbol_select(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    monkeypatch.setattr(mt5, "symbol_select", lambda s, e: True)
    client = client_factory(mt5_bp)
    resp = client.post("/mt5/symbol_select", json={"symbol": "EURUSD", "enabled": True})
    assert resp.json == {"ok": True}


def test_symbol_info(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    monkeypatch.setattr(mt5, "symbol_info", lambda s: Fake(name="EURUSD", digits=5))
    client = client_factory(mt5_bp)
    assert client.get("/mt5/symbol_info/EURUSD").json["digits"] == 5


def test_copy_ticks_range(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]

    class FakeTicks:
        dtype = type("DType", (), {"names": ["time", "bid", "ask"]})

        def tolist(self):
            return [(1704067200, 1.08, 1.0801)]

    monkeypatch.setattr(mt5, "copy_ticks_range", lambda *a, **k: FakeTicks())
    client = client_factory(mt5_bp)
    resp = client.get("/mt5/copy_ticks_range", query_string={
        "symbol": "EURUSD", "start": "2024-01-01T00:00:00+00:00",
        "end": "2024-01-02T00:00:00+00:00", "flags": 0})
    assert resp.json[0]["bid"] == 1.08


def test_orders_get(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    monkeypatch.setattr(mt5, "orders_get", lambda **k: [Fake(ticket=1, magic=510)])
    client = client_factory(mt5_bp)
    resp = client.get("/mt5/orders_get")
    assert resp.json == [{"ticket": 1, "magic": 510}]


def test_positions_get(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    monkeypatch.setattr(mt5, "positions_get", lambda **k: [Fake(ticket=9)])
    client = client_factory(mt5_bp)
    assert client.get("/mt5/positions_get").json == [{"ticket": 9}]


def test_history_deals_get(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    monkeypatch.setattr(mt5, "history_deals_get", lambda *a, **k: [Fake(ticket=42)])
    client = client_factory(mt5_bp)
    assert client.get("/mt5/history_deals_get").json == [{"ticket": 42}]


def test_history_orders_get(client_factory, monkeypatch):
    mt5 = sys.modules["MetaTrader5"]
    monkeypatch.setattr(mt5, "history_orders_get", lambda **k: [Fake(ticket=1)])
    client = client_factory(mt5_bp)
    assert client.get("/mt5/history_orders_get").json == [{"ticket": 1}]
