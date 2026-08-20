import json
import urllib.error
from datetime import datetime, timezone

import pytest

import mt5connect.remote_mt5 as rmt5


def test_constants_match_real_values():
    assert rmt5.ORDER_TYPE_BUY == 0
    assert rmt5.ORDER_FILLING_IOC == 1
    assert rmt5.TRADE_ACTION_DEAL == 1
    assert rmt5.TRADE_RETCODE_DONE == 10009
    assert rmt5.TIMEFRAME_H1 == 16385
    assert rmt5.COPY_TICKS_ALL == 0


def test_tick_struct_attribute_access():
    t = rmt5.Tick(time=123, bid=1.0, ask=1.1, last=0.0, volume=5, time_msc=123000,
                  flags=2, volume_real=5.0)
    assert t.bid == 1.0 and t.ask == 1.1 and t.time_msc == 123000
    assert t._asdict()["bid"] == 1.0


def test_symbol_info_struct():
    s = rmt5.SymbolInfo(name="EURUSDm", digits=5, volume_step=0.01, volume_min=0.01,
                        volume_max=100.0, trade_contract_size=100000.0,
                        margin_initial=0.0, margin_maintenance=0.0,
                        currency_base="EUR", currency_profit="USD", filling_mode=3)
    assert s.name == "EURUSDm" and s.filling_mode == 3
    assert s._asdict()["currency_base"] == "EUR"


def test_order_result_struct():
    r = rmt5.OrderResult(retcode=10009, deal=0, order=12345, volume=0.1, price=1.08,
                         bid=1.0799, ask=1.0801, comment="done", request_id=0,
                         retcode_external=0)
    assert r.retcode == 10009 and r.comment == "done" and r.order == 12345


def test_from_dict_drops_unknown_keys():
    t = rmt5.from_dict(rmt5.Tick, {"bid": 1.0, "ask": 1.1, "bogus": 9})
    assert t is not None and t.bid == 1.0 and t.ask == 1.1


def _stub(method, path, result):
    captured = {"url": None, "body": None}

    class FakeResp:
        _data = json.dumps(result).encode()

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = req.data
        return FakeResp()

    return captured, fake_urlopen


def test_request_success(monkeypatch):
    captured, fake = _stub("GET", "/health", {"ok": True})
    monkeypatch.setattr(rmt5.urllib.request, "urlopen", fake)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    assert rmt5._request("GET", "/health") == {"ok": True}
    assert captured["url"].startswith("http://127.0.0.1:5000/health")


