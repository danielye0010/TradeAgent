# Research protocol v1

These rules implement an evidence machine, not a profitability claim.

## Scan and resolve

Initialize versions before their decision time. `scan` accepts a single symbol and
benchmark snapshot, within 120 seconds of its decision, and persists every enabled
version. Intraday input bars must be complete and actually available at decision time;
their final end may precede the decision by at most 120 seconds, with symbol and
benchmark ends aligned. Explicit daily signal snapshots
instead retain the provider bar identity and mapped XNYS session close, with
completion <= actual history availability <= current decision. Today's unfinished
daily bar is excluded; canonical completed-bar identity suppresses repeated daily
signals independently of observation time. Quotes must be at most 120 seconds
old. Session open and previous close references require prior timestamps. Opening
and gap families abstain outside their first 90 minutes; absent fields cause abstention.

`resolve` imports future observations from the same source, at their actual later
availability. For each horizon it requires contiguous symbol AND benchmark bars from
decision to the exact endpoint. Missing data stays unresolved; no interpolation or
nearest-close fallback exists. Corrections conflicting with frozen observations halt.
Repeated identical imports and resolutions are idempotent.

Raw return = endpoint close / decision entry price - 1. When the final intraday
signal bar ends before the decision, entry price is the first decision-minute bar's
open, observed later during resolution; this prevents counting predecision moves.
The benchmark uses the same entry model. An exactly aligned signal keeps its
existing close-based entry; daily signal snapshots retain their separate existing
contract. Entry model/prices and signal bar end persist in outcome metadata.
Residual = raw - benchmark return.
Excursions use path high/low, oriented to prediction direction and bounded by zero;
abstention retains long-oriented diagnostics. Realized volatility is the population
standard deviation of observed log-bar returns, without annualization. Sector
adjustment is explicitly unavailable. These are horizon forecasts, not realized fills.

## Expressions and attribution

Every prediction gets no-trade and underlying counterfactuals plus all supplied call
and put quotes. Option entry uses ask, exit uses the exact-horizon observed bid of the
same standard contract. Quote identity, expiry, strike and multiplier must match.
Missing quotes yield unavailable PnL; marks cannot substitute for executable sides.
Fees and fill probability are not modeled. Underlying uses entry ask and endpoint
close less the decision half-spread: this is explicitly an estimated exit bid,
not an observed executable quote. PnL units are one share or one standard contract.

The default selected plan is an unleveraged long underlying or NO_TRADE; short
underlying exposure is excluded. The expression API can choose a quoted long call/put,
but automatic option allocation and option EV ranking remain deferred: the initial
return estimates are not calibrated distributions. Quote IV/Greeks can be retained
in research input. Existing option-chain normalization and simulated execution remain.

Attribution separately records direction/noise/correct abstention/missed moves;
magnitude error beyond a coarse frozen tolerance; favorable-path reversal; mismatch
against prior regime evidence; correct thesis with losing matching long-premium
counterfactuals; and observed fill error when live calibration records were imported
before attribution. The noise tolerance is max(10bp, 25% historical bar volatility).
Regime diagnosis needs at least ten prior daily clusters. Without live records,
execution is `unobserved_shadow_only`. Labels are deterministic diagnostics, not
proof of causal failure. Attribution never reduces a strategy to option PnL sign.
Late externally reconciled fills are imported with `import-execution`. They append
separate `live_attributions` without changing the alpha attribution or training score.

## Daily learner

The fixed learning score is clip(direction * residual_return - 10bp when active,
-2%, +2%). It is a normalized alpha diagnostic, not executable PnL or leverage.
Multiple symbols/horizons in a UTC decision day form one mean daily cluster per
exact version. Day weights decay with a 30-day half-life. The zero-centered prior
has ten days and 1% daily sigma. The shrunk mean, effective sample size and posterior
standard-error approximation determine a conservative weight; recent degradation is
positive all-history mean minus the last five-day mean, shrunk by evidence.

