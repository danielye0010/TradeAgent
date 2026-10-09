# Alpha research and economic TradePlans

Results from the frozen experiment are in [Alpha findings](alpha-findings.md).

This is an offline research path. It does not register strategies in the running
SHADOW service or submit orders. A positive backtest is not an execution approval.

## Run the frozen experiment

From a development checkout, using its own virtual environment:

```bash
python -m tradeagent.research.alpha_experiment --download work/public-alpha-data
python -m tradeagent.research.alpha_experiment \
  --input work/public-alpha-data/bars.json \
  --protocol docs/examples/tradeplan-protocol.json --output work/alpha-results
```

Each output directory must be new. Downloading explicitly enables public network
access; evaluating an input file is offline. Reproduce a previous experiment from
its cached input, since the rolling public download changes. The example protocol
has fixed 2026 boundaries; it is not a promise of future data availability.
The loader also accepts independently obtained one- or five-minute bars in the
existing Bar representation, with source, interval_seconds and adjustment metadata.
Use explicit synthetic provenance for fixtures. The runner always treats results
as historical research, never promotion evidence.

Outputs: protocol and data/source hashes, validation selection, all opportunity
records (including abstentions), trades.csv, selector evidence, economic plans and
results. Raw public data is cached locally and is not redistributed in the repository.

## Hypotheses and controls

Three account-free strategies implement the existing immutable Prediction interface:

- Opening continuation: an opening move exceeding max(10 bp, half its realized
  volatility), aligned with the gap and SPY. Tests continued directional pressure.
- Stabilized reversal: an abnormal opening move followed by an opposing completed
  bar and non-adverse latest SPY bar. Tests correction after early stabilization.
- Residual strength: opening return less a prior-session SPY beta exceeds the same
  volatility floor. Beta uses up to 20 prior sessions, at least 10, capped at 0–3;
  the initial value is one. This is a signal and diagnostic, not a traded hedge.

The unchanged BaselineStrategy opening momentum/reversal and unconditional
same-window entry are controls. Bearish predictions are retained but receive no
short-equity profit credit. Cash yields zero. Each symbol owns half the capital;
idle sleeves stay cash and no leverage or overlapping configurations are combined.

Decisions are at 10:00 and 11:00 New York. Enter five minutes later and exit after
30/60/120 minutes. Use later bar opens and complete contiguous paths, never the
already-known signal close. Prediction horizon includes delay plus holding period.
Bar-end availability is an assumption, not a historical receipt-time attestation.
The five-minute delay and adverse cost scenarios do not establish actual fill rates.

There are 18 candidate configurations, 12 simple directional controls, six
unconditional windows, and three selectors. These are correlated comparisons,
not independent trials. No parameters are retuned on the test period.
Corporate-action sessions are excluded across all symbols because the prior-close
gap reference is ambiguous; source gaps are unavailable, not zero-return days.

Per-side costs (half-spread, slippage, fee) in bp are low (0.5,0.5,0),
base (1,1,0.1), stress (2,3,0.2). Net = (1+gross)*(1-side)/(1+side)-1.
Approximate round trips are 2/4.2/10.4 bp. These are sensitivity assumptions,
not observed spreads or verified fee schedules. The matched-SPY comparator uses
exactly the active sleeves and timestamps, with the same costs. Beta residuals
exclude hedge implementation costs and are diagnostics only.

## Selection and planning

Validation picks the highest base-cost mean daily portfolio return among candidates
with at least five active days, or cash when none qualifies. That choice is frozen
before test reporting. All test configurations remain descriptive. The rule
selector uses continuation in a gap-aligned market trend, stabilized reversal
otherwise; it can abstain.

Two learned selectors compare pooled and regime-specific prior outcomes, freeze
evidence weekly, require ten active daily clusters, and rank a zero-shrunk economic
mean less one standard error after costs. This carries forward the existing
learner's daily-cluster/shrinkage principle without treating its clipped residual
diagnostic as spendable profit. No current-day outcome can enter selection. They
are comparators, not assumed improvements over the simple rule.

EconomicPlan wraps the existing TradePlan without migrating its database table
or changing the installed loop. It carries:

- Original forecast identity, opportunity features, horizon and uncalibrated point estimate.
- Prior empirical gross/net/stress returns, daily standard error, downside diagnostic,
  evidence digest, pool, sample size, and explicit rejection reasons.
- Entry price cap, entry window, planned exit time and full-equity downside bound.

The economic gate needs 20 prior active daily clusters, fresh evidence, and positive
mean minus 1.96 daily standard errors after stress costs. This is a conservative
screen, not a calibrated confidence guarantee; serial dependence and model search
are not corrected by that formula. Historical and synthetic plans remain research-only.
Signal validity and the planned entry window are independent of quote freshness.
A delayed plan requires a newly observed quote at execution, without renewing the
initial quote's timestamp. Rejected, stale, bearish and unselected plans abstain.

The pure `to_execution_intent` adapter supports externally sized whole-share limit,
fractional market and dollar-denominated market entries accepted by the existing
Intent contract. Fresh ask must respect the original cap. The interactive daily
workflow uses `validated_plan_entry` for owner account/risk sizing and the existing
one-shot lifecycle for submission, time exit and recovery. See
[Daily Trading Research](opportunity-workflow.md). Frozen experiment results remain
historical evidence, independently of this later execution integration.

## Equity and options evidence

Equity is the only expression with historical price evidence in this experiment;
even that evidence uses modeled costs. There are no synchronized historical
option entry/exit books, so calls, puts and debit spreads cannot be ranked economically.
7–21 DTE remains a candidate collection range, not a selected sweet spot.

option_economics contains research-only arithmetic for observed long calls/puts
and same-expiry directional debit spreads: entry at executable ask/bid by leg,
exit at bid/ask, contract identity and timestamp checks, premium at risk, and
explicit per-contract fees. It does not manufacture IV, theta, future option marks,
or a distribution from an underlying point forecast. Real bid/ask exit observations
would incorporate time decay and IV changes, but attributing them requires captured
Greeks/IV and synchronized underlying prices. Neither function routes orders.

The smallest useful option experiment is to capture one liquid 7–21 DTE near-ATM
call/put and one defined-risk vertical at each frozen equity signal, then capture
the same contracts at its exact exit horizon, including quote sizes, timestamps,
IV/Greeks and fees. Reject missing or stale endpoints. Compare dollar downside,
net PnL and capital usage before enabling an expression.

## Methods used selectively

[Gao et al. (2018)](https://profiles.wustl.edu/en/publications/market-intraday-momentum/)
motivates conditioning intraday hypotheses on timing and volatility. Its documented
first-half-hour to last-half-hour finding is not evidence for this morning
30–120-minute strategy. That distinction prevents importing a published result
into an untested horizon.

[LEAN slippage modeling](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/slippage/key-concepts)
motivates separating signal, fill timing and cost assumptions. The implementation
uses those principles in the existing lightweight models; no external engine or
multi-agent orchestration framework is added.
