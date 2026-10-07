# Getting started

Use Python 3.12–3.14 on Linux or Ubuntu/WSL2, with `findmnt` from util-linux.
SQLite state and locks require ext2/ext3/ext4, btrfs or xfs, not a Windows mount,
network share or tmpfs. Install `python -m pip install -e ".[dev]"` from the checkout.

## Offline demonstration

```bash
tradeagent demo --demo-dir data/rsi-demo
tradeagent inspect --state-dir data/rsi-demo
```

Use a fresh directory. Outputs are `experience.sqlite3`, `summary.json`, `events.json`,
`snapshot.example.json` and `future.example.json`. These are synthetic and ignored by
Git. The example snapshot is historical fixture data: inspect its shape; do not
submit it as a new prospective scan.

## Input data contract

`scan --input` accepts one JSON MarketSnapshot with:

- symbol, benchmark, UTC decision_time, source, evidence_kind (`prospective` normally);
- arrays `bars` and `benchmark_bars`: symbol, start, end, available_at,
  open, high, low, close and optional volume;
- bid, ask, quote_time, quote_available_at;
- optional session_open, previous_close and their `_time` timestamps;
- optional `options`: contract_id, underlying, kind (call/put), expiration,
  strike, asof, available_at, bid, ask; optional mark, IV/Greeks, volume/open interest.

Timestamps may be Unix seconds or ISO 8601 with timezone. Prices are finite JSON
numbers. Only completed decision-time bars are accepted; the latest symbol and
benchmark bars end exactly at the decision. Inputs need truthful source availability
timestamps, including session references. Missing session references abstain in
opening/gap families; missing history abstains when a feature cannot be computed.

`resolve --input` accepts `{ "source": "same-source", "bars": [...], "options": [...] }`.
Future bars use the same Bar schema. Options use the same quote schema at the exact
horizon. The command stores actual ingestion time and rejects observations not yet
available. Missing symbol/benchmark path segments leave predictions unresolved.

Initialize before the first decision, then provide a freshly collected snapshot:

```bash
tradeagent init --state-dir data/research
tradeagent scan --state-dir data/research --input decision-snapshot.json
# Later:
tradeagent resolve --state-dir data/research --input future-observations.json
tradeagent learn-daily --state-dir data/research
tradeagent evolve-weekly --state-dir data/research
tradeagent inspect --state-dir data/research
```

Each command runs once. Use an external scheduler and data collector for unattended
operation. Automated broker-market collection is not installed in this version.
The CLI does not accept a backdated `--asof` override. Synthetic experiments use
their separate evidence pool (`--evidence-kind synthetic` for learning/evolution).

## Agent challenger proposal

```json
{
  "strategy_id": "opening_momentum",
  "parent_version": "v1",
  "hypothesis": "A larger abstention threshold may reduce noisy opening predictions",
  "params": {"threshold": 0.00125, "scale": 0.5, "horizon": 3600, "max_expected": 0.03}
}
```

Run `tradeagent evolve-weekly --proposal hypothesis.json`. Bounds and incumbent
identity are checked. The new version starts in shadow and receives a frozen test
plan. There is no immediate parent replacement.

`tradeagent retire --strategy-id ID --version VERSION --reason TEXT` records a
reason and excludes that version from future scans while preserving all history.
Retiring the incumbent disables the strategy population until an explicit future
administrative workflow is implemented; historical state remains inspectable.
`tradeagent retire-lesson --lesson-id ID --reason TEXT` appends a retired lesson
revision. `inspect` includes generation/regime performance and learned/raw selection
comparisons, using the frozen selections actually recorded at decision time.

## External execution calibration

`tradeagent import-execution --input reconciled-fills.json` accepts an object with
`reconciled: true` and a `records` array. Each record needs prediction_id,
execution_key, observed_at, expected_entry, quantity and status (filled/rejected/failed/pending).
Filled records need actual_entry and fees; actual_exit is optional. All timestamps
here are finite Unix seconds. Quantity denotes long price units: include the option
multiplier for option records. The importer trusts the external reconciliation
attestation and neither contacts a broker nor creates an order. Late fill diagnostics
append separately and never alter original forecasts, attribution or alpha scores.

## Execution compatibility

`tradeagent execution simulate --demo-dir data/execution-demo` runs preserved
synthetic equity and option submission/reconciliation tests. Its generated
`inspect.example.json` can be passed to `tradeagent execution inspect --config ...`.
Old validate/tools/shadow/simulate aliases remain for execution compatibility.
`inspect` defaults to the research DB; `inspect --config` routes to execution inspection.

See [Execution boundary](deployment.md) before using any older operational library.
