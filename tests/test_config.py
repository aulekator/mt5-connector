import pytest

from mt5connect.backend import derive_ws_url
from mt5connect.config import MT5Config
from mt5connect.errors import MT5ConfigError


def _base(backend="local", **kw):
    return MT5Config(account=1, password="p", server="s", symbols=["EURUSD"],
                     backend=backend, **kw)


def test_default_backend_is_local():
    assert _base().backend == "local"


def test_invalid_backend_rejected():
    with pytest.raises(MT5ConfigError):
        _base(backend="nope")


def test_remote_requires_server_url():
    with pytest.raises(MT5ConfigError):
        _base(backend="remote")


def test_remote_with_server_url_ok():
    c = _base(backend="remote", server_url="http://192.168.1.10:5000")
    assert c.server_url == "http://192.168.1.10:5000"


def test_ws_url_derived_when_omitted():
    c = _base(backend="remote", server_url="http://192.168.1.10:5000")
    assert c.ws_url == "ws://192.168.1.10:9000"


def test_ws_url_explicit_wins():
    c = _base(backend="remote", server_url="http://192.168.1.10:5000",
              ws_url="ws://192.168.1.10:9001")
    assert c.ws_url == "ws://192.168.1.10:9001"


def test_derive_ws_url_http():
    assert derive_ws_url("http://localhost:5000") == "ws://localhost:9000"


def test_derive_ws_url_already_ws():
    assert derive_ws_url("ws://localhost:9000") == "ws://localhost:9000"
