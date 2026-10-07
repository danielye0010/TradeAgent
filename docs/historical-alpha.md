# Historical alpha research

`research-history` evaluates the five existing baseline families as unleveraged
long-only portfolios. It calls the existing `BaselineStrategy.predict`, immutable
`MarketSnapshot`/`Bar` validation, completed-bar features, replay snapshot builder,
and `expressions.plan`. It writes files only: no experience database, learner,
selector update, challenger creation, promotion, service operation or broker calls.
The existing replay command remains a separate diagnostic commissioning experiment.

## Run

On Linux/WSL, from a development checkout with the package installed:

```bash
tradeagent research-history \
  --start 2020-01-01 --validation-start 2023-01-01 \
  --test-start 2025-01-01 --end 2026-01-01 \
  --output work/alpha-2020-2025
```

The default uses the existing Alpaca SIP historical adapter with raw minute bars.
Set `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` in the research process environment,
or use the adapter's interactive hidden prompt. Do not use operator runtime
credentials or reconfigure the prospective service. Feed entitlement must be
verified independently. Downloads are cached in monthly chunks; no session-count
cap applies. Memory is bounded by a monthly chunk for network retrieval.

For a normalized input bundle in the existing history format:

```bash
tradeagent research-history --input /path/to/minutes.json \
  --start 2020-01-01 --validation-start 2023-01-01 \
  --test-start 2025-01-01 --end 2026-01-01 \
  --output work/alpha-cached
```

A bundle contains `metadata` (named source, `frequency: "1Min"`, `adjustment:
"raw"` or `"split"`, availability convention and preferably symbols/provenance)
and `bars` with the existing Bar fields: symbol, UTC epoch start/end/available_at,
open/high/low/close/volume. Explicitly label fixtures with a source starting
`fixture` or `evidence_kind: "synthetic"`; their results are implementation checks.
Split-and-dividend-adjusted inputs are rejected. Imported files are indexed once
in memory; use manageable bundles. No observation is interpolated.

The explicitly selected `--source firstrate-sample` downloads public two-week
QQQ/IWM/SPY sample archives. It does not provide multi-year history. Archives and
normalized `cache/minutes.json` remain local, with source URLs and hashes recorded.
Samples change over time; reproduce a previous run using its frozen normalized
input rather than downloading a newer sample. The data is not redistributed here.

Every run requires a fresh output directory. Keep the default
`work/historical-alpha/experiments.jsonl` across experiments, or consistently pass
`--registry /persistent/path/experiments.jsonl`. Each invocation records all five
family hypotheses, parameters, costs and period boundaries before evaluation,
including attempts that cannot obtain data. The ledger cannot count outside
searches or prevent manual deletion; changing a registry does not reset scientific
evidence. A consumed test period cannot become untouched again in another run.

## Frozen experiment

- Five families: opening momentum, opening reversal, gap continuation, relative
  strength and mean reversion, with the existing default parameters and features.
- Decision at 09:33 America/New_York using only the three completed opening bars,
  aligned SPY history and the previous session's closing minute. A missing or late
  signal bar skips the session. These features deliberately match the current
  opening decision; this experiment does not test other schedules or lookbacks.
- Enter at the 09:34 bar open, allowing one full minute after decision; exit at the
  10:34 open. Never enter at the already-known signal close. These are OHLCV price
  proxies, not executable quotes. Bar availability at completion is assumed, not
  certified from historical receipt records. Revisions remain a limitation.
- Equal fixed capital sleeves for target symbols (default QQQ/IWM). A bullish plan
  buys the underlying; bearish and abstaining forecasts remain cash. No credit for
  short predictions, no options, leverage or redeployment of idle sleeves. Returns
  compound across daily portfolio observations.
- Modeled per-side basis points `(half-spread, slippage, fee)`: low `(0.5,0.5,0)`,
  base `(1,1,0.1)`, stress `(2,3,0.2)`. Approximate round trips are 2, 4.2 and 10.4 bp.
  Net return is `exit*(1-side_cost)/(entry*(1+side_cost))-1` within each sleeve.
  Fees are conservative scenarios, not a claim about a broker's historical rate.
- Chronological train/validation/test with explicit exclusive boundaries. No
  strategy parameter fitting. Training calibrates only benchmark exposure and
  volatility scales; validation picks maximum base-cost cumulative return, with
  cash if all are nonpositive. `selection.json` freezes that choice before test
  evaluation. All five fixed test comparisons are descriptive, not five separate
  confirmations or a reason to change the selected winner.
- Cash at zero yield; passive SPY and an equal-initial-weight QQQ/IWM buy-and-hold
  price basket; unconditional same-hour target/market trades; fixed training
  capital exposure applied to same-hour target trades; time/ex-ante-volatility
  scaled passive comparisons. Scaling is frozen before validation/test and capped
  at unleveraged volatility exposure. Scaled passive comparisons are diagnostic
  constant-weight cash allocations and omit incremental cash-rebalancing costs.
  Passive incurs one modeled entry/exit pair per period; same-hour trades pay daily.
