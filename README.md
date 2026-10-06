# TradeBot

A Python trading system for simulation, shadow trading, and controlled live execution.

[Getting Started](docs/getting-started.md) ·
[Architecture](docs/architecture.md) ·
[Deployment](docs/deployment.md)

TradeBot separates strategy, risk management, execution, and broker reconciliation.
It includes a local simulator, live-data shadow mode, persistent execution state,
and controlled live equity trading.

## Quick Start

```bash
git clone https://github.com/danielye0010/tradebot.git
cd tradebot

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

tradebot simulate --demo-dir data/demo
tradebot inspect --config data/demo/inspect.example.json
```

This runs a complete synthetic trading cycle locally. No brokerage account is required.

Use Python 3.12–3.14 on Linux or Ubuntu on WSL2. Keep the checkout and runtime
state on a local Linux filesystem. See [Getting Started](docs/getting-started.md)
for installation and configuration.

## Features

- Local equity and options simulation
- Live-data shadow trading
- Deterministic portfolio and order risk controls
- Persistent SQLite execution state
- Duplicate-order protection and recovery
- Broker reconciliation after interrupted submissions
- Signed controls for live equity execution

## Architecture

```mermaid
flowchart LR
    Data[Market + Account] --> Strategy
    Strategy --> Risk
    Risk --> Execution
    Execution --> Broker
    Broker --> State
    State --> Risk
```

Strategies generate trade proposals. Risk checks validate account, market, and
portfolio limits before execution. Orders and broker state are persisted for
reconciliation and recovery. See [Architecture](docs/architecture.md) for the modules.

## Broker Integration

The current broker adapter uses Robinhood's official Trading MCP.

See [Getting Started](docs/getting-started.md#connect-robinhood) for connection
and shadow-mode setup.

## Operating Modes

Simulation runs locally with synthetic brokers. Shadow mode reads live data and
records decisions without sending orders. Real equity execution requires a signed
approval or policy and an enrolled public verification key. The default is shadow
mode; production keys are not enrolled. Live options are not supported.

See [Deployment](docs/deployment.md) for supervised, canary, and limited autonomous
equity operation. The included EMA strategy is an example with no established live
performance record.

## Documentation

- [Getting Started](docs/getting-started.md) — installation, demo, and broker setup
- [Architecture](docs/architecture.md) — strategy, risk, execution, and integrations
- [Safety](docs/safety.md) — risk controls and order recovery
- [Deployment](docs/deployment.md) — operating modes, state, and recovery

## Development

Install with `python -m pip install -e ".[dev]"`, then run:

```bash
python -m pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
python -m build
```

Tests and CI use synthetic brokers. See [Contributing](CONTRIBUTING.md) for the
full checks and [Security](SECURITY.md) for sensitive reports.

## License

Original code is licensed under [Apache-2.0](LICENSE). Reused components retain
their [MIT licenses](THIRD_PARTY.md). Trading involves risk and can lose money.

TradeBot is an independent project and is not affiliated with or endorsed by Robinhood Markets, Inc.
