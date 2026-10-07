# Historical commissioning

Run the frozen research loop on the latest 60 completed XNYS sessions before the
New York run date:

```bash
tradeagent replay-history
```

Equivalent explicit configuration:

```bash
tradeagent replay-history --sessions 60 --symbols QQQ,IWM --benchmark SPY \
  --horizon 60m --output work/commissioning
```

There is no canonical prospective schedule in this repository. Commissioning uses
exactly **09:32 America/New_York**, with a 60-minute horizon, cold learner state,
regular-session minute bars and no options. No decision-time search is available.
`--as-of YYYY-MM-DD` fixes the run date for reproducibility and excludes that date's
session even if it has already closed. Holidays, DST, prior closes and early closes
come from the existing exchange-calendars XNYS dependency.

## Data boundary

The default isolated adapter calls only Alpaca's historical market-data endpoint
`https://data.alpaca.markets/v2/stocks/bars`, using raw SIP 1-minute bars.
It first reads `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` from the environment.
When either is missing and uncached real data is needed, an interactive terminal
prompts only for missing values. Both inputs are hidden, including the key ID.
Credentials remain in process memory; they are never printed, written to files,
caches or SQLite, copied into the environment, or fingerprinted.
Non-interactive runs with missing credentials return an actionable REVIEW summary
without waiting for input. Failure to disable echo cancels input before fallback.
Cached or supplied data does not prompt. No authentication files or
broker/account/trading APIs are accessed.
No extra dependency is needed. Missing credentials produce a REVIEW summary with no database and an explicit reason;
implementation and fixture validation do not require credentials.

[Alpaca historical bars documentation](https://docs.alpaca.markets/us/reference/stockbars)
defines the feed, adjustment and pagination parameters.
The adapter freezes every raw response page, normalized symbol/start/end/availability
timestamp, retrieval period, adjustment mode and retrieval time in a checksum-bound
local cache. Repeated runs use that cache without downloading unchanged history.
No missing minute is interpolated.

A retrospective API response is not a point-in-time archive: final historical bars
may incorporate late reports/corrections. Availability is explicitly assumed at
bar end. Such a run receives REVIEW, even if implementation checks pass; it cannot
certify original historical data availability. Externally supplied data should
retain actual `available_at` times and an honest availability declaration.

The existing snapshot contract requires bid/ask. Replay uses the decision bar
close for both as an explicit proxy. Features, predictions, frozen alpha scores
and selection do not use this proxy; underlying counterfactuals are not executable
price evidence. Only the frozen residual score is reported, never PnL or Sharpe.

An offline input bundle can be supplied with `--input minute-data.json`:

```json
{
  "metadata": {
    "source": "your-source", "frequency": "1Min", "adjustment": "raw",
    "symbols": ["QQQ", "IWM", "SPY"],
    "retrieval_start": "2026-01-02T20:59:00Z",
    "retrieval_end": "2026-01-09T21:00:00Z",
    "retrieved_at": "2026-01-10T12:00:00Z",
    "availability": "bar_end_assumed_not_point_in_time_archive"
  },
  "bars": [{
    "symbol": "QQQ", "start": "2026-01-05T14:30:00Z",
    "end": "2026-01-05T14:31:00Z", "available_at": "2026-01-05T14:31:00Z",
    "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10000
  }]
}
```

That abbreviated example is a schema illustration, not enough data for a run.
Include exact prior-session closing minutes and regular-session opening-to-horizon
paths for each target/benchmark. Numeric timestamps are UTC epoch seconds;
string timestamps must carry a timezone. Raw metadata and bar timestamps remain
in the cached bundle.

## Chronology and isolation

An explicit monotonic replay clock is passed to the existing APIs. Production
`scan` still uses wall-clock time and retains all contemporaneity/backdating checks.
All targets are scanned before the future path is exposed. Resolution requires
the exact contiguous symbol and benchmark paths ending 60 minutes later.
Attribution and daily learning then run on resolved replay evidence only.
Weekly evolution runs once per UTC week, after daily learning, using the unchanged
proposal/evaluation rules. Challenger parameters follow the existing frozen
evolution rule; incumbent strategy definitions/parameters and learner settings
are unchanged. Replay plans cannot satisfy the prospective-only promotion condition.

Missing decision/previous-close data skips capture. Missing exact-horizon data
records a skip and leaves affected predictions unresolved; other available paths
can resolve normally. Source gaps produce REVIEW rather than invented data.

All evidence, weights, lessons and challengers live in the isolated output's
`experience.sqlite3` and carry `evidence_kind=replay`. Existing normal research
databases and execution journals are refused before SQLite is opened. Use a fresh
local Linux directory. The configuration, input and frozen strategy implementation
are bound by `commissioning.json`; rerunning the same completed experiment verifies
all canonical evidence and returns the same summary. A changed/incomplete run needs
a fresh output directory. The existing database is never reset or overwritten.
Do not run normal research commands against a commissioning directory.

## Outputs and interpretation

- `summary.json`: configuration, source/range, counts, skips, integrity checks,
  daily learned/raw/null/random scores, coverage, weights and status/reasons.
- `SUMMARY.md`: concise readable results.
- `events.json`: per-session chronological progress and unresolved source gaps.
- `commissioning.json`: frozen identity and evidence fingerprint.
- `cache/*.json`: deterministic raw/normalized source input.

Scores use the unchanged clipped direction × benchmark residual minus 10bp for
active forecasts. All comparisons are daily clustered; a resolved day without a
selection has score zero. Skipped decision days are excluded, partial resolution
is reported and forces REVIEW. Coverage includes all captured snapshot opportunities.

Unavailable credentials produce no observations; the requested range is distinct from the
actual session range (null). Invalid data/API responses still halt explicitly.

Gate diagnostics are declared in `research/replay.py:GATE`, before results. They
are commissioning checks, not fitted strategy thresholds. PASS needs at least 30
completed sessions, no source gaps/availability ambiguity, valid persistence and
chronology, and no material behavioral flags. There is no outperformance requirement.
FAIL covers integrity problems, unexplained unresolved evidence, promotion,
coverage under half of raw (at least 20 snapshot opportunities), six reversals
among weight steps of at least 0.5, or degradation against raw and both controls:
at least 20 daily clusters, average deficit >=20bp, and deficit >10bp on >=75% of days.
Noisy/limited data, no adaptation, persistent bounds, degradation against only
some comparators, or no active selection coverage produce REVIEW. Saturation by
itself does not prove pathology. These are operational diagnostics, not alpha claims.

For a deterministic small implementation check:

```bash
python scripts/commissioning_fixture.py --output work/commissioning-fixture/minutes.json
tradeagent replay-history --sessions 6 --as-of 2026-01-12 \
  --input work/commissioning-fixture/minutes.json --output work/commissioning-fixture/replay
```

The synthetic fixture intentionally receives REVIEW. Historical commissioning,
including a PASS, never supplies challenger promotion evidence.
