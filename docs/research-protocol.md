# Research protocol v1

These rules implement an evidence machine, not a profitability claim.

## Scan and resolve

Initialize versions before their decision time. `scan` accepts a single symbol and
benchmark snapshot, within 120 seconds of its decision, and persists every enabled
version. Input bars end exactly at the decision. Quotes must be at most 120 seconds
old. Session open and previous close references require prior timestamps. Opening
and gap families abstain outside their first 90 minutes; absent fields cause abstention.

`resolve` imports future observations from the same source, at their actual later
availability. For each horizon it requires contiguous symbol AND benchmark bars from
decision to the exact endpoint. Missing data stays unresolved; no interpolation or
nearest-close fallback exists. Corrections conflicting with frozen observations halt.
Repeated identical imports and resolutions are idempotent.

Raw return = endpoint close / decision close - 1. Residual = raw - benchmark return.
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
