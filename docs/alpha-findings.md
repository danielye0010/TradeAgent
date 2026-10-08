# Alpha findings: frozen public-data experiment

No strategy qualifies for deployment. Validation selected cash; all 330 economic plans are NO_TRADE.

The public Yahoo chart responses contain 14,043 five-minute QQQ/IWM/SPY bars. The
usable experiment covers July 17–October 7, 2026: 20 training, 12 validation,
11 holdout and 12 supplemental sessions. The holdout is September 1–17 after
excluding September 15 and 18 dividend dates. Supplemental dates overlap the
previous FirstRate experiment and are not independent evidence. The first session
lacks a previous close; September 21 is also excluded for a dividend.

Data identity: `cd96141f589941032366da78214ddaf70c7411deaa95164d63dc99ec24dc8464`.
[Machine-readable summary](examples/tradeplan-alpha-results.json) and
[frozen protocol](examples/tradeplan-protocol.json) retain configuration and results.

## Main 60-minute experiment

Decision 10:00 ET, entry 10:05, exit 11:05. Returns below are percentages of total
portfolio capital, with a fixed half-capital sleeve per ETF and cash while inactive.
The round-trip cost assumptions are approximately 2/4.2/10.4 bp.

| Candidate | Trades | Gross | Low net | Base net | Stress net | Base net vs matched SPY |
|---|---:|---:|---:|---:|---:|---:|
| opening_continuation | 6 | +0.104% | +0.044% | -0.022% | -0.208% | -0.077% |
| stabilized_reversal | 2 | +0.225% | +0.205% | +0.183% | +0.120% | +0.066% |
| residual_strength | 7 | -0.073% | -0.143% | -0.220% | -0.436% | +0.083% |
| rule_selector | 4 | +0.357% | +0.317% | +0.273% | +0.149% | +0.097% |
| opening_reversal | 9 | +0.300% | +0.210% | +0.111% | -0.168% | -0.106% |
| same_window | 22 | +0.332% | +0.112% | -0.130% | -0.809% | +0.112% |
| adaptive_pooled | 0 | +0.000% | +0.000% | +0.000% | +0.000% | +0.000% |
| adaptive_regime | 0 | +0.000% | +0.000% | +0.000% | +0.000% | +0.000% |

## Best lead and its limits

The simple rule chooses opening continuation during a gap-aligned market trend,
otherwise a stabilized reversal, with abstention when that family has no long signal.
It earned +0.273% base net and +0.149% stress net over four holdout trades.
Average gross trade return was about 17.86 bp versus modeled round-trip costs
of 4.2 bp (base) and 10.4 bp (stress); base net averaged 13.65 bp per active trade.
Its matched-SPY portfolio returned +0.176%, but its mean beta-adjusted gross
residual was -1.71 bp. Market timing and market exposure remain plausible explanations.
Four trades cannot establish a stable instrument-specific alpha.

At 10:00, stabilized reversal gained +0.232%, +0.183% and +0.381% base net for
30/60/120-minute holds, but all three used the same two holdout trades. These are
correlated horizon sensitivities, not six independent successes. At 11:00 its
60-minute variant made four trades and gained only +0.021% base net while losing
0.103% under stress costs. The result does not justify selecting a new horizon.

The rule had positive base returns in training (+0.319%) and validation (+0.289%),
but validation contained only two rule trades. None of the 18 candidate configurations
met the frozen five-active-day validation screen with a positive return. Cash is
the actual selected portfolio. Both statistical selectors stayed in cash; this
is evidence of insufficient support under their thresholds, not proof of superior
market intelligence. No threshold was lowered to make a strategy qualify.

The 60-minute simple opening reversal control gained +0.111% base but lost
0.168% under stress; conditioned continuation and residual strength lost money
after base costs. Positive residual direction and positive underlying trade PnL
are different questions. No overnight or news-latency strategy was added without
evidence that it would be a better use of this data.

## Interpretation and next experiment

No repeatable net edge is established. The smallest useful next experiment is the
unchanged 10:00 simple rule with 60-minute holdings on multiple years of independent
QQQ/IWM/SPY minute data, followed by prospective bid/ask and delayed-fill observations.
Keep continuation, stabilized reversal, same-window equity and matched-SPY controls.
Do not use this consumed holdout as a new tuning target. Its four rule trades are
a reason to gather evidence, not promote a challenger.

Equity is the only supported research expression. Options remain research-only:
no paired historical option books exist here to establish realistic spread, theta,
IV, capacity or affordability. The implemented option arithmetic accepts real
paired books but no option profitability experiment was fabricated.

After production cleanup finishes, the integration point is the pure
to_execution_intent adapter followed by the existing deterministic account risk
and execution interfaces. Before activation, collect enough prior prospective
evidence to pass the economic gate and implement fresh-quote revalidation plus
the planned horizon exit in that existing owner. Nothing is activated by this branch.

## Reproducibility and limitations

The candidate grid was frozen before evaluation. The first run was retained locally;
a subsequent correctness pass aligned Prediction horizons with delayed entry and
excluded corporate-action dates after inspecting source metadata. No strategy
threshold, horizon grid, selector gate or final-test choice was tuned in response.
All outcomes remain exploratory because the corporate-action eligibility correction
followed the initial result, costs are modeled, historical availability is assumed,
prices may be revised, and only eleven clean holdout sessions are available.
Daily endpoint drawdown omits intraday equity peaks; adverse excursion is separate.
Missing windows use complete-case exclusion and can create coverage bias.
Cash yield, taxes, capacity and actual limit-order fill probability are not modeled.

Prior research on the historical-alpha branch found only two profitable reversal
test trades in an eleven-session FirstRate sample. This result preserves that
caution instead of treating the larger public sample as established profitability.
