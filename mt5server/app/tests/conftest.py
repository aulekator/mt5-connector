import sys
from unittest.mock import MagicMock

import pytest

_mt5 = MagicMock()
sys.modules.setdefault("MetaTrader5", _mt5)


class Fake:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def _asdict(self):
        return dict(self.__dict__)


def _tick(**kw):
    base = dict(time=1704067200, bid=1.08, ask=1.0801, last=0.0, volume=0,
                time_msc=1704067200123, flags=2, volume_real=0.0)
    base.update(kw)
    return Fake(**base)


@pytest.fixture
def client_factory():
    def _make(blueprint):
        from flask import Flask

        app = Flask(__name__)
        app.register_blueprint(blueprint)
        return app.test_client()

    return _make