Weight = clip(1 + (shrunk mean - shrunk uncertainty - degradation)/0.005, 0.1, 2).
Calibration scales confidence using a shrunk ratio of favorable realized magnitude
to predicted magnitude, capped at [0.25,2] before shrinkage. It is not probability
calibration. Regime compatibility uses the same estimator on each regime subset.
Updates append only when the resolved evidence set changes, and never rewrite
observations, strategy parameters or historical selections.

Lessons aggregate exact-version/regime attribution by daily clusters, retaining
all supporting and contradicting prediction IDs. Confidence is (supporting daily
fraction sum + 1)/(days + 2). Ten days with confidence >=0.7 support a provisional
lesson; previously supported lessons weaken below 0.6. Each update appends a revision.
Retirement of a strategy is an explicit reasoned command; lessons are retained.
Automatic lesson retirement is not implemented.
`retire-lesson --lesson-id ID --reason TEXT` explicitly appends a retired revision.

## Meta selector

Raw-score baseline = abs(expected return) * confidence. Learned rank score multiplies
this by version weight, calibration, square root of regime compatibility, and
(0.75 + 0.5 * directional agreement). All inputs are explicit in stored rationale.
Only learner state from BEFORE decision time and the same evidence pool is allowed.
Up to three current champion candidates per scan are selected; challengers and
controls always remain shadow-only until promotion. A selected bearish forecast may
still yield NO_TRADE under the underlying-only default expression boundary.

The raw baseline is saved for every candidate. This first version does not claim
that the learned selector outperforms raw ranking. Compare both prospectively.
`inspect` reports actual frozen learned/raw selections, daily diagnostic scores and
performance by exact version, generation and regime.

## Weekly evolution

A supplied agent hypothesis can alter only bounded, whitelisted parameters in a
known strategy family. Otherwise the default hypothesis tests a 25% higher abstention
threshold. One active challenger per strategy prevents concurrent parent replacement;
at most one new proposal per UTC week/parent/evidence pool is accepted. Parameters,
parent, implementation hash, generation, hypothesis and optional lesson link persist.
Bounds checks are the initial sanity screen; historical replay is not final proof.

Each creation freezes the score above, pairing keys (symbol, decision, horizon),
60 distinct daily clusters, 180-calendar-day deadline, minimum improvement 10bp,
and alpha = 0.05/(attempt*(attempt+1)). Attempt counting spans all stored mutations.
Only predictions issued AFTER creation in the registered evidence pool can pair.
The first 60 paired days are the one fixed final look. Earlier evaluations record
progress only. Daily paired differences have range 0.08; the conservative screening
radius is 0.08 * sqrt(log(2/alpha)/(2*n)). Promotion requires lower bound >10bp,
the unchanged incumbent, and prospective evidence. Otherwise the final look or
deadline records rejection. Repeated reads use the same evidence hash and add no n.
Synthetic/replay observations cannot promote; missing pairing does not fabricate n.

