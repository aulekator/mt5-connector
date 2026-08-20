from unittest.mock import MagicMock, patch

import pytest

import mt5connect.backend as backend
from mt5connect.config import MT5Config
from mt5connect.errors import MT5ConfigError


def _remote_config():
    return MT5Config(account=1, password="p", server="s", symbols=["EURUSD"],
                     backend="remote", server_url="http://127.0.0.1:5000")


def _local_config():
    return MT5Config(account=1, password="p", server="s", symbols=["EURUSD"])


def test_set_backend_remote_binds_remote_mt5():
    # NOTE: remote_mt5 does not exist yet during Task 3 — patch importlib so it
    # returns the fake only for remote_mt5; other modules import normally.
    remote = MagicMock()
    real_import = backend.importlib.import_module

    def fake_import(name, *a, **k):
        if name == "mt5connect.remote_mt5":
            return remote
        return real_import(name, *a, **k)

    with patch.object(backend.importlib, "import_module", side_effect=fake_import):
        chosen = backend.set_backend(_remote_config())
        assert chosen is remote
        remote.configure.assert_called_once_with(
            server_url="http://127.0.0.1:5000", ws_url="ws://127.0.0.1:9000")
        from mt5connect import connection, data
        assert connection.mt5 is remote
        assert data.mt5 is remote


def test_set_backend_remote_without_server_url_raises():
    with pytest.raises(MT5ConfigError):
        backend.set_backend(MT5Config(account=1, password="p", server="s",
                                      symbols=["EURUSD"], backend="remote"))


def test_set_backend_local_without_mt5_raises():
    with patch.object(backend, "local_mt5", None):
        with pytest.raises(MT5ConfigError):
            backend.set_backend(_local_config())


def test_set_backend_local_binds_real_module():
    import MetaTrader5 as real  # conftest pre-mocks this on non-Windows
    chosen = backend.set_backend(_local_config())
    assert chosen is real
    from mt5connect import connection
    assert connection.mt5 is real
