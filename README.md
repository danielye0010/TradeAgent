# TradeAgent

TradeAgent is a full-stack trading agent that takes you from strategy to live execution.

Its v0.2 core is a self-improving short-horizon trading research and execution system.
It evaluates a population of quantitative strategies, records prospective predictions
and outcomes, learns which versions work in which regimes, and creates challengers
for later out-of-sample comparison. Selected forecasts can be expressed through
equities or long-premium options, with broker execution kept downstream.

The working v0.2 loop is:

**Prediction → Outcome → Attribution → Lesson → Hypothesis → Challenger →
Prospective evidence → Promotion / Rejection / Retirement**

There is no established profitability record. Live capital is for future execution
calibration; alpha learning works entirely from prospective shadow observations.

## Run locally

Use Python 3.12–3.14 on Linux or Ubuntu/WSL2. Keep SQLite on persistent local Linux
storage. From this checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
tradeagent demo --demo-dir data/rsi-demo
tradeagent inspect --state-dir data/rsi-demo
```

The deterministic, offline demo closes the loop with 150 predictions/outcomes,
15 learner updates, changed future selector state and five challengers with nine
paired shadow days. It places no real orders and requires no brokerage account.
Choose a new demo directory for each run.

## Operate the shadow lab

```bash
tradeagent init --state-dir data/research
tradeagent scan --state-dir data/research --input decision-snapshot.json
# Run later, when the requested horizon has elapsed and future data is available:
tradeagent resolve --state-dir data/research --input future-observations.json
tradeagent learn-daily --state-dir data/research
tradeagent evolve-weekly --state-dir data/research
tradeagent inspect --state-dir data/research
```

All enabled versions predict even when the selector rejects them. Strategies receive
market information only and can abstain. Five initial families cover opening
momentum/reversal, gap continuation, relative strength and mean reversion; null and
deterministic random controls remain shadow-only. Gap reversal is also available
through the family implementation.

Inputs use completed, timestamped bars and quotes. The input-file route is the
initial data boundary; production market collection/scheduling is external. The demo
exports the exact [input formats](docs/getting-started.md). Historical snapshots cannot
be submitted as contemporary prospective forecasts. Outcomes need complete symbol
and benchmark paths; missing data stays unresolved.

Daily learning changes version weights, coarse calibration, degradation and regime
compatibility using decayed/shrunk evidence. Weekly evolution creates new versions,
never overwrites parents, and fixes evaluation rules before prospective evidence.
Agent hypotheses can be supplied as constrained JSON; numerical truth and promotion
rules remain deterministic. There is no automatic LLM or daily source-code rewriting.

## Execution and options

The existing official Robinhood Trading MCP clients, quote/chain access, durable
intent, duplicate suppression, locks, fill reconciliation and crash recovery are
retained. The v0.1 execution substrate is frozen at tag `v0.1-execution-core`.

The default product commands have no broker placement path. Former signed deployment,
canary and autonomous control workflows are isolated in `tradeagent.legacy`.
`tradeagent execution simulate --demo-dir data/execution-demo` exercises the preserved
synthetic equity/option lifecycle. Existing execution DBs remain untouched by research.

Option counterfactuals buy at the recorded ask and sell at the future recorded bid.
Missing option quotes stay unavailable; marks do not become imaginary fills.
Automatic option EV/allocation and bridging research plans to real execution are
deferred. Sparse future live results should calibrate fills, spread, latency and
rejections, not train alpha from live losses. The old final real-money canary is canceled.

Future agent-adoption/attention/flow features have a small reserved input boundary
with zero live weight. No Agent Crowd trading strategy exists.

[Architecture](docs/architecture.md) · [Research protocol](docs/research-protocol.md) ·
[Getting started](docs/getting-started.md) · [Execution boundary](docs/deployment.md)

## Development

```bash
python -m pytest -q
ruff check src tests scripts
ruff format --check src tests scripts
python -m compileall -q src
python -m build
python -m twine check dist/*
python scripts/check_project.py
```

Tests use fresh synthetic state and mocked brokers. Scheduling remains external;
no daemon, scheduler or final production run times are installed.

## License

Original code: [Apache-2.0](LICENSE). Reused components retain their
[MIT notices](THIRD_PARTY.md). TradeAgent is independent of Robinhood Markets, Inc.