This Hoeffding screen assumes independent bounded daily observations. Daily clustering
reduces duplicated correlated votes but does not establish market independence.
Promotion is an operational research decision, not established statistical proof of
alpha. Fixed costs, clipping, family restrictions and no adaptive final looks make
the limitations visible. A rejected challenger and all its losing predictions remain.
The bounded independent-sample inequality follows [Hoeffding's original paper](https://www.tandfonline.com/doi/abs/10.1080/01621459.1963.10500830);
the operational cost, clipping and promotion policy here are project choices.

The six-month RSI thesis needs genuinely prospective generation comparisons,
cost-sensitive results and selector controls. A passing synthetic demo cannot answer it.


## Bidirectional daily SHADOW research (v3)

`daily-bidirectional-v3` reuses the daily pipeline and three Alpha families. The
frozen `daily-four-arm-v1` and `daily-news-v2` decisions, configuration and timers
retain their original long-only semantics. Use a separate state directory.

The v3 shortlist remains three symbols. A signed point forecast must exceed its
estimated round-trip underlying cost plus 10bp. LONG, BEARISH and CASH persist in
each arm; Codex Price/News must support the same signal direction. Quant + Codex
ranks the news-supported signals by quantitative edge. Estimates remain uncalibrated;
passing this screen does not establish profitability.

The frozen discovery pool contains 106 stocks/ETFs. The official Robinhood minute
history/quote interface covered 31/60/106 symbols in 13.4/18.4/33.3 seconds on
2026-10-13. See `config/research-universe-profile.json`. Choose the largest complete
measured prefix within 45 seconds; each runtime capture retains that deadline.
Collection uses five-symbol history batches and twenty-symbol quote batches.
Shortlisting also requires observed spread <=0.5% and trailing thirty-minute mean
dollar volume >=$1m/minute. One observation is not a future latency guarantee.

```bash
python -m tradeagent.prospective.daily --protocol daily-bidirectional-v3 \
  --state-dir work/bidirectional-shadow \
  --universe-profile config/research-universe-profile.json
```

Run the capture tick during 10:30–10:37 New York time. Inspect its frozen decision
for `outcome_entry_time` and `outcome_exit_time`; run the same manual command at
each endpoint to capture actual option quotes, then at least sixty seconds after
exit to resolve. Missed quotes are UNAVAILABLE.
Do not point this command at an existing experiment directory.

`scripts/install_shadow.py --daily --protocol daily-bidirectional-v3 --python PATH
--state-dir PATH --universe-profile config/research-universe-profile.json
--prepare-only` prepares and verifies a separate disabled timer. Its minute ticks
at 10:30–11:59 ET cover the variable frozen entry/exit minutes; 12:00 and 17:10
ET resolve later observations. The existing one-shot service and research lock
prevent overlapping capture; ticks never recreate a missed observation. Enable
only after genuine option access and both Codex modes are verified.

The same CLI model and Code Mode host configuration serves both price/news arms;
shell, broker and filesystem tools remain disabled. A v3 news result requires an
actual search event and a cited HTTPS source. The price arm keeps search disabled.
`daily-pnl.md` gives readable instrument P&L and a separate theoretical benchmark;
the append-only experience journal remains the source of truth.

A Put contract is chosen before the decision freezes: earliest expiry 14–35 calendar
days away, nearest liquid of three strikes to contemporaneous spot, standard 100
multiplier, bid/ask spread <=10%, open interest >=100, volume >=10 and both sizes
>=1. Entry requires that same contract's genuine ask; exit requires its genuine
bid. Quote and receipt timestamps must fall in the first sixty seconds of the
planned endpoint minute. Actual times are retained; this is a bounded window
simulation, not an exact-time fill. Quantity is fixed from entry premium/ask size;
insufficient exit bid size makes the outcome unavailable rather than resizing.

Quant Only, Codex Price, Codex News, Quant + Codex, SPY and CASH use paired sessions
and a $1,000 funded instrument budget: fractional unleveraged equity or whole Puts
with unused cash. Reported return is dollar P&L divided by $1,000. Put premium
returns are separately labeled. Spreads are included; unknown option fees are
explicitly unobserved. The arithmetic accepts explicit per-contract costs when
observed. Equity simulations use minute open/close and frozen estimated costs.
Random remains a secondary record and does not determine primary paired coverage.

The separate directional comparison uses $1,000 underlying reference notional.
Its bearish leg is a theoretical negative exposure, excluding borrow/margin costs
and with unbounded short risk; it is neither an executable stock short nor Put P&L
and never enters the funded instrument comparison.

Authenticated server 1.8.0 advertised timestamped option bid/ask quotes but only
OHLC historical bars. The inspected QQQ quote and historical calls returned tool
errors; no usable option pricing observation was obtained. Historical OHLC cannot
substitute for entry/exit sides. Prospective successful quotes are still required
before claiming observed Put performance. Options access is a separate market-only
capability; it cannot read accounts, review/place/cancel orders or authorize LIVE.