- Complete contiguous entry/holding/exit paths and passive reference bars are
  required for every symbol. Missing days are unavailable and excluded from all
  comparisons, not counted as cash performance. Coverage and reasons are retained;
  gaps can bias the complete-case sample and disrupt calendar-time interpretation.

`REPORT.md` gives the comparison and limitations. `results.json` contains costs,
coverage, yearly stability, gross/net returns, daily Sharpe/volatility, drawdown,
trade counts, directional abstention counts, capital/time exposure and benchmark
comparisons. `daily.json` and `trades.csv` trace calculations to predictions and
bar prices; `protocol.json` identifies code/parameters/data scope. Raw downloads
and large outputs stay in ignored `work/` directories. Data-unavailable runs write
a report and return a nonzero status.

Drawdown uses end-of-day portfolio marks; it excludes intraday portfolio peaks and
troughs. Worst per-trade adverse excursion is separate. Annualized returns are
suppressed below 252 observations; daily Sharpe is descriptive even when short.
Fractional sizing, cash interest, taxes, impact and dividends are omitted. Split-only
prices preserve within-period fractional returns but do not reproduce raw share
quantities. No confidence interval or statistical significance is claimed.

Historical findings can identify candidate family/parameter/code identities for
independent validation and later prospective shadow collection. They cannot enter
existing promotion evidence. `further_investigation` is a descriptive screen
requiring 126 test sessions, positive stress net return and positive base excess
against the training-exposure same-hour benchmark, never automatic promotion or
proof of alpha. A short positive result remains inconclusive.

## Observed public-sample experiment, October 7, 2026

Source: [FirstRate Data public ETF samples](https://firstratedata.com/free-tick-data),
[format and timezone documentation](https://firstratedata.com/about/faq).
The downloaded archive readmes specify split-only adjustments and minute-start
US Eastern timestamps. Actual normalized coverage: September 21, 2026 04:00 ET
through October 6, 2026 20:00 ET; 31,106 one-minute bars across QQQ/IWM/SPY, including
extended hours. Only regular-session windows are used. Zero-volume minutes are
omitted by the source. Retrieval does not prove historical receipt availability.

```bash
tradeagent research-history --source firstrate-sample \
  --start 2026-09-22 --validation-start 2026-09-28 \
  --test-start 2026-10-01 --end 2026-10-07 \
  --output work/alpha-public-sample
```

There were 4 training, 3 validation and 4 test sessions, all complete. Validation
selected opening reversal before test. The table reports total-capital test returns:

| Family | Trades | Gross | Low net | Base net | Stress net | Base daily drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Opening momentum | 2 | -0.261% | -0.281% | -0.303% | -0.364% | -0.364% |
| Opening reversal | 2 | +0.315% | +0.295% | +0.273% | +0.211% | 0.000% |
| Gap continuation | 6 | -0.275% | -0.335% | -0.401% | -0.586% | -0.450% |
| Relative strength | 0 | 0.000% | 0.000% | 0.000% | 0.000% | 0.000% |
| Mean reversion | 1 | +0.073% | +0.063% | +0.052% | +0.021% | 0.000% |

Opening reversal lost 0.998% net in training, gained 0.112% in validation and gained
0.273% in test. Its test exposure averaged 3.846% of regular-session capital-time;
its training-exposure same-hour comparator returned approximately -0.352% in test.
Passive QQQ/IWM returned 1.894% and passive SPY 2.145% in test, at much greater
market exposure. The unconditional target one-hour comparator lost 0.703% net.
These are different risk/exposure profiles; passive outperformance alone does not
settle whether a timing signal has edge.

**No credible positive net trading edge is established.** Two profitable test trades
are insufficient, and the training reversal result is negative. The positive
reversal observation is a reason to test the unchanged family again on independent
multi-year data, not to promote it or claim profitability. The evidence does not
establish that any family is inherently unprofitable either.

A separate 2022–2025 Alpaca request could not run because this research process had
no Alpaca API key/secret. The local registry recorded two invocations (ten family
hypotheses), including that unavailable-data attempt; no parameter search occurred.

Next experiment: obtain at least several years of QQQ/IWM/SPY SIP minutes with
explicit adjustment/revision metadata; retain all five families and this schedule,
freeze a fresh final year, and compare reversal stability across market periods and
costs. Obtain prospective spreads to check whether cost scenarios are conservative.
The four-session test above is consumed and must not be reused as independent proof.

A small machine-readable selection of the actual run is retained in
[historical-alpha-sample.json](examples/historical-alpha-sample.json); raw bars and
full local results are not committed. Alpaca's existing raw/SIP adapter contract is
documented by its [historical bars API](https://docs.alpaca.markets/us/reference/stockbarsingle-1).