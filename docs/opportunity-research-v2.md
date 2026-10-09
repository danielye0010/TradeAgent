# Opportunity research v2: fixed prospective experiment

This is a design for a shadow research experiment, not an implemented replacement
LIVE model or a profitability claim. The three frozen Alpha families, their supported
horizons, `opportunity-cohorts-v1`, original research artifacts and SHADOW service
remain v1. Do not tune or reclassify their recorded decisions retroactively.

## One compact comparison

Use the existing 31-symbol snapshots and frozen Alpha signals. Fix the primary
configuration to the existing 300-second delay / 3600-second holding period (3900-second
Alpha horizon). No horizon/threshold/grid search is allowed in this experiment.
Only signals whose full regular-session window can occur are sampled. Preserve all
signals, abstentions and losses, timestamped source/quote costs and version identity.

Compare one predefined hierarchical economics estimator with unchanged v1 selectors.
Keep exact Alpha family/version, source, prospective pool, horizon, delay and benchmark
identity. Pool only within the fixed v1 asset groups (stocks by sector, broad equity
ETFs, sector ETFs); bond, commodity and unclassified instruments stay separate.
SPY's QQQ benchmark remains separate from SPY-benchmarked instruments.
These transport assumptions must be checked in the prior training block; peer evidence
cannot be represented as independent target-symbol days.

## Less fragmented, economically interpretable evidence

Use the frozen benchmark opening return known at the prediction cutoff to define
three market states: risk-on (>+10 bp), risk-off (<-10 bp), and quiet (otherwise).
Missing opening evidence means unavailable, never a retrospectively assigned regime.
The sign measures directional market exposure; it does not assert that trend persists.
Record opening realized volatility, decision minute and executable spread as continuous
conditioning diagnostics. Do not split on exact minute, separate spread bands or many
volatility bins. Pool across valid decision times ONLY under the training-block transport
check below; if time-specific behavior is materially inconsistent, report failed pooling
and abstain rather than discover a winning time cohort on the holdout.

Average repeated slots within symbol/day before pooling equal-weight symbol-day means
into one daily group vote. Retain the existing first-frozen-minute-slot deduplication;
intraday repeats and correlated peers never increase the independent-day count.
Use only outcomes whose actual resolution availability is strictly before the next
forecast. Use the same source/horizon and keep overlapping days together.

For each family/asset group, shrink a market-state gross mean toward the group's
all-state mean with fixed weight n_state/(n_state+10), then shrink the target mean toward
that state estimate with fixed weight n_target/(n_target+10). Counts are prior daily
clusters; ten prior days is a preregistered regularization choice, not a fitted claim.
Target observations also appear in the group: use leave-target-out peer estimates to
avoid counting them twice. Missing peers use the target estimate alone. Require at
least five target training days; otherwise keep the candidate research-only/abstaining.
Do not mix different horizon returns or infer short/options profitability from long rows.

Before transporting peer evidence, use prior-only target-versus-peer dispersion and
market exposure diagnostics. Use the lesser target and shrunken lower estimate,
with the larger ordinary/three-lag HAC daily standard error and the existing t(4)
critical value as a conservative descriptive screen. The temporal dependence adjustment
follows [Newey and West](https://www.nber.org/papers/t0055); it does not validate
this market application or the chosen finite-sample cutoff. Very sparse, inconsistent or
unrepresented targets remain insufficient; pooled n is not a substitute for local
support. These approximations do not establish calibrated coverage or independence.

## Costs and baselines

At each decision, deduct actual observed half-spread on both sides plus the unchanged
base 1 bp slippage / 0.1 bp fee and stress 3 bp slippage / 0.2 bp fee per side, with the
existing spread floors. Reuse `Costs.net`; never manufacture future executable sides.
Modeled exit spread assumptions and actual broker fills/PnL stay distinct. Freeze
costs before outcomes. Report raw, base-net, stress-net, residual and downside results.

On identical timestamped eligible windows, report paired daily differences against:

- Cash (zero equity exposure and zero modeled trading return).
- Each unchanged v1 Alpha family and v1 Evidence-Gated selector.
- Equal-weight underlying exposure across the predefined valid research universe.
- The frozen benchmark exposure over the same entry/exit window.

Report trade coverage, day counts, turnover and abstentions alongside returns. Outperforming
cash alone does not establish Alpha: positive market drift/beta must not be labelled edge.
Experimental actual execution and its fees/PnL are a separate policy dataset, never
substituted for these matched counterfactuals or for proven signal economics.

## Temporal out-of-sample protocol

Register a start timestamp before collecting any v2 evaluation observations; that
registration and the protocol hash must be immutable. Existing history is descriptive
coverage/engineering input only, never the new holdout. For each predefined transport
unit, collect 40 distinct prospective training days, then a single contiguous 20-day
future evaluation block. Purge training signals whose endpoint or resolution availability
crosses the test boundary. All preprocessing/means/cost metadata available to a test
prediction must precede it. Freeze hyperparameters and pooling assumptions after the
training block; sequential state updates during test may use only previously resolved
outcomes. Do not revisit the same holdout to choose regimes, horizons or shrinkage strengths.

Forty plus twenty days is an operational minimum, not an assurance of statistical power.
After the fixed block, report paired daily uncertainty (including temporal dependence),
base/stress costs, leave-target-out stability and market exposure sensitivity. Report all
families with their preregistered comparisons; do not name a winner without accounting
for the three hypotheses and correlated comparisons. If diagnostics contradict pooling,
report an inconclusive/failed transport result. Any revised protocol needs a new timestamp
and new future holdout. No automatic promotion or LIVE selector replacement follows.

## Data readiness

Use actual append-only prospective economic rows, not scanner discoveries, synthetic
fixtures, impossible windows or broker fill counts. Count distinct eligible days by
family/version/source/benchmark/asset group/horizon/delay and target training days.
Record the observation cutoff and exclusions in ignored `work/strategy-usability/`.
With fewer than 40 valid training days, fitting this model is premature; without a
separate 20-day prospective holdout it cannot be evaluated. Missing synchronized market
or benchmark outcomes remain unavailable. The repair's data-readiness artifact reports
the current counts; no fit, backtest rerun or exploratory profitability is asserted here.
