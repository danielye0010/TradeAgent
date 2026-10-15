# Continuous Quant SHADOW v5

This account-free experiment asks whether rolling relative momentum survives decision
latency after observed spread and modeled slippage. It makes no profitability claim.
`continuous-quant-v5` and the hash of its frozen `CONFIG` identify the experiment.

Full scans run on five-minute slots from 09:45 through 15:00 America/New_York in XNYS
regular sessions. Early closes shorten discovery; signals without a full 60-minute
exit window plus endpoint tolerance are recorded as horizon-ineligible. Holidays
produce no scans. Deployment starts at the next slot, without morning backfill.

The collector reuses official Robinhood history batches of five and quote batches
of twenty, with a 40-second budget. It requests at most the latest 30 minutes.
Only the two pinned market-data read tools are accessible. Endpoint-only captures
request quotes, never account data, orders, options, Codex or an OpenAI API.
Active v1/v2 services take priority: v5 records deferred/missing evidence rather
than adding collection traffic. Their service files and installed environment need
not change. One systemd oneshot and the existing research state lock prevent overlap.

## Frozen rolling hypothesis

This is `rolling-relative-momentum-v1`, independent of the opening Alpha families.
Fifteen consecutive completed minute bars and synchronized SPY bars are required.
The stock return must exceed max(30bp, 1.5 times realized minute volatility), and
the same-sign stock-minus-SPY return must exceed 20bp. Existing observed liquidity
filters require spread <=0.5% and trailing completed mean dollar volume >=$1m/min.
Volume ratios and volatility ratios remain recorded scanner features, without
retrospective threshold tuning. Quotes must be active, positive, uncrossed, and have
both sides no more than 15 seconds old when received.

The forecast is clip(0.5 times relative return, -3%, +3%), explicitly UNCALIBRATED.
An abnormal move is a research trigger, not evidence of positive expected P&L.
Each symbol's direction transitions NEW / CONTINUING / EXPIRED; invalid evidence
is UNKNOWN and cannot silently rearm it. A valid expired signal may trigger again.
Restarted or failed scans never replace the first frozen slot with a later result.

## Forward decay design

Detection is the actual time the scan completes and the opportunity freezes.
Entry targets are exactly detection+1/3/5/10 minutes, and all exits target
detection+60 minutes. A 15-second scheduler tick collects only when a forward
endpoint is due. Both quote-side venue timestamps and receipt must lie in the
first 60 seconds beginning at the endpoint; actual times are retained. These are
bounded observation windows, not exact-time fills. Late starts or missing/stale
quotes are terminal MISSING, never historical reconstructions or interpolation.
Actual holding periods vary and are reported beside nominal 59/57/55/50 minutes.

Long entry uses ask and exit bid. Gross midpoint return, actual observed spread
cost and net modeled return remain separate. Frozen modeled slippage is 5bp/side;
fees are assumed zero and explicitly unobserved. Bearish signals use bid-to-ask
theoretical short-underlying diagnostics, excluding borrow/margin; they are neither
Put P&L nor an executable short strategy. No broker instrument is submitted.

Every frozen eligible opportunity is observed regardless of later losses or decay.
A signal is stale for entry if signed waiting midpoint drift consumes its frozen
expected magnitude, entry spread exceeds 0.5%, or the latest valid scan available
at the entry receipt no longer agrees with the original direction. Stale signals stay in the full
decay cohort. A separate trade-or-CASH return uses zero for a stale entry. Missing
prices remain unavailable and cannot become CASH. No OHLCV price substitutes for
quote returns; this phase leaves quote-unavailable outcomes missing.

The benchmark basket freezes all symbols satisfying the same completed-history
and liquidity conditions at detection, including candidates without a trigger.
It is equal-weight long exposure over that opportunity's entry/exit window, with
the same quote sides and costs. SPY and zero-return CASH are secondary controls.
The primary delay comparison requires complete selection, full frozen basket and
SPY quotes for all four delays. Unmatched records remain in completeness counts.
Within-session opportunity returns/differences are averaged before cross-session
means and standard errors. Fewer than two sessions has no standard error. Matched
coverage may be selective; it does not prove results apply to missing observations.

Morning is before 12:00 ET, midday 12:00–13:59, afternoon from 14:00. Reports keep
frequency and cost-adjusted results distinguishable. Opportunities are independent
counterfactuals with overlapping windows; never sum them into a fictional account.

## Operation and evidence

Use a dedicated local Linux state and a separately installed wheel environment:

```bash
python scripts/install_continuous.py --python /abs/v5-venv/bin/python \
  --state-dir /abs/data/continuous-quant-v5
# Verify scheduler and one representative real capture before activation.
python scripts/install_continuous.py --python /abs/v5-venv/bin/python \
  --state-dir /abs/data/continuous-quant-v5 \
  --measurement /abs/measurement.json --enable
```

Enablement requires a complete measured 31-symbol forward capture under 40 seconds,
with matching protocol/version, bar coverage and quote coverage. The measured five-
minute budget is a starting configuration, not a future latency guarantee. Actual
intervals, missing slots, failures and capture volume/latency remain in reports.
Systemd ticks perform no data access when no scan or endpoint is due; they still
refresh the readable report and completeness state. Persistent=false avoids boot
catch-up requests. The exchange calendar and frozen targets remain authoritative.

The independent `experience.sqlite3` contains append-only `continuous_events` with
configuration, activation, started/completed scans, failures, opportunities, endpoint
observations and capture identities. Content-addressed `captures/*.json` retain
normalized provider facts and availability times. Existing experiment state is refused.
`daily-status.json` provides reproducible cumulative rows, `daily-report.md` the current
session, `reports/YYYY-MM-DD.{md,json}` dated session reports, and
`cumulative-report.md` the across-session comparison. Regenerate from saved evidence:

```bash
python -m tradeagent.prospective.continuous --state-dir /abs/data/continuous-quant-v5 --report-only
```

The immutable opportunity event is the future event-triggered assessor boundary.
No assessor or new execution authorization is enabled by this phase.
