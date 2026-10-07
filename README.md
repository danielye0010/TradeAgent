# TradeAgent

TradeAgent is a Python trading system with timestamped market snapshots,
account-free strategies, durable shadow predictions, deterministic risk controls
and recoverable broker execution.

The installed runtime is **SHADOW**: it collects QQQ/IWM/SPY market data and records
prospective predictions and outcomes. It cannot review, place or cancel broker
orders. The separate execution substrate has durable intent identity, risk gates,
fill reconciliation and crash recovery. Connecting research plans to real orders
remains deferred; there is no enabled LIVE trading loop or established profitability record.

## Run locally

Use Python 3.12–3.14 on Linux or Ubuntu/WSL2. Keep SQLite on persistent local Linux storage.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
tradeagent demo --demo-dir data/fresh-demo
tradeagent inspect --state-dir data/fresh-demo
tradeagent execution simulate --demo-dir data/fresh-execution-demo
```

These commands use synthetic state and require no brokerage credentials. Choose
new directories for demos. They do not establish prospective market performance.

## Core runtime

```text
market data -> MarketSnapshot -> Strategy.predict -> Prediction / TradePlan
            -> durable shadow recording -> exact-horizon outcome recording

separate execution: Intent -> deterministic risk -> durable submission
                   -> broker -> reconciliation -> execution journal
```

Strategies live in `strategy.py`; risk lives in `risk.py` and `options.py`.
The research database and execution journal have separate ownership. Acknowledged
orders are reconciled against observed fills, fees, cash and positions. Ambiguous
submissions are never blindly retried.

One systemd user service owns prospective collection. The Windows scheduled task
only keeps WSL alive; it does not start trading. Process locks suppress overlapping
invocations and immutable identities suppress duplicate predictions and intents.
See [Architecture](docs/architecture.md) and [Prospective operation](docs/prospective-shadow.md).

## Prospective shadow operation

The SIP collector uses completed minute bars actually received by a fixed **09:33
America/New_York** decision. A bounded 30-second capture window tolerates normal
scheduler polling; it does not admit data received after the decision. Missing or
stale data and missed windows produce skips, never backfilled predictions.

```bash
# Operator-only hidden market-data credential setup, in an interactive WSL terminal:
.venv/bin/python -m tradeagent.prospective.access
.venv/bin/python scripts/install_shadow.py
systemctl --user status tradeagent-prospective.service
cat work/prospective-v2/STATUS.md
```

SIP entitlement, real receipt timing and end-to-end prospective outcomes remain
unverified until authenticated collection runs. No historical observations or
synthetic demo records enter the cold prospective database.

For explicit input-file operation:

```bash
tradeagent init --state-dir data/research
tradeagent scan --state-dir data/research --input decision-snapshot.json
tradeagent resolve --state-dir data/research --input future-observations.json
tradeagent learn-daily --state-dir data/research
tradeagent evolve-weekly --state-dir data/research
```

All enabled strategy versions predict in shadow; controls and challengers remain
unselected until an explicit promotion workflow. Daily learning appends statistical
state without rewriting strategy code or parameters. Optional learning/report failures
do not undo predictions or outcomes. Unavailable learned selection preserves predictions
and emits NO_TRADE plans. Challenger generation and promotion run through
explicit research commands, outside the unattended collector.

## Research and execution boundaries

The existing families, thresholds, learner formulas and promotion rules are retained.
Backtesting, attribution, reporting and challenger comparisons operate on separate
evidence pools. Historical and synthetic evidence cannot establish prospective alpha.
`tradeagent replay-history` runs isolated historical commissioning; it is not
required to operate shadow collection.

Long-premium option counterfactuals use recorded executable quote sides; missing
quotes remain unavailable. Automatic option allocation and the plan-to-live bridge
remain deferred. Preserved signed/canary/autonomous workflows live in `tradeagent.legacy`
and are not scheduled by the shadow service. Real broker operations require a separate
explicitly authorized workflow.

[Research protocol](docs/research-protocol.md) · [Getting started](docs/getting-started.md) ·
[Execution boundary](docs/deployment.md) · [Task handoff](GPT_HANDOFF.md)

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

Tests use fresh synthetic local state and mocked brokers. Safe smoke checks are
the offline prediction demo, preserved execution simulation, and credential-free
service status inspection. Never use a real broker order to test this cleanup.

## License

Original code: [Apache-2.0](LICENSE). Reused components retain their
[MIT notices](THIRD_PARTY.md). TradeAgent is independent of Robinhood Markets, Inc.
