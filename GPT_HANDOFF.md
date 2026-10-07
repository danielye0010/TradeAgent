# TradeAgent v0.2 RSI handoff

## Git and preserved state

- Starting TradeAgent `main`: `167397fe66ff342e65b97e8b503f5fe76839430f`, clean.
- Frozen annotated tag: `v0.1-execution-core`, pushed to origin at that commit.
- Development branch: `rsi-v0.2`; never merged into `main`.
- Implementation commit: `9c9b051f72a10f67033ec94f34d984ca80fdb5a7` — prediction-first
  RSI loop and isolated v0.1 execution controls.
- Documentation/demo commit: `39c103e66e3248bd08a01d22ab5978a281077255`.
- This handoff is the final logical commit. Its exact SHA is available from
  `git rev-parse HEAD` and the completion message; embedding its own SHA is impossible.
- Canonical repository: [danielye0010/TradeAgent](https://github.com/danielye0010/TradeAgent).
  Actual work was done in the existing WSL TradeAgent checkout, not the incomplete
  Windows export configured as the app project. The older private development
  checkout's uncommitted changes were left untouched.
- No operator DB, broker account, credentials, private keys or real-account artifacts
  were opened. No real broker request, review, placement or cancellation occurred.
  The old final real-money transaction remains canceled.

## Working architecture

`strategy.py` now defines an account-free Strategy -> immutable Prediction interface.
`research/` contains domain, market, store, lab, outcomes, expressions, attribution,
learning, evolution, selector, reporting, feedback, agent_market and offline demo.
`cli.py` exposes the prediction laboratory. Existing broker/account/risk/execution,
contract validation, state and reconciliation modules remain downstream.

Former top-level strategy/runner/CLI, policy, release, canary, autonomous, standalone,
standalone_cli, supervised_cli and standalone_reference workflows moved into
`tradeagent.legacy`. Their former top-level locations are gone or replaced with the
new API. No validated broker lifecycle was deleted. Signing/enrollment/manifest/canary
requirements no longer enter the normal research path; legacy checks remain fail-closed.
The project checker no longer requires a deployment manifest on the normal path.
Existing optional manifest regeneration still supports the legacy library.

New canonical docs: README and docs/architecture.md, research-protocol.md,
getting-started.md, deployment.md and safety.md. CI runs the offline RSI demo and
preserved execution simulation. Vendored bytes and all licenses/provenance are intact.

## Data and prediction contract

New `experience.sqlite3`, schema version 1, is separate from `state.sqlite3`.
Transactional initialization uses `PRAGMA user_version` plus `schema_migrations`.
Unknown schemas halt; old execution journals are not converted or modified.
Tables normalize strategies, versions/generations, snapshots, predictions, observations,
option observations, outcomes, selections/plans, shadow/live expressions, counterfactuals,
alpha/live attributions, learner runs, version/regime scores, lessons/revisions,
mutations, evaluations, promotions, rejections and retirements.

Evidence is append-only, including protections against SQL REPLACE using primary
or alternate unique identities. Foreign keys and timing/identity guards bind records.
The only mutable metadata is strategy enablement and champion pointer; changing the
pointer requires a promotion event. Research commands use a process lock and DB
transactions on persistent local Linux storage. The execution fenced lease is unchanged.

Prediction fields: ID, exact strategy/version, snapshot ID, symbol, UTC decision time,
horizon seconds, direction -1/0/+1, expected return, confidence, immutable features,
context, distribution metadata, creation time and model version. Version parameters
and implementation hash covering strategy/features/domain code are durable. Changing
implementation without an explicit new version fails closed. Initial distributions
are uncalibrated point estimates; confidence is coarse, not a fitted probability.

## Exact shadow and outcome workflows

`init` registers five baseline families: opening momentum, opening reversal, gap
continuation, relative strength and mean reversion; null and deterministic random
controls also run. Gap reversal is implemented as an additional selectable family.
Opening/gap families abstain outside the first 90 minutes or without references.
Every enabled nonrejected/nonretired version predicts, including unselected candidates,
challengers and former champions. Controls/challengers cannot be selected as incumbents.

`scan --input` consumes a contemporaneous symbol/benchmark snapshot. Bars and quote
availability precede the decision; latest bars end at that decision. System-clock
capture is within 120 seconds. Known future observations block backdating. Snapshot,
predictions, conservative quote snapshots, selection and TradePlans commit together.

Later, `resolve --input` ingests same-source future bars and optional option quotes.
It resolves every due forecast with contiguous symbol AND benchmark paths ending
at the exact horizon. Missing data stays unresolved. Conflicting source corrections
halt, identical reruns do nothing, and forecasts remain unchanged. Raw/benchmark/
residual return, direction-oriented MFE/MAE, population log-bar volatility and source
observation IDs persist. Sector adjustment is explicitly unavailable.

Counterfactuals include no trade, long underlying and all decision-time quoted calls/
puts. Options buy ask and sell exact-horizon bid of the identical contract. Missing
quotes stay unavailable, including if absent at resolution; no mark-based replacement
or automatic late counterfactual revision is implemented. Underlying entry is ask and
exit is bar close less decision half-spread, explicitly an estimated bid. Fees/fill
probabilities are not modeled. Default selected plans use long underlying/NO_TRADE;
bearish forecasts cannot create underlying shorts.

## Exact attribution and lessons

Numerical attribution separately classifies direction error/correctness, insufficient
edge/noise, correct abstention or missed move; coarse magnitude error; path reversal;
prior regime mismatch; correct thesis with losing matching long-premium expression;
and observed execution error when feedback already exists. Unobserved execution is
explicit. Noise tolerance is max(10bp, 25% historical bar volatility); magnitude
error exceeds max(noise, twice predicted absolute return). Regime diagnosis requires
ten prior daily clusters. Labels are diagnostics, not causal proof.

`import-execution` accepts externally reconciled fill observations and appends separate
live attributions for late feedback. Adverse entry slippage over 10bp or rejection/
failure labels a fill error. Alpha attribution and training observations never change.
No automatic broker-fill collector exists; external reconciliation is an attestation.

Lessons retain scope, exact version/regime/pool condition, observation, daily evidence
count, supporting/contradicting prediction IDs, confidence, status and timestamps.
Confidence = (supporting daily fraction sum + 1)/(days + 2). At least ten days and
confidence >=0.7 support a provisional lesson; a supported lesson weakens below 0.6.
Updates append revisions. Explicit lesson retirement appends a retired revision.

## Exact daily learning and selection

Fixed score = clip(direction * residual - 10bp for active predictions, -2%, +2%).
It is an alpha diagnostic, not executable PnL or leverage. Each UTC day is one mean
cluster per version across correlated symbols/horizons. Weights decay with 30-day
half-life and shrink toward zero with a ten-day/1%-sigma prior. An effective-n
standard-error approximation and last-five-day degradation reduce confidence.

Selector weight = clip(1 + (shrunk mean - shrunk uncertainty - degradation)/0.005,
0.1, 2). Coarse magnitude calibration uses a shrunk favorable-realized/predicted
ratio capped [0.25,2]. Regime subsets use the same estimator. A changed resolved
evidence set appends new learner state; identical evidence does not count twice.

Rank score = abs(expected return) * confidence * version weight * calibration *
sqrt(regime compatibility) * (0.75 + 0.5 * directional agreement). Only state strictly
before the decision and from the same evidence pool is used. Up to three champion
candidates are selected per scan. Raw-score baseline ranking AND selection are
persisted. Inspection compares both and reports exact version/generation/regime results.

## Exact weekly evolution

Agent/human JSON proposals supply a hypothesis and bounded whitelisted parameters.
Default evolution tests a 25% stronger abstention threshold. Parents never change in
place: a new version records parent, generation, hypothesis, optional lesson and a
frozen plan. There is one active challenger per strategy and at most one proposal
per parent/week/pool. Historical replay is not promotion proof; current sanity
screening checks parameter bounds.

The plan fixes paired symbol/decision/horizon evidence after challenger creation,
the first 60 distinct daily cluster means, a 180-calendar-day deadline, 10bp required
improvement, and alpha = .05/(attempt*(attempt+1)). Difference range is .08; screening
radius = .08 * sqrt(log(2/alpha)/(2*n)). Earlier evaluations only report progress.
The single final look promotes only when lower bound >10bp, the incumbent is unchanged
and evidence is prospective. Otherwise the final look/deadline rejects. Repeated
reads preserve n/evidence hashes; all failed history remains. Retirement is explicit.
The independence assumption is not established by daily clustering: this is an
operational research screen, not a verified proof of market alpha.

## Agent, quant and execution responsibilities

Agents interpret attribution and regimes, propose hypotheses and select research
priorities. Constrained proposals enter through JSON, with no automatic LLM service.
Quant code owns deterministic features, returns, residuals, counterfactuals, weights,
calibration diagnostics and promotion evidence. Execution owns account/instrument/
capital bounds, order identity, submission, fills and reconciliation.

Existing official Robinhood MCP clients, pinned broker contracts, options chain/
quote access, durable intent, duplicate suppression, no blind retry, locks and
crash/fill recovery remain. Optional IV/delta/gamma/theta/vega are now preserved by
the broker OptionQuote normalization when supplied. Long call/put expression APIs
and executable-side shadow evaluation work. Automatic option EV/allocation and
research-plan-to-live bridging are deferred. Agent-market input placeholder has zero
live weight; no Agent Crowd strategy was built.

## CLI, verification and evidence

Research commands: init, scan, resolve, learn-daily, evolve-weekly, inspect, demo,
retire, retire-lesson, import-execution. Execution compatibility: `execution` with
validate/tools/shadow/inspect/simulate, plus the old aliases; `inspect --config`
routes to the execution journal. Real canary/run-once commands are not in product CLI.
No daemon, scheduler or final production schedule was installed.

- Baseline: 365 tests passed in 18.53s.
- Final: **402 passed in 22.71s**; all 365 existing tests retained, 37 added.
- Ruff lint and format, compile/import, pip dependency check, wheel/source build,
  Twine, provenance/documentation checks and Git whitespace checks passed.
- Final source and built-wheel demo summaries matched exactly; repeated runs also matched.
- Offline demo: **150 predictions, 150 outcomes, 150 attributions, 15 learner runs,
  222 lesson revisions, 5 challengers, 9 paired shadow days each**. Weights changed;
  57 attributions separated correct direction from losing long-option expressions.
  Challengers remain `continue_testing`; synthetic evidence did not promote them.
- Sanitized committed outputs: docs/examples/rsi-demo-summary.json,
  rsi-demo-snapshot.example.json and rsi-demo-future.example.json.
- Full local evidence: work/rsi-v0.2/final-tests.txt, validation.json, final-build.txt,
  final-demo/experience.sqlite3, final-demo/events.json and final-wheel-demo/.
- Build distributions: work/rsi-v0.2/final-dist/. Runtime/test artifacts stay ignored.

## Remaining integration limits and best next experiment

No external blocker prevented local work or GitHub publishing. Remaining integration
work is explicit: production timestamp-faithful market collection, external scheduling,
calibrated option EV allocation and a separately validated plan-to-live bridge.
Truthful vendor availability and independent-market evidence cannot be manufactured
by schema guards. There is no prospective market performance or profitability claim.

**Next experiment:** collect 60 prospective sessions of QQQ and IWM against SPY at
one fixed opening decision window and one-hour horizon, with zero live money. Freeze
data availability, the 10bp score and current families; compare actual learned
selection against the saved raw-score baseline and both controls. The question is
whether attribution-driven daily weighting improves held-out daily residual scores
without losing coverage. Build the read-only timestamp-faithful collector first;
do not substitute replay or alter the metric after seeing results.
