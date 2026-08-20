"""Backend selection and the remote ws_url derivation helper."""
from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlunparse

from mt5connect.errors import MT5ConfigError

if TYPE_CHECKING:
    from mt5connect.config import MT5Config

logger = logging.getLogger(__name__)


def derive_ws_url(server_url: str) -> str:
    """Derive the WebSocket hub URL from the REST server URL (port 9000)."""
    p = urlparse(server_url)
    if p.scheme == "ws" and p.port == 9000:
        return server_url
    host = p.hostname or "localhost"
    return urlunparse(("ws", f"{host}:9000", p.path, "", "", ""))


try:
    import MetaTrader5 as local_mt5
except ImportError:  # pragma: no cover - Windows-only dependency
    local_mt5 = None

_CONSUMER_MODULES = (
    "mt5connect.connection",
    "mt5connect.data",
    "mt5connect.providers",
    "mt5connect.execution",
    "mt5connect.downloader",
)


def set_backend(config: "MT5Config"):
    """Bind the active MT5 backend module into the consumer modules.

    ``local`` uses the real MetaTrader5 library (Windows); ``remote`` uses
    ``mt5connect.remote_mt5`` which talks to the MT5 server over HTTP/WS.
    """
    if config.backend == "remote":
        if not config.server_url:
            raise MT5ConfigError("backend='remote' requires server_url")
        remote = importlib.import_module("mt5connect.remote_mt5")
        remote.configure(server_url=config.server_url, ws_url=config.ws_url)
        chosen = remote
    else:
        if local_mt5 is None:
            raise MT5ConfigError(
                "backend='local' requires the MetaTrader5 package (Windows-only). "
                "Install on Windows, or set backend='remote'."
            )
        chosen = local_mt5

    for name in _CONSUMER_MODULES:
        mod = importlib.import_module(name)
        mod.mt5 = chosen

    logger.debug("set_backend: active backend = %s", getattr(chosen, "__name__", chosen))
    return chosen
