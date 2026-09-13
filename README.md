# mt5-connector

**Unofficial community MetaTrader 5 adapter for NautilusTrader** — live trading and backtesting on any MT5 broker (Exness, IC Markets, Pepperstone, and more).

> ⚠️ **Disclaimer:** This is an independent community project. It is **not** affiliated with, endorsed by, or supported by [Nautech Systems Pty Ltd](https://nautilustrader.io) or the official [NautilusTrader](https://nautilustrader.io) project.

[![PyPI version](https://badge.fury.io/py/mt5-connector.svg)](https://badge.fury.io/py/mt5-connector)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform: Windows | Linux](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey.svg)](#linux--docker-remote-backend)
[![Unofficial](https://img.shields.io/badge/NautilusTrader-unofficial%20community%20adapter-orange.svg)](https://nautilustrader.io)

---

## What this is

`mt5-connector` is a **data and execution adapter** that connects [NautilusTrader](https://nautilustrader.io) to any MetaTrader 5 broker. Write your strategy once in Python, then run it as a backtest against historical MT5 data or flip a switch and run it live.

**Windows (local mode)** — connects directly to a running MT5 terminal via Windows IPC:

```
MT5 Terminal (Windows) ←→ mt5-connector ←→ NautilusTrader
                              ↑
                    tick polling, order routing,
                    account state, reconciliation
```

**Linux / Docker (remote mode)** — runs MT5 inside a Docker container on any Linux machine or VPS:

```
Ubuntu VPS / Linux Machine
├── Docker container
│   ├── Wine → MT5 Terminal
│   ├── Flask HTTP server (port 5000)
│   └── WebSocket tick hub (port 9000)
│
└── mt5-connector (remote backend) ←→ NautilusTrader
```

**What you get:**

- Live tick data — polled on Windows, streamed via WebSocket on Linux/Docker
- Full order lifecycle: market, limit, stop, stop-limit orders with SL/TP
- Account state and position reconciliation on startup and continuously
- Historical bar data download into a NautilusTrader Parquet catalog for backtesting
- Automatic reconnection with exponential backoff
- Works with any MT5 broker — Exness, IC Markets, Pepperstone, OANDA, and more
- **Linux/VPS support** via Dockerized MT5 server (v0.7.0+)

---

## Table of contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Configuration](#configuration)
  - [Symbol naming](#symbol-naming)
- [Writing a strategy](#writing-a-strategy)
- [Backtesting](#backtesting)
- [Live trading](#live-trading)
- [Linux / Docker remote backend](#linux--docker-remote-backend)
  - [What it is](#what-it-is)
  - [Security notice](#security-notice)
  - [Requirements](#requirements-1)
  - [Quick start](#quick-start-1)
  - [Persistence](#persistence)
- [Running the full test suite](#running-the-full-test-suite)
- [Project structure](#project-structure)
- [Broker compatibility](#broker-compatibility)
- [Troubleshooting](#troubleshooting)
- [Safety notes](#safety-notes)
- [Changelog](#changelog)
- [License](#license)

---

## Requirements

**Windows (local mode):**
- Windows 10 or 11
- Python 3.10, 3.11, or 3.12
- MetaTrader 5 terminal installed and open, logged in to your broker account
- An MT5 broker account (demo accounts work perfectly for development)

**Linux / Docker (remote mode):**
- Ubuntu 20.04+ or any Linux distribution with Docker
- Python 3.10, 3.11, or 3.12
- Docker and Docker Compose
- An MT5 broker account

---

## Installation

```bash
pip install mt5-connector
```

Or install from source for development:

```bash
git clone https://github.com/aulekator/mt5-connector
cd mt5-connector
pip install -e ".[dev]"
```

---

## Quick start

**1. Create a `.env` file** in your project root with your broker credentials:

```bash
# .env — never commit this file
MT5_ACCOUNT=12345678
MT5_PASSWORD=your_password
MT5_SERVER=Exness-MT5Trial9
MT5_SYMBOLS=EURUSDm,XAUUSDm
```

Find your server name in MT5 → File → Open Account → search your broker.

**2. Open MT5 and log in.** The adapter connects to the running terminal via Windows IPC — the terminal must be open before you run any script.

**3. Enable AutoTrading** in the MT5 toolbar (the button should show a green dot). Without this, order_send calls will be rejected.

**4. Test the connection:**

```python
import MetaTrader5 as mt5

mt5.initialize()
mt5.login(12345678, "your_password", "Exness-MT5Trial9")
print(mt5.account_info())
mt5.shutdown()
```

**5. Run the example live strategy:**

```bash
python examples/live_simple_strategy.py
```

---

## Configuration

All configuration goes through `MT5Config`. The only required fields are your account credentials and symbols.

```python
from mt5connect.config import MT5Config

config = MT5Config(
    account  = 12345678,            # MT5 account number
    password = "your_password",
    server   = "Exness-MT5Trial9",  # broker server name
    symbols  = ["EURUSDm", "XAUUSDm"],
)
```

**Full configuration reference:**

```python
config = MT5Config(
    # Required
    account  = 12345678,
    password = "your_password",
    server   = "Exness-MT5Trial9",
    symbols  = ["EURUSDm", "XAUUSDm"],

    # Polling intervals
    poll_interval_ms      = 100,   # tick data polling (default: 100ms)
    exec_poll_interval_ms = 250,   # order/position polling (default: 250ms)

    # Order tagging — change if running multiple bots simultaneously
    magic_number = 510,

    # Reconnection
    reconnect_initial_delay_s = 1.0,
    reconnect_max_delay_s     = 60.0,
    reconnect_max_attempts    = 20,

    # Connection timeout
    timeout_s = 10.0,

    # Linux/Docker remote backend (v0.7.0+)
    backend    = "remote",                    # "local" (Windows) or "remote" (Linux/Docker)
    server_url = "http://localhost:5000",     # mt5server Flask API
    ws_url     = "ws://localhost:9000",       # mt5server WebSocket tick hub
)
```

**Loading from `.env`** (recommended — never hardcode credentials):

```python
import os
from pathlib import Path
from dotenv import load_dotenv
from mt5connect.config import MT5Config

load_dotenv(Path(__file__).parent / ".env")

config = MT5Config(
    account  = int(os.environ["MT5_ACCOUNT"]),
    password = os.environ["MT5_PASSWORD"],
    server   = os.environ["MT5_SERVER"],
    symbols  = os.environ["MT5_SYMBOLS"].split(","),
)
```

### Symbol naming

Different brokers use different symbol names. Always use the **exact name shown in your MT5 Market Watch window**.

| Broker | EURUSD | Gold | Bitcoin |
|--------|--------|------|---------|
| Exness standard | `EURUSDm` | `XAUUSDm` | `BTCUSDm` |
| Exness zero/raw | `EURUSD` | `XAUUSD` | `BTCUSD` |
| IC Markets | `EURUSD` | `XAUUSD` | `BTCUSD` |
| Pepperstone | `EURUSD` | `XAUUSD` | `BTCUSD` |

The adapter handles suffix normalisation internally for instrument classification — you just provide the exact broker symbol name.

---

## Writing a strategy

Strategies are plain NautilusTrader `Strategy` subclasses. The adapter handles all the MT5-specific plumbing — your strategy code is identical for both backtesting and live trading.

```python
from decimal import Decimal
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.config import StrategyConfig


class SmaCrossConfig(StrategyConfig, frozen=True):
    instrument_id : str
    bar_type      : str
    fast_period   : int     = 10
    slow_period   : int     = 30
    trade_size    : Decimal = Decimal("0.01")


class SmaCrossStrategy(Strategy):

    def __init__(self, config: SmaCrossConfig) -> None:
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type      = BarType.from_str(config.bar_type)
        self.fast_period   = config.fast_period
        self.slow_period   = config.slow_period
        self.trade_size    = config.trade_size
        self._fast_prices: list[float] = []
        self._slow_prices: list[float] = []
        self._position_side = None

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar) -> None:
        close = float(bar.close)
        self._fast_prices.append(close)
        self._slow_prices.append(close)
        if len(self._fast_prices) > self.fast_period:
            self._fast_prices.pop(0)
        if len(self._slow_prices) > self.slow_period:
            self._slow_prices.pop(0)

        if len(self._fast_prices) < self.fast_period:
            return

        fast_sma = sum(self._fast_prices) / self.fast_period
        slow_sma = sum(self._slow_prices) / self.slow_period

        if fast_sma > slow_sma and self._position_side != OrderSide.BUY:
            self._close_position()
            self._open_position(OrderSide.BUY)
        elif fast_sma < slow_sma and self._position_side != OrderSide.SELL:
            self._close_position()
            self._open_position(OrderSide.SELL)

    def _open_position(self, side: OrderSide) -> None:
        quantity = Quantity(float(self.trade_size), self.instrument.size_precision)
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=side,
            quantity=quantity,
        )
        self.submit_order(order)
        self._position_side = side

    def _close_position(self) -> None:
        if self._position_side is None:
            return
        for pos in self.cache.positions_open(instrument_id=self.instrument_id):
            close_side = OrderSide.SELL if pos.side.name == "LONG" else OrderSide.BUY
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=close_side,
                quantity=pos.quantity,
            )
            self.submit_order(order)
        self._position_side = None

    def on_stop(self) -> None:
        self._close_position()
```

---

## Backtesting

Backtesting requires two steps: download historical bar data from MT5, then run the backtest engine against it.

### Step 1 — download historical data

```bash
python examples/download_historical_data.py
```

Or call the downloader directly:

```python
from mt5connect.config import MT5Config
from mt5connect.connection import MT5Connection
from mt5connect.providers import MT5InstrumentProvider
from mt5connect.downloader import MT5DataDownloader
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from datetime import datetime, timezone

config = MT5Config(
    account=12345678, password="your_password",
    server="Exness-MT5Trial9", symbols=["EURUSDm"],
)

conn     = MT5Connection(config)
conn.connect()
provider = MT5InstrumentProvider(conn)
catalog  = ParquetDataCatalog("./catalog")

instrument = provider.load_symbol("EURUSDm")
catalog.write_data([instrument])

downloader = MT5DataDownloader(conn, provider, catalog)
result = downloader.download_bars(
    symbol    = "EURUSDm",
    start     = datetime(2024, 1,  1, tzinfo=timezone.utc),
    end       = datetime(2024, 12, 31, tzinfo=timezone.utc),
    timeframe = 16385,  # H1
)
print(result)
conn.disconnect()
```

**MT5 timeframe constants:**

| Timeframe | Constant |
|-----------|----------|
| M1  | 1     |
| M5  | 5     |
| M15 | 15    |
| M30 | 30    |
| H1  | 16385 |
| H4  | 16388 |
| D1  | 16408 |
| W1  | 32769 |

---

## Live trading

See `examples/live_simple_strategy.py` for a complete runnable example. The key difference from backtesting is wiring the adapter into a `TradingNode` instead of a `BacktestEngine`.

```python
from mt5connect.config import MT5Config
from mt5connect.factories import build_mt5_node_config
from nautilus_trader.live.node import TradingNode

config = MT5Config(
    account=12345678,
    password="your_password",
    server="Exness-MT5Trial9",
    symbols=["EURUSDm", "XAUUSDm"],
)

node = TradingNode(config=build_mt5_node_config(config))
node.trader.add_strategy(YourStrategy(config=YourStrategyConfig(...)))
node.run()
```

---

## Linux / Docker remote backend

### What it is

v0.7.0 adds full Linux support via a Dockerized MT5 server. This lets you run NautilusTrader strategies on a Linux VPS or server without needing a Windows machine.

The `mt5server/` directory contains:
- A **Dockerfile** that installs MT5 inside Wine on Ubuntu
- A **Flask HTTP API** (port 5000) that exposes the full MetaTrader5 Python API over HTTP
- A **WebSocket hub** (port 9000) that streams live ticks from MT5 to your Python adapter in real time
- An **MQL5 Expert Advisor** (`ticks.mq5`) that runs inside MT5 and pushes ticks to the WebSocket hub
- **Automated setup scripts** — MT5 installs and configures itself from environment variables, no manual GUI interaction needed

### Security notice

> ⚠️ The server currently has **no authentication**. It must only be used locally (loopback) and must **never** be exposed to a public network. Exposing port 5000 or 9000 publicly gives anyone full control of your MT5 account.

Account credentials live only in your local `.env` file. They are passed to the container via environment variables and never baked into the Docker image.

### Requirements

- Docker and Docker Compose
- Linux (Ubuntu 20.04+ recommended) or macOS with Docker Desktop
- An MT5 broker account

### Quick start

**1. Copy the environment file and fill in your credentials:**

```bash
cd mt5server/
cp ../.env.example .env
# Edit .env with your MT5 account, password, server, and symbols
```

**2. Start the server:**

```bash
docker compose up -d
# MT5 installs automatically inside the container — allow 1-2 minutes
# Track progress: docker exec -it mt5server-mt5server-1 tail -f /var/log/mt5_setup.log
# View MT5 UI (optional): open https://localhost:3001 in a browser
```

**3. Run the remote backend example:**

```bash
source .env
source .venv/bin/activate
python examples/live_remote.py
```

If everything is working you should see ticks streaming within a few seconds:

```
[INFO] TRADER-001.TickPrintStrategy: XAUUSDp.MT5 bid=4368.34 ask=4368.46 @ 1787152807592000000
[INFO] TRADER-001.TickPrintStrategy: EURUSDp.MT5 bid=1.16074 ask=1.16075 @ 1787152807684000000
```

**4. Connect your strategy using remote backend config:**

```python
from mt5connect.config import MT5Config

config = MT5Config(
    account    = 12345678,
    password   = "your_password",
    server     = "Exness-MT5Trial9",
    symbols    = ["EURUSDm", "XAUUSDm"],
    backend    = "remote",
    server_url = "http://localhost:5000",
    ws_url     = "ws://localhost:9000",
)
```

Tick streaming is configured automatically for every symbol in `MT5_SYMBOLS`.

### Persistence

The containerized MT5 instance configures itself at startup and is not intended to be used via the regular MT5 GUI. It has no volumes for persisting configuration by default. To persist data between container restarts, mount a volume to `/config`:

```bash
docker run -v $PWD/config:/config ...
```

Or add the volume to `docker-compose.yml`.

---

## Running the full test suite

```bash
pytest tests/ -v
```

All tests mock the MT5 terminal — no live connection required.

```
640 passed in ~18s

tests/test_backend.py      — backend switching (local vs remote)
tests/test_config.py       — MT5Config validation
tests/test_connection.py   — MT5Connection lifecycle and reconnect logic
tests/test_data.py         — MT5DataClient tick polling and bar publishing
tests/test_data_ws.py      — WebSocket tick streaming (remote mode)
tests/test_downloader.py   — historical bar download
tests/test_execution.py    — order submission, fills, reconciliation
tests/test_factories.py    — factory wiring and node config
tests/test_parsing.py      — symbol info → NautilusTrader instrument conversion
tests/test_providers.py    — MT5InstrumentProvider loading
tests/test_remote_mt5.py   — HTTP shim for remote backend
tests/test_ws_stream.py    — WebSocket client auto-reconnect
```

---

## Project structure

```
mt5-connector/
├── mt5connect/
│   ├── backend.py       # backend switching — local (Windows IPC) vs remote (HTTP)
│   ├── config.py        # MT5Config — all user-facing configuration
│   ├── connection.py    # MT5Connection — terminal IPC lifecycle
│   ├── constants.py     # venue, magic number, symbol sets, normalize_symbol()
│   ├── data.py          # MT5DataClient — tick polling and bar publishing
│   ├── downloader.py    # MT5DataDownloader — historical bar download
│   ├── errors.py        # custom exceptions
│   ├── execution.py     # MT5LiveExecutionClient — order submission and fills
│   ├── factories.py     # LiveDataClientFactory + LiveExecClientFactory wiring
│   ├── parsing.py       # symbol_info → NautilusTrader Instrument conversion
│   ├── providers.py     # MT5InstrumentProvider
│   ├── remote_mt5.py    # HTTP shim mirroring MetaTrader5 Python API (remote mode)
│   └── ws_stream.py     # WebSocket tick client (remote mode)
├── mt5server/           # Dockerized MT5 server for Linux/VPS (v0.7.0+)
│   ├── Dockerfile       # Ubuntu + Wine + MT5 + Python + Flask
│   ├── docker-compose.yml
│   ├── app/             # Flask HTTP API + WebSocket hub
│   │   ├── app.py
│   │   ├── routes/      # /health, /login, /account, /mt5/*
│   │   └── ws_server.py # tick relay hub
│   ├── mt5ticks/        # MQL5 EA that streams ticks to the WebSocket hub
│   └── scripts/         # automated MT5 installation scripts
├── tests/               # full test suite (no live MT5 required)
├── examples/
│   ├── live_simple_strategy.py     # Windows local mode
│   ├── live_remote.py              # Linux/Docker remote mode
│   ├── backtest_eurusd.py          # SMA crossover backtest
│   └── download_historical_data.py # download bars from MT5
├── .env.example         # credential template — copy to .env and fill in
└── pyproject.toml
```

---

## Broker compatibility

The adapter works with any MT5 broker. The key difference between brokers is the symbol naming convention and the server name format.

| Broker | Server format | Symbol format |
|--------|--------------|---------------|
| Exness standard | `Exness-MT5Trial9` (demo) / `Exness-MT5Real8` (live) | `EURUSDm`, `XAUUSDm` |
| Exness zero/raw | `Exness-MT5Real8` | `EURUSD`, `XAUUSD` |
| IC Markets | `ICMarketsSC-Demo` | `EURUSD`, `XAUUSD` |
| Pepperstone | `Pepperstone-Demo` | `EURUSD`, `XAUUSD` |
| OANDA | `OANDA-OANDATrade-1` | `EUR_USD` |

Find your exact server name in MT5 → File → Open Account → search your broker name.

---

## Troubleshooting

**`mt5.initialize() failed — error -6: Terminal: Authorization failed`**

The MT5 terminal is not open or is not logged in. Open MetaTrader 5, log in to your account, wait for the green connection indicator in the bottom-right corner, then run the script again.

**`mt5.login() failed — error -6: Terminal: Authorization failed`**

Wrong account number, password, or server name. Double-check all three against your broker's welcome email or the MT5 terminal itself (account number is shown in the top-left).

**`order_send failed — retcode=10027 comment=AutoTrading disabled by client`**

AutoTrading is disabled in the MT5 terminal. Click the **AutoTrading** button in the toolbar — it should turn green.

**`Factory was not of type LiveExecClientFactory`**

You are using an old version of `factories.py`. Update to the latest version.

**Strategy not placing trades after 30+ minutes**

Check that the bar type string in your strategy config exactly matches the bar type you subscribed to in `on_start`. Also verify AutoTrading is enabled in the MT5 terminal.

**Docker container not starting / MT5 not initializing**

Check the setup log: `docker exec -it mt5server-mt5server-1 tail -f /var/log/mt5_setup.log`. The first startup takes 1-2 minutes for MT5 to install and log in.

---

## Safety notes

- Always use a **demo account** until you have verified your strategy behaves correctly.
- The `magic_number` in `MT5Config` (default: `510`) tags every order placed by the adapter. Orders without this magic number are ignored — safe to have the MT5 terminal open and trade manually alongside the bot.
- Change `magic_number` if you run multiple bots simultaneously to avoid one bot managing the other's positions.
- The adapter uses netting mode (one position per symbol) matching how MT5 accounts work by default. Hedging accounts are not currently supported.
- Past backtest performance does not guarantee live performance. Spreads, slippage, and execution latency differ between backtest and live environments.
- **Remote mode security:** Never expose the mt5server ports (5000, 9000) to a public network. Use SSH tunnels or a private network if accessing remotely.

---

## Changelog

### 0.7.1 (2026-09-13)

- Fix `pyproject.toml` metadata version incompatibility with Python 3.14
- Add `websockets>=12.0` as explicit dependency
- Add Linux OS classifier

### 0.7.0 (2026-09-13)

**Linux/Docker support via remote backend** — contributed by [@cornelius-keller](https://github.com/cornelius-keller)

- Add `mt5server/` — Dockerized MT5 server running on Ubuntu via Wine
- Add `mt5connect/remote_mt5.py` — HTTP shim mirroring the MetaTrader5 Python API, allowing Linux Python code to talk to a remote MT5 terminal
- Add `mt5connect/ws_stream.py` — WebSocket tick streaming client with auto-reconnect
- Add `mt5connect/backend.py` — clean switching between `local` (Windows IPC) and `remote` (HTTP) backends via `MT5Config(backend="remote", server_url=...)`
- Add `examples/live_remote.py` — complete remote backend example
- Fix `_disconnect()` — handle `asyncio.CancelledError` correctly when cancelling the poll task
- Fix `_poll_once()` — per-symbol exception isolation so one failing symbol does not stop polling others
- Fix `_subscribe_quote_ticks()` — track subscriptions before connection is established
- Fix `subscribed_quote_ticks` — expose as `@property` not a plain method
- Fix `get_instrument()` in `providers.py` — case-insensitive symbol lookup
- 640 tests passing

### 0.1.0 (2026-06-12)

- Initial release — Windows local mode with full NautilusTrader integration
- Live tick polling, order execution, position reconciliation
- Historical bar download to Parquet catalog for backtesting
- Fix: execution client no longer replays historical deals as fills on node restart

---

Pull requests are welcome. Run the test suite before submitting:

```bash
pytest tests/ -v
```

New features should include tests. The test suite mocks the MT5 terminal so no live account is needed to contribute.

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

*This project is not affiliated with, endorsed by, or supported by Nautech Systems Pty Ltd or the NautilusTrader project.*