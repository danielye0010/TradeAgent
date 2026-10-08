# TradeAgent

An open-source trading agent for US equities. It collects market data, runs a set of
strategies on a schedule, records every prediction and its outcome, and executes
trades through the official [Robinhood Trading MCP](https://robinhood.com/us/en/support/articles/agentic-trading-overview/).

[![CI](https://github.com/danielye0010/TradeAgent/actions/workflows/ci.yml/badge.svg)](https://github.com/danielye0010/TradeAgent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12%E2%80%933.14-blue)
![License](https://img.shields.io/badge/license-Apache--2.0-green)

> **Status: alpha.** Live trading is driven by explicit one-shot commands, and none
> of the bundled strategies has a proven edge yet. Start with paper mode and small
> amounts.

## Features

- **Shadow trading.** A background service makes predictions at the open each
  trading day, then scores them against what the market actually did.
- **Strategy research loop.** Every strategy version predicts on the same snapshots.
  Daily learning re-weights strategies from their track record, and challengers
  compete against the current champion before they can replace it.
- **Live execution.** One-shot entry and exit through Robinhood, with dollar-based
  or fractional orders, position and exposure limits, and a kill switch.
- **Crash recovery.** Orders are journaled before they are sent. After a restart,
  the agent reconciles with the broker instead of resubmitting.
- **Historical replay.** Run the same strategy code over past minute bars.
- **Pluggable market data.** Robinhood by default, Alpaca SIP as an option.

## Quickstart

TradeAgent needs Python 3.12 or newer on Linux (or WSL2 on Windows).

```bash
git clone https://github.com/danielye0010/TradeAgent.git
cd TradeAgent
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Try it without a brokerage account:

```bash
tradeagent demo --demo-dir data/demo                  # research loop on synthetic data
tradeagent inspect --state-dir data/demo              # strategy scores and selections
tradeagent run-once --paper --state-dir data/paper    # full entry/exit against a simulated broker
```

## How it works

```text
market data ──► snapshot ──► strategies ──► predictions ──► outcomes ──► learning
 (Robinhood                    (all versions                (1-hour        (weights,
  or Alpaca)                    in parallel)                 horizon)       challengers)
                                     │
                                     ▼
                       trade plan ──► risk checks ──► Robinhood order ──► reconcile
```

Strategies only see market data. They never see account state, and they cannot
place orders directly. A trade goes through the risk engine and the order lifecycle
on its way to the broker, so strategy code can change freely without affecting
how orders are handled.

## Live trading

**1. Connect Robinhood.** TradeAgent doesn't handle your login. It calls a small
OAuth helper script you provide, which prints an access token for the Robinhood MCP
endpoint. See [the helper contract](docs/prospective-shadow.md#robinhood-setup).

**2. Configure.** Copy the example config and edit the paths:

```toml
[live]
enabled = true
symbols = ["QQQ", "IWM"]
state_dir = "/abs/path/to/state"
max_notional = "25"

[broker]
oauth_helper = "/abs/path/to/robinhood-oauth-helper"

[entry]
order_type = "market"
dollar_amount = "5"

[exit]
order_type = "market"
hold_seconds = 30
```

**3. Check, then trade:**

```bash
tradeagent live-check --config tradeagent.toml   # read-only: account, quotes, permissions
tradeagent run-once --live --config tradeagent.toml
```

Each run makes one entry and one exit. To stop early, create a `KILL` file in
`state_dir`; the agent will close its position and exit. See
[One-shot operation](docs/one-shot.md) for all options and recovery steps.

## Shadow service

Install the daily prediction service as a systemd user unit:

```bash
python scripts/install_shadow.py --oauth-helper /path/to/helper
systemctl --user status tradeagent-prospective.service
cat work/prospective-robinhood/STATUS.md
```

To use Alpaca for market data instead, run `python -m tradeagent.prospective.access`
to store your keys, then add `--market-data-provider alpaca` to the install command.

## Commands

| Command | Purpose |
|---|---|
| `demo`, `inspect` | Run the research loop on synthetic data; inspect a state directory |
| `init`, `scan`, `resolve` | Register strategies, record predictions from a snapshot, score outcomes |
| `learn-daily`, `evolve-weekly` | Update strategy weights; create and evaluate challengers |
| `retire`, `retire-lesson` | Remove a strategy version or a learned lesson |
| `replay-history` | Replay recent sessions from historical minute bars |
| `live-check` | Read-only readiness check against your account |
| `run-once --paper / --live` | One entry and exit, simulated or real |
| `reconcile-once`, `new-run` | Inspect a finished run; start a new one |

## Documentation

- [Architecture](docs/architecture.md)
- [Getting started](docs/getting-started.md)
- [Research protocol](docs/research-protocol.md)
- [Market data providers](docs/market-data.md)
- [Shadow service](docs/prospective-shadow.md)
- [One-shot live trading](docs/one-shot.md)

## Roadmap

- Connect the research loop's trade plans to live execution
- Scheduled live trading, with alerts
- Agent-generated strategy proposals
- Larger universe and longer horizons

## Contributing

Pull requests are welcome. Before you submit, run:

```bash
pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
```

Tests use synthetic data and mocked brokers, so no credentials are needed. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0. Some vendored components are MIT-licensed; see [THIRD_PARTY.md](THIRD_PARTY.md).

TradeAgent is not affiliated with Robinhood Markets, Inc. Trading involves risk of
loss. Nothing in this repository is investment advice.
