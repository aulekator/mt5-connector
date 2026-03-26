# mt5connect

**Unofficial community MetaTrader 5 adapter for NautilusTrader** — live trading and backtesting on any MT5 broker (Exness, IC Markets, Pepperstone, and more).

> ⚠️ **Disclaimer:** This is an independent community project. It is **not** affiliated with, endorsed by, or supported by [Nautech Systems Pty Ltd](https://nautilustrader.io) or the official [NautilusTrader](https://nautilustrader.io) project.

[![PyPI version](https://badge.fury.io/py/mt5connect.svg)](https://badge.fury.io/py/mt5connect)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey.svg)](https://www.microsoft.com/windows)
[![Unofficial](https://img.shields.io/badge/NautilusTrader-unofficial%20community%20adapter-orange.svg)](https://nautilustrader.io)

---

## What this is

`mt5connect` is a **data and execution adapter** that connects [NautilusTrader](https://nautilustrader.io) to any MetaTrader 5 broker. Write your strategy once in Python, then run it as a backtest against historical MT5 data  or flip a switch and run it live.

```
MT5 Terminal (Windows) ←→ mt5connect ←→ NautilusTrader
                              ↑
                    tick polling, order routing,
                    account state, reconciliation
```

**What you get:**

- Live tick data polled from MT5, aggregated into any bar type NautilusTrader supports
- Full order lifecycle: market, limit, stop, stop-limit orders with SL/TP
- Account state and position reconciliation on startup and continuously
- Historical bar data download into a NautilusTrader Parquet catalog for backtesting
- Automatic reconnection with exponential backoff
- Works with any MT5 broker — Exness, IC Markets, Pepperstone, OANDA, and more

> **Platform note:** The MetaTrader5 Python library is Windows-only. This adapter runs on Windows. Backtesting with downloaded data works on any platform once the data has been collected.

---

## Table of contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Writing a strategy](#writing-a-strategy)
- [Backtesting](#backtesting)
- [Live trading](#live-trading)
- [Running the full test suite](#running-the-full-test-suite)
- [Project structure](#project-structure)
- [Broker compatibility](#broker-compatibility)
- [Troubleshooting](#troubleshooting)

---

## Requirements

- Windows 10 or 11 (required by MetaTrader 5)
- Python 3.10, 3.11, or 3.12
- MetaTrader 5 terminal installed and open, logged in to your broker account
- An MT5 broker account (demo accounts work perfectly for development)

---

## Installation

```bash
pip install mt5connect
```

Or install from source for development:

```bash
git clone https://github.com/aulekator/mt5-connect
cd mt5connect
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

The strategy above is identical whether you run it in a backtest or live — the only difference is which engine you wire it into.

---

## Backtesting

Backtesting requires two steps: download historical bar data from MT5, then run the backtest engine against it.

### Step 1 — download historical data

```bash
python examples/download_historical_data.py
```

This connects to MT5, downloads H1 bars for the configured symbol, and writes them into a NautilusTrader Parquet catalog at `./catalog`.

You can customise the download by editing the script, or call the downloader directly:

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

# Write the instrument definition first (required by the backtest engine)
instrument = provider.load_symbol("EURUSDm")
catalog.write_data([instrument])

# Download bars
downloader = MT5DataDownloader(conn, provider, catalog)
result = downloader.download_bars(
    symbol    = "EURUSDm",
    start     = datetime(2024, 1,  1, tzinfo=timezone.utc),
    end       = datetime(2024, 12, 31, tzinfo=timezone.utc),
    timeframe = 16385,  # MT5 timeframe constant: 16385 = H1
)
print(result)
conn.disconnect()
```

**MT5 timeframe constants:**

| Timeframe | Constant |
|-----------|----------|
| M1  | 1 |
| M5  | 5 |
| M15 | 15 |
| M30 | 30 |
| H1  | 16385 |
| H4  | 16388 |
| D1  | 16408 |
| W1  | 32769 |

### Step 2 — run the backtest

```bash
python examples/backtest_eurusd.py
```

Or wire it up yourself:

```python
from decimal import Decimal
from datetime import datetime, timezone
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue, TraderId
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

SYMBOL   = "EURUSDm"
VENUE    = "MT5"
CATALOG  = "./catalog"

catalog     = ParquetDataCatalog(CATALOG)
instruments = catalog.instruments()
instrument  = next(i for i in instruments if i.id.symbol.value == SYMBOL)

# Load bars from catalog
bars = catalog.bars([f"{SYMBOL}.{VENUE}"])

engine = BacktestEngine(
    config=BacktestEngineConfig(
        trader_id=TraderId("BACKTESTER-001"),
        logging=LoggingConfig(log_level="WARNING"),
    )
)

engine.add_venue(
    venue             = Venue(VENUE),
    oms_type          = OmsType.NETTING,
    account_type      = AccountType.MARGIN,
    base_currency     = USD,
    starting_balances = [Money(10_000.0, USD)],
    fill_model        = FillModel(
        prob_fill_on_limit=0.95,
        prob_slippage=0.10,
        random_seed=42,
    ),
)
engine.add_instrument(instrument)
engine.add_data(bars)

strategy = SmaCrossStrategy(
    config=SmaCrossConfig(
        instrument_id = f"{SYMBOL}.{VENUE}",
        bar_type      = f"{SYMBOL}.{VENUE}-1-HOUR-LAST-INTERNAL",
        fast_period   = 10,
        slow_period   = 30,
        trade_size    = Decimal("0.10"),
    )
)
engine.add_strategy(strategy)
engine.run(
    start = datetime(2024, 1,  1, tzinfo=timezone.utc),
    end   = datetime(2024, 12, 31, tzinfo=timezone.utc),
)

# Results
account = engine.trader.generate_account_report(Venue(VENUE))
fills   = engine.trader.generate_order_fills_report()
print(account)
print(f"Total fills: {len(fills)}")
engine.dispose()
```

---

## Live trading

Live trading uses NautilusTrader's `TradingNode` with the MT5 data and execution clients.

```python
import os, signal, sys
from decimal import Decimal
from pathlib import Path
from dotenv import load_dotenv
from nautilus_trader.live.node import TradingNode
from mt5connect.config import MT5Config
from mt5connect.factories import (
    build_mt5_node_config,
    MT5LiveDataClientFactory,
    MT5LiveExecClientFactory,
)

load_dotenv(Path(__file__).parent / ".env")

# 1. Configure MT5
mt5_config = MT5Config(
    account  = int(os.environ["MT5_ACCOUNT"]),
    password = os.environ["MT5_PASSWORD"],
    server   = os.environ["MT5_SERVER"],
    symbols  = os.environ["MT5_SYMBOLS"].split(","),
)

# 2. Configure strategy
symbol        = mt5_config.symbols[0]
instrument_id = f"{symbol}.MT5"
bar_type      = f"{instrument_id}-1-MINUTE-LAST-INTERNAL"

strategy_config = SmaCrossConfig(
    instrument_id = instrument_id,
    bar_type      = bar_type,
    fast_period   = 10,
    slow_period   = 30,
    trade_size    = Decimal("0.01"),
)

# 3. Build and run the node
node_config = build_mt5_node_config(mt5_config=mt5_config)
node        = TradingNode(config=node_config)

# 4. Register factories (must be before node.build())
node.add_data_client_factory("MT5", MT5LiveDataClientFactory)
node.add_exec_client_factory("MT5", MT5LiveExecClientFactory)

# 5. Add strategy
node.trader.add_strategy(SmaCrossStrategy(config=strategy_config))

# 6. Graceful shutdown on Ctrl+C
def _shutdown(sig, frame):
    node.stop()
    sys.exit(0)

signal.signal(signal.SIGINT,  _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

# 7. Start
node.build()  # connects to MT5, loads instruments
node.run()    # starts polling loops and strategy
```

The node lifecycle in order — **sequence matters:**

```
TradingNode(config)                    # 1. init kernel and engines
node.add_data_client_factory(...)      # 2. register MT5 data factory
node.add_exec_client_factory(...)      # 2. register MT5 exec factory
node.trader.add_strategy(instance)     # 3. register strategy instance
node.build()                           # 4. connect to MT5, load instruments
node.run()                             # 5. start tick polling and strategy
```

### Bar types for live trading

NautilusTrader aggregates ticks into bars internally. The bar type string format is:

```
{symbol}.{venue}-{step}-{aggregation}-{price_type}-{aggregation_source}
```

Common examples:

```python
"EURUSDm.MT5-1-MINUTE-LAST-INTERNAL"    # 1-minute bars
"EURUSDm.MT5-5-MINUTE-LAST-INTERNAL"    # 5-minute bars
"EURUSDm.MT5-1-HOUR-LAST-INTERNAL"      # 1-hour bars
"EURUSDm.MT5-100-TICK-LAST-INTERNAL"    # 100-tick bars
"EURUSDm.MT5-1000-VOLUME-LAST-INTERNAL" # volume bars
```

---

## Running the full test suite

```bash
pytest tests/ -v
```

All tests mock the MT5 terminal — no live connection required to run tests.

```
tests/test_connection.py   — MT5Connection lifecycle, reconnect logic
tests/test_data.py         — MT5DataClient tick polling and bar publishing
tests/test_execution.py    — order submission, fills, reconciliation
tests/test_factories.py    — factory wiring and node config
tests/test_parsing.py      — symbol info → NautilusTrader instrument conversion
tests/test_providers.py    — MT5InstrumentProvider loading
```

---

## Project structure

```
mt5connect/
├── mt5connect/
│   ├── config.py        # MT5Config — all user-facing configuration
│   ├── connection.py    # MT5Connection — terminal IPC lifecycle
│   ├── constants.py     # venue, magic number, symbol sets, normalize_symbol()
│   ├── data.py          # MT5DataClient — tick polling and bar publishing
│   ├── downloader.py    # MT5DataDownloader — historical bar download
│   ├── errors.py        # custom exceptions
│   ├── execution.py     # MT5LiveExecutionClient — order submission and fills
│   ├── factories.py     # LiveDataClientFactory + LiveExecClientFactory wiring
│   ├── parsing.py       # symbol_info → NautilusTrader Instrument conversion
│   └── providers.py     # MT5InstrumentProvider
├── tests/               # full test suite (no live MT5 required)
├── examples/
│   ├── live_simple_strategy.py       # full live trading example
│   ├── backtest_eurusd.py            # SMA crossover backtest
│   ├── download_historical_data.py   # download bars from MT5
│   └── test_place_order.py           # verify execution path end-to-end
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

The MT5 terminal is not open, or is not logged in. Open MetaTrader 5, log in to your account, wait for the green connection indicator in the bottom-right corner, then run the script again.

**`mt5.login() failed — error -6: Terminal: Authorization failed`**

Wrong account number, password, or server name. Double-check all three against your broker's welcome email or the MT5 terminal itself (the account number is shown in the top-left of the terminal).

**`order_send failed — retcode=10027 comment=AutoTrading disabled by client`**

AutoTrading is disabled in the MT5 terminal. Click the **AutoTrading** button in the toolbar — it should turn green. This must be enabled for any automated order to be sent.

**`Factory was not of type LiveExecClientFactory`**

You are using an old version of `factories.py` where `MT5LiveExecClientFactory` did not inherit from `LiveExecClientFactory`. Update to the latest version.

**Strategy not placing trades after 30+ minutes**

Check that the bar type string in your strategy config exactly matches the bar type you subscribed to in `on_start`. A mismatch means `on_bar` is never called. Also verify AutoTrading is enabled in the MT5 terminal.

Note: this package only installs successfully on Windows. It cannot be installed on macOS or Linux.

---

## Safety notes

- Always use a **demo account** until you have verified your strategy behaves correctly.
- The `magic_number` in `MT5Config` (default: `510`) tags every order placed by the adapter. Orders without this magic number are ignored — safe to have the MT5 terminal open and trade manually alongside the bot.
- Change `magic_number` if you run multiple bots simultaneously to avoid one bot managing the other's positions.
- The adapter uses netting mode (one position per symbol) matching how MT5 accounts work by default. Hedging accounts are not currently supported.
- Past backtest performance does not guarantee live performance. Spreads, slippage, and execution latency differ between backtest and live environments.

---

## Contributing

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
