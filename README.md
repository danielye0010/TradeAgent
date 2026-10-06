# Robinhood Agent

Robinhood Agent is a Python trading bot built on Robinhood's official Trading MCP. It supports local simulation, real-data shadow runs, account and order risk checks, and controlled equity execution.

Try a trading cycle without a brokerage account:

```bash
git clone https://github.com/danielye0010/robinhood-agent-public.git
cd robinhood-agent-public
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

robinhood-agent simulate --demo-dir data/demo
robinhood-agent inspect --config data/demo/inspect.example.json
```

The demo fills synthetic equity and option orders, suppresses a duplicate decision, and records the results locally. No brokerage account is required.

## Installation

Use Python 3.11–3.14 on Linux or Ubuntu on WSL2. Keep the checkout and runtime state on a local Linux filesystem.

Install from a source checkout with the commands above, then check your configuration:

```bash
robinhood-agent validate
```

See [Getting Started](docs/getting-started.md) for requirements and setup help.

## Features

- Official Robinhood Trading MCP integration
- Synthetic local trading demo
- Real-data shadow mode
- Deterministic account and order risk checks
- SQLite execution journal and recovery
- Duplicate submission protection
- Signed controls for real equity execution

The included EMA strategy is a configurable example. Trading results depend on your strategy, market conditions, and costs.

## Using Robinhood

Connect Robinhood through Codex CLI's browser login, then run a shadow cycle:

```bash
robinhood-agent tools
robinhood-agent shadow
robinhood-agent inspect
```

Follow [Connect Robinhood](docs/getting-started.md#connect-robinhood) to set up the official MCP connection first. Shadow mode reads account and market data but never sends orders.

## Operating modes

| Mode | Use |
| --- | --- |
| Simulation | Run synthetic orders and inspect local results. |
| Shadow | Evaluate trades using real account and market data without submitting orders. |
| Supervised / canary | Execute equities with signed approval or a bounded signed policy. |
| Limited autonomous equity | Run within a signed policy's account, instrument, and trading limits. |

Real execution requires a signed policy or approval and an enrolled public verification key. The default configuration uses shadow mode; production keys are not enrolled. Live options are not supported.

See [Deployment](docs/deployment.md) for setup and recovery.

## Documentation

- [Getting Started](docs/getting-started.md) — install, run the demo, and connect Robinhood
- [Architecture](docs/architecture.md) — clients, strategy, risk, execution, and state
- [Safety](docs/safety.md) — risk checks and order recovery
- [Deployment](docs/deployment.md) — modes, real execution requirements, and operations

## Development

Install the development dependencies with `python -m pip install -e ".[dev]"`, then run:

```bash
python -m pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
python -m build
```

Tests and CI use synthetic brokers. See [Contributing](CONTRIBUTING.md) for the full checks and [Security](SECURITY.md) for sensitive reports.

## License

Original code is licensed under [Apache-2.0](LICENSE). Reused components retain their [MIT licenses](THIRD_PARTY.md).

Trading involves risk and can lose money. Robinhood Agent is an independent project and is not affiliated with or endorsed by Robinhood Markets, Inc.