def test_request_transport_error_records_last_error(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(rmt5.urllib.request, "urlopen", boom)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    rmt5._last_error = (0, "")
    assert rmt5._request("GET", "/health") is None
    assert rmt5.last_error()[0] != 0


def test_initialize_hits_health(monkeypatch):
    _, fake = _stub("GET", "/health", {"status": "healthy", "mt5_initialized": True})
    monkeypatch.setattr(rmt5.urllib.request, "urlopen", fake)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    assert rmt5.initialize() is True


def test_initialize_false_when_server_down(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(rmt5.urllib.request, "urlopen", boom)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    assert rmt5.initialize() is False


def test_login_posts_credentials(monkeypatch):
    captured, fake = _stub("POST", "/login", {"ok": True, "error": None})
    monkeypatch.setattr(rmt5.urllib.request, "urlopen", fake)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    assert rmt5.login(12345678, "secret", "Exness-MT5Trial9") is True
    body = json.loads(captured["body"])
    assert body == {"account": 12345678, "password": "secret", "server": "Exness-MT5Trial9"}


def test_login_false_records_error(monkeypatch):
    _, fake = _stub("POST", "/login", {"ok": False, "error": {"code": 7, "message": "bad"}})
    monkeypatch.setattr(rmt5.urllib.request, "urlopen", fake)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    assert rmt5.login(1, "x", "s") is False
    assert rmt5.last_error()[0] == 7


def test_account_info_returns_struct(monkeypatch):
    acc = {"login": 12345678, "balance": 10000.0, "equity": 10050.25, "currency": "USD",
           "leverage": 2000, "name": "T", "server": "S", "margin": 100.0,
           "margin_free": 9950.0, "margin_level": 10050.0, "profit": 50.25,
           "company": "C"}
    _, fake = _stub("GET", "/account", acc)
    monkeypatch.setattr(rmt5.urllib.request, "urlopen", fake)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    info = rmt5.account_info()
    assert info is not None
    assert info.login == 12345678 and info.balance == 10000.0
    assert info._asdict()["currency"] == "USD"


def test_symbols_get_returns_list(monkeypatch):
    _, fake = _stub("GET", "/mt5/symbols_get", [{"name": "EURUSD", "digits": 5}])
    monkeypatch.setattr(rmt5.urllib.request, "urlopen", fake)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    syms = rmt5.symbols_get()
    assert syms is not None and syms[0].name == "EURUSD" and syms[0].digits == 5


def test_symbol_info_tick_returns_tick(monkeypatch):
    tick = {"time": 1712345678, "bid": 1.08, "ask": 1.0801, "last": 0.0, "volume": 0,
            "time_msc": 1712345678123, "flags": 2, "volume_real": 0.0}
    _, fake = _stub("GET", "/mt5/symbol_info_tick/EURUSD", tick)
    monkeypatch.setattr(rmt5.urllib.request, "urlopen", fake)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    t = rmt5.symbol_info_tick("EURUSD")
    assert t is not None and t.bid == 1.08 and t.time_msc == 1712345678123


def test_copy_rates_range_rows_are_dicts(monkeypatch):
    rows = [{"time": 1704067200, "open": 1.08, "high": 1.09, "low": 1.07,
             "close": 1.085, "tick_volume": 100, "spread": 1, "real_volume": 0}]
    captured, fake = _stub("GET", "/mt5/copy_rates_range", rows)
    monkeypatch.setattr(rmt5.urllib.request, "urlopen", fake)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    out = rmt5.copy_rates_range("EURUSD", rmt5.TIMEFRAME_H1,
                                datetime(2024, 1, 1, tzinfo=timezone.utc),
                                datetime(2024, 1, 2, tzinfo=timezone.utc))
    assert out is not None
    assert out[0]["time"] == 1704067200 and out[0]["close"] == 1.085


def test_copy_ticks_range_returns_ticks(monkeypatch):
    ticks = [{"time": 1704067200, "bid": 1.08, "ask": 1.0801, "last": 0.0, "volume": 0,
              "time_msc": 1704067200123, "flags": 2, "volume_real": 0.0}]
    _, fake = _stub("GET", "/mt5/copy_ticks_range", ticks)
    monkeypatch.setattr(rmt5.urllib.request, "urlopen", fake)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    out = rmt5.copy_ticks_range("EURUSD", datetime(2024, 1, 1, tzinfo=timezone.utc),
                                datetime(2024, 1, 2, tzinfo=timezone.utc),
                                rmt5.COPY_TICKS_ALL)
    assert out is not None and out[0].bid == 1.08


def test_order_send_passes_request(monkeypatch):
    result = {"retcode": 10009, "deal": 0, "order": 77, "volume": 0.1, "price": 1.08,
              "bid": 1.0799, "ask": 1.0801, "comment": "done", "request_id": 0,
              "retcode_external": 0}
    captured, fake = _stub("POST", "/mt5/order_send", result)
    monkeypatch.setattr(rmt5.urllib.request, "urlopen", fake)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    req = {"action": rmt5.TRADE_ACTION_DEAL, "symbol": "EURUSD", "volume": 0.1}
    res = rmt5.order_send(req)
    assert res is not None and res.retcode == 10009 and res.order == 77
    assert json.loads(captured["body"]) == req


def test_orders_get_with_ticket(monkeypatch):
    orders = [{"ticket": 1, "magic": 510, "symbol": "EURUSD", "type": 0,
               "volume": 0.1, "price_open": 1.08}]
    _, fake = _stub("GET", "/mt5/orders_get", orders)
    monkeypatch.setattr(rmt5.urllib.request, "urlopen", fake)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    out = rmt5.orders_get(ticket=1)
    assert out is not None and out[0].magic == 510


def test_positions_get(monkeypatch):
    _, fake = _stub("GET", "/mt5/positions_get",
                    [{"ticket": 9, "magic": 510, "symbol": "EURUSD", "volume": 0.1}])
    monkeypatch.setattr(rmt5.urllib.request, "urlopen", fake)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    out = rmt5.positions_get()
    assert out is not None and out[0].ticket == 9


def test_history_deals_get(monkeypatch):
    deals = [{"ticket": 42, "magic": 510, "time": 1704067200, "symbol": "EURUSD",
              "volume": 0.1, "price": 1.08, "profit": 0.0}]
    _, fake = _stub("GET", "/mt5/history_deals_get", deals)
    monkeypatch.setattr(rmt5.urllib.request, "urlopen", fake)
    rmt5.configure(server_url="http://127.0.0.1:5000")
    out = rmt5.history_deals_get(datetime(2024, 1, 1, tzinfo=timezone.utc), None)
    assert out is not None and out[0].magic == 510


def test_tick_from_ws_converts_new_format():
    tick = rmt5.tick_from_ws({
        "symbol": "EURUSD", "time": "2024.06.01 10:00:00",
        "ask": "1.0854", "bid": "1.0852", "volume": "0", "last": "0.0",
        "time_msec": "1712345678123", "flags": "2",
    })
    assert tick.time == 1712345678            # time_msec // 1000
    assert tick.time_msc == 1712345678123
    assert tick.bid == 1.0852 and tick.ask == 1.0854
    assert tick.last == 0.0 and tick.volume == 0 and tick.flags == 2


def test_tick_from_ws_missing_fields_default():
    tick = rmt5.tick_from_ws({"bid": "1.1"})
    assert tick.bid == 1.1 and tick.ask == 0.0
    assert tick.time == 0 and tick.time_msc == 0
    assert tick.volume == 0 and tick.flags == 0 and tick.volume_real == 0.0
