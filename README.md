# TradeAgent

TradeAgent is a Python trading system with timestamped market snapshots,
account-free strategies, durable shadow predictions, deterministic risk controls,
and recoverable broker execution.

**Default:** Robinhood market data + Robinhood execution.

**Optional:** Alpaca market data + Robinhood execution.

The unattended runtime is **SHADOW**: it records QQQ/IWM predictions against SPY
and resolves one-hour outcomes. It cannot review, place, or cancel orders. Robinhood
execution and reconciliation remain a separate, explicitly authorized substrate;
there is no automatic research-to-live bridge or established profitability record.

## Run locally

Use Python 3.12–3.14 on Linux or Ubuntu/WSL2. Keep SQLite on persistent local Linux storage.

~~~bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
tradeagent demo --demo-dir data/fresh-demo
tradeagent inspect --state-dir data/fresh-demo
tradeagent execution simulate --demo-dir data/fresh-execution-demo
~~~

These demonstrations use synthetic state and require no credentials. Choose fresh
directories; demonstration results do not establish prospective performance.

## Robinhood deployment

Configure an external OAuth helper for the official
[Robinhood Trading MCP](https://robinhood.com/us/en/support/articles/agentic-trading-overview/).
Authentication stays outside this repository. The helper must be an owner-only
local executable returning a resource-bound token; see
[Prospective operation](docs/prospective-shadow.md) for its contract.

~~~bash
.venv/bin/python scripts/install_shadow.py --oauth-helper /absolute/external/helper
systemctl --user status tradeagent-prospective.service
cat work/prospective-robinhood/STATUS.md
~~~

The service defaults to Robinhood and requires no Alpaca credential. One systemd
user service owns the loop. The Windows lifetime task keeps WSL available.
Unavailable authentication or market data fails safely without switching providers.

A decision consumes only data available by **09:33 America/New_York**.
Completed one-minute bars, fresh bid/ask, and aligned benchmark history pass through
one provider-independent strategy path. A missed decision is skipped; historical
data never backfills predictions. Outcomes require the exact 60-minute path.

## Optional Alpaca market data

Alpaca retains its completed-minute SIP snapshot adapter and encrypted credential
setup. Select it explicitly:

~~~bash
.venv/bin/python -m tradeagent.prospective.access
.venv/bin/python scripts/install_shadow.py --market-data-provider alpaca
cat work/prospective-alpaca/STATUS.md
~~~

The installer reconfigures the same service and uses a separate cold state directory.
There is no silent fallback or mixing of provider evidence. Alpaca SIP entitlement
and live collection must be verified independently. The separate historical
commissioning command remains an optional Alpaca/input-file experiment.

## Historical alpha research

Evaluate the five frozen baselines as long-only portfolios with modeled costs,
chronological holdouts and passive/exposure benchmarks:

~~~bash
tradeagent research-history --start 2020-01-01 --validation-start 2023-01-01 \
  --test-start 2025-01-01 --end 2026-01-01 --output work/alpha-2020-2025
~~~

Uses Alpaca historical SIP minutes or explicit cached input, independently of the
Robinhood shadow service. See [running and interpreting historical research](docs/historical-alpha.md)
for data requirements and the limited real-market sample findings. Historical
results do not establish profitability or promote strategies.

## Research and execution

~~~text
Robinhood (default) / Alpaca (optional)
    -> MarketSnapshot -> Strategy.predict -> Prediction / TradePlan
    -> durable shadow recording -> exact-horizon outcomes

separate execution: Intent -> deterministic risk -> durable submission
    -> official Robinhood broker -> reconciliation -> execution journal
~~~

All enabled versions predict in shadow. Daily learning appends statistical state
without rewriting strategies. Challenger proposals and promotion require explicit
research commands; learner and selector failures cannot create broker orders.
Strategies, thresholds, risk bounds, and execution recovery rules remain frozen.

For explicit input-file research:

~~~bash
tradeagent init --state-dir data/research
tradeagent scan --state-dir data/research --input decision-snapshot.json
tradeagent resolve --state-dir data/research --input future-observations.json
tradeagent learn-daily --state-dir data/research
tradeagent evolve-weekly --state-dir data/research
~~~

Broker submissions persist identity before network I/O, reconcile observed fills,
fees, cash, and positions, and never blindly retry ambiguous submissions.
Options counterfactuals require recorded executable quotes. Automatic option
allocation and the research-to-live bridge remain deferred.

[Architecture](docs/architecture.md) · [Provider capabilities](docs/market-data.md) ·
[Research protocol](docs/research-protocol.md) · [Getting started](docs/getting-started.md) ·
[Execution boundary](docs/deployment.md)

## Development

~~~bash
python -m pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
python -m compileall -q src
python -m build
python -m twine check dist/*
python scripts/check_project.py
~~~

Tests use fresh synthetic state and mocked brokers. Internal handoffs, validation
transcripts, runtime databases, and authentication artifacts belong in ignored
local work directories.

## License

Original code: [Apache-2.0](LICENSE). Reused components retain their
[MIT notices](THIRD_PARTY.md). TradeAgent is independent of Robinhood Markets, Inc.
