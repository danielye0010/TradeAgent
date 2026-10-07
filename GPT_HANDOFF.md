# Daily signal timing repair — 2026-10-07

**NOT_READY.** The timestamp blocker is repaired and passed live evaluation.
The one newly authorized review-only attempt then HALTed because official Robinhood
`search` returned a broker-tool error during CANARY instrument classification.
It was not NO_TRADE. No canary order was constructed or reviewed.

- Source commit on clean `rsi-v0.2`: `59581ae79ab720f25714489a4916c00ca9e2d630`. PR remains unmerged; no push.
- Root cause: the new collector conflated completed bar end with current decision,
  while frozen snapshots required quotes/history to be available by that same end.
  Truthful later receipt could not satisfy those constraints. The earlier attempt
  actually used five-minute data and lost its raw history; its filtered-bar failure
  was not proof of a daily-provider failure.
- Fix: explicit daily signal label `signal_bar_begins_at` preserves original provider
  identity. Calendar mapping derives official XNYS completion, including early closes.
  Completed close <= actual history receipt <= actual current decision; quote and
  book freshness still use current validation time. Existing intraday exact-end
  checks remain. No timestamps are rewritten/backdated and no historical N-second
  freshness rule was added.
- The review adapter now requests regular-session daily closes as specified; this
  is an input-frequency change from the prior new five-minute adapter. This branch's
  RSI is its research population, not an RSI oscillator. No oscillator was invented.
  Strategy/feature numerical sources, parameters, ranking, universe, risk, order
  construction, execution, grant/key policy and MCP 1.7.0 pins are byte-identical.
  Tests compare all seven families' floating-point outputs bit-for-bit for identical
  completed closes. Exact reviewed old/new composite hash compatibility preserves
  immutable registered versions; unknown implementation drift still fails closed.
- Daily labels resolve only as UTC midnight date labels, New York midnight or exact
  regular open. Ambiguous/non-session/future labels fail closed. Today's candle is
  excluded before calendar close; after close it requires a real provider bar.
  Canonical final bar start/session close identifies the daily signal. Repeated
  observation clocks do not create another snapshot, population or review.
- Files: `calendar.py`, `canary_review.py`, `research/domain.py`, `research/lab.py`,
  focused daily tests, replacement of the old bug-expectation test, narrow protocol/
  review docs, deployment manifest and this handoff. No database schema migration.
- Validation: **561 passed in 43.35s**; focused **90 passed in 6.56s**. Ruff lint/
  formatting, compileall, 56 package-module imports, pip check, build/Twine,
  wheel/source identity, project/privacy/provenance/manifest checks and fresh
  synthetic demo passed. Synthetic daily review proves one review, zero intents/
  writes, unchanged production authorization and duplicate suppression.
- Existing configured review window: XNYS regular session only, 09:30–16:00 ET
  (08:30–15:00 Chicago today). It was open; it was not widened. Exactly one new
  live attempt ran. Authentication, origin/schema, manifest, regular session,
  account/accounting/startup reconciliation and daily RSI/scan/rank passed.
- Live daily data: 501 completed bars each for OPEN/SPY; latest provider label
  `2026-10-06T00:00:00Z`, mapped close October 6 at 16:00 ET. Live observation
  October 7 at approximately 12:55 ET. All seven predictions persisted.
  Mean reversion ranked first and selected long; relative strength ranked second,
  bearish, resulting in NO_TRADE for that expression. Controls were not selected;
  opening/gap families abstained because session reference inputs remain unavailable.
- CANARY classification failed at the official frozen request
  `search(query=OPEN, asset_type=equity, limit=20)`. Exact provider rejection reason
  was not retained. Deterministic canary selection, order sizing and order-specific
  risk checks were NOT_RUN. Candidate/quantity/limit/notional/review: none.
- Authoritative actual reads/review/place/cancel: **10/0/0/0** (one failed search
  attempt included). Previous authorized task: 9/0/0/0; combined two distinct
  authorized launches: **19/0/0/0**, not an automatic retry.
- Unresolved reporting defect: `MCPError` escaped the existing handler. Its finally
  block journaled the true counters, but CLI finally wrote default 0/0/0/0 to
  `run/result.json`. Use `journal-result.json` and matching SQLite/JSONL event as
  authoritative; originals are preserved. No out-of-scope error-handler edit.
- State: new audit has 1 HALT run, 6 matching events, 0 intents/budgets; lease empty,
  lock released, integrity ok. RSI store retains 7 original version records and now
  has 1 snapshot, 7 predictions, 7 selections and 7 research plans. Existing
  execution SQLite/log/lock bytes unchanged. No grant, key enrollment or write.
- Finalization scope note: unrelated untracked `scripts/install_shadow.py` and
  `src/tradeagent/prospective/` appeared after the validated/committed repair.
  They were left untouched and unstaged. The final working tree is not clean;
  the combined executable inventory is not covered by this task's freeze/
  validation. Do not deploy that combined tree as the tested source snapshot.
- Next prerequisite: separately resolve official classification search failure and
  the incomplete CLI failure report. Today's completed-bar signal is already
  captured; do not reset/delete evidence or force another same-bar preparation.
  No automatic retry, scheduler or execution follows.

Owner-only detailed evidence:
`$HOME/.local/state/robinhood-agent/daily-signal-timing-20261007/`:
`GPT_HANDOFF.md`, `inspection.json`, `observed-result.json`,
`journal-result.json`, `state-before.json`, validation/window checks,
`live_attempt_claim.json`, `run/state.sqlite3`, `run/events.jsonl`, `stderr.txt`.
Private account/market details remain outside Git.

---

## Prior task handoffs (historical status)

# Review-only RSI CANARY handoff — 2026-10-07

**NOT READY FOR USER-CONFIRMED MANUAL CANARY.** Exactly one live attempt HALTed at
`insufficient completed RSI bars`; it was not NO_TRADE. No signal, selected
candidate, proposed quantity/limit/notional, or official review was produced.
There was no second live attempt after the local collector correction.

- Branch: `rsi-v0.2`. Implementation: `f88fe1c68be4e7724266a22fc2d3acaeafc2c3b7`.
  Final collector correction: `35d372fb6dfcc81e5ff076e6cf8c94dabc578763`. No push or PR merge.
- New isolated action: `tradeagent canary-review`; normal RSI scan/ranking and
  underlying proposal, then unchanged one-share selector and risk gates.
  It uses the existing `review_equity_once` boundary at most once.
  Metadata-only direct MCP provides the full inventory; authenticated Codex MCP
  exposes only existing READ tools plus review. Execution transport is not
  constructed, and execution/RPC/send layers reject all placement/cancel/write
  calls before network. Review allowance is consumed before I/O; no retry.
- Production verification key remains unset. No enrollment, grant, signing,
  execution release or budget consumption. Execution-capable paths retain their
  production-key requirement. RSI/strategy/ranking/risk/execution/policy/config
  source bytes were preserved.
- Files: `src/tradeagent/canary_review.py`, `src/tradeagent/canary_review_cli.py`,
  isolated routing in `src/tradeagent/cli.py`, `tests/test_canary_review.py`,
  `docs/canary-review.md`, regenerated `docs/deployment_manifest.json`, this handoff.
- Exact official MCP 1.7.0 origin/schema/version checks passed. Preflight manifest,
  regular XNYS session, dedicated account/accounting and startup reconciliation
  passed; RSI snapshot gate failed. Classification, daily-history/candidate gates,
  order-specific risk gates and review were NOT_RUN. Complete per-gate results
  and private account evidence are outside Git.
- Actual broker reads/review/place/cancel: **9/0/0/0**. One live launch; no broker
  calls after halt. New audit journal: 1 HALT run, 4 matching SQLite/JSONL events,
  0 intents, no side-budget use; integrity ok, empty lease and released lock.
  Separate RSI store: 7 unchanged default versions registered at actual time,
  0 snapshots/predictions/selections/plans; integrity ok. Existing operator
  databases/logs/lock were byte-for-byte unchanged (3 historical runs, 0 intents).
- Validation: pre-live **514 passed in 39.79s**; final correction **515 passed in
  38.92s**, focused 24 passed. Ruff lint/format, compileall, pip check, build/Twine,
  exact wheel/source match, project/privacy/provenance/manifest checks, CLI
  validation and fresh synthetic demo passed. Tests cover unset production key,
  independent write denials, one-use timeout/replay, schema/version drift,
  frozen RSI/selector parity and unchanged risk decisions.
- Collector correction: optional `interpolated` now excludes only explicit true,
  matching the existing reader; removed the new collector's extra five-bar
  minimum in favor of the frozen nonempty requirement. Raw history/count/end
  evidence is now retained before snapshot validation. The spent attempt did
  not retain raw historical bars, so its exact provider count/cause is unresolved.
  The correction has local regression coverage, not a subsequent live result.
- **Independent remaining timing blocker:** the new adapter uses latest completed
  bar end as decision, but actual quote/history receipts occur later. Frozen
  `MarketSnapshot` requires quote/bar availability <= decision and final bar end
  == decision. This receipt-based adapter cannot satisfy that alignment; it fails
  closed. No timestamps were backdated and no frozen rule was relaxed.
  Next step is separately scoped review of truthful live snapshot timing, followed
  by a separately authorized fresh single review attempt. Do not claim this
  path can currently reach live review or reuse today's stale evidence.
- Durable owner-only evidence:
  `$HOME/.local/state/robinhood-agent/canary-review-20261007/`
  (`GPT_HANDOFF.md`, `validation-summary.json`, `inspection.json`,
  `live_attempt_claim.json`, `run/result.json`, SQLite and JSONL).
  No reviewed transaction is actionable; any eventual purchase requires personal
  user confirmation and Robinhood action.

---

## Historical handoffs (superseded status; retained for provenance)

# MCP 1.7.0 canary repair handoff — 2026-10-07

**NOT READY FOR USER-CONFIRMED CANARY.** The version blocker is repaired;
the normal production gate stops at `AUTONOMOUS release closed: pinned production
key/phase required`. This branch's `AUTONOMOUS_PUBLIC_KEY_SHA256` remains unset.
No key enrollment, grant creation/signing, policy change, or execution occurred.

- Repair commit on `rsi-v0.2`: `27ac443d45de1322aa6809bf8aab56b5b8fe8a1d`. No push or draft PR merge.
  The independent commissioning commit was preserved.
- Official origin: https://agent.robinhood.com/mcp/trading.
  Old pin **1.6.2** → live/reviewed pin **1.7.0**; server name
  `robinhood-trading`; protocol **2025-11-25**; complete inventory **84 tools**.
  A fresh normal authenticated metadata handshake passed after the repair.
- **Exact structural differences: none across all 18 required tools.** Input and
  output schemas and pinned annotations/readOnlyHint are unchanged; equity
  review/place/cancel contracts and option execution contracts match. Descriptions
  are ignored exactly as before. Exact version and structural rejection remain.
  No new tools or permissions were enabled. The old snapshot is retained.
- Files changed: `src/tradeagent/schema.py`, new
  `src/tradeagent/contracts/official-1.7.0.json`, `tests/test_contract_pins.py`,
  pin fixtures in `tests/test_execution_lifecycle.py`, `tests/test_canary_execution.py`,
  `tests/test_standalone.py`, `docs/deployment_manifest.json`,
  `docs/MCP_CONTRACT_1_7_0_REVIEW.md`, and this handoff.
  Strategy/RSI/selection/risk/execution/grants/budgets/sizing bytes were not edited.
- Validation on the final branch base: **491 passed in 38.15s**; Ruff lint/format,
  compileall, pip check, isolated build, Twine, project/privacy/provenance checks,
  exact wheel/source comparison, deployment manifest and fresh synthetic demo passed.
  Focused checks: 201 passed. Contract-only earlier-base isolation: 474 passed.
  An initial shared-scratch full run had one synthetic SQLite readonly failure;
  isolated single-test and full reruns passed. Concurrent scratch/source activity
  was observed; the initial failure's causal mechanism was not proven. No execution
  source fix was made for it. Validation evidence is retained externally.
- **Actual broker reads/review/place/cancel for this task: 0/0/0/0**, including both
  metadata handshakes, repair/validation and the stopped preflight. Diagnostics
  hard-blocked every tools/call before network. Synthetic tests are not broker calls.
- Operational SQLite integrity: **ok**. Existing state/lease databases, JSONL and
  process-lock file hashes are byte-for-byte unchanged: **3 runs, 25 events,
  0 intents**, no side budgets consumed, empty lease; process lock obtainable and
  released. No real SHADOW cycle, submission or cancellation.
- Candidate/proposed parameters/review: **NOT REACHED**, not NO_TRADE. Config/risk,
  manifest, authentication and live schema checks passed. Account identity,
  NAV/cash/deposits/capital, positions/orders, startup reconciliation, classification,
  candidate selection, quote/book/session/history/liquidity/risk and signed-grant/
  review gates were **not run** after the production-key gate failed.
- Human boundary: separately resolve the existing production-key enrollment and
  owner-signed CANARY authorization for the exact reviewed deployment/account/context,
  then repeat fresh gated preparation/review. This task does not authorize signing,
  enrollment, placement or bypass. Any securities purchase requires the user's
  separate personal Robinhood action. No expired or unreviewed plan is actionable.
- The older operator checkout was not repaired or redeployed; it was read only for
  the explicitly requested integrity proof. Its production context is not this branch.

Evidence: [contract comparison](docs/MCP_CONTRACT_1_7_0_REVIEW.md) and owner-only
`$HOME/.local/state/robinhood-agent/mcp-contract-repair-20261007/`:
`metadata.json`, `comparison.json`, `validation-summary.json`, `final-checks.json`,
`final-source-files.json`, `preflight-result.json`, `state-before.json`,
`state-after.json`, `commit-result.json`.

---

# TradeAgent v0.2 RSI handoff

## Git and preserved state

- Starting TradeAgent `main`: `167397fe66ff342e65b97e8b503f5fe76839430f`, clean.
- Frozen annotated tag: `v0.1-execution-core`, pushed to origin at that commit.
- Development branch: [rsi-v0.2](https://github.com/danielye0010/TradeAgent/tree/rsi-v0.2); never merged into `main`.
- Draft [PR #1](https://github.com/danielye0010/TradeAgent/pull/1) targets main and remains unmerged.
- Implementation commit: `9c9b051f72a10f67033ec94f34d984ca80fdb5a7` — prediction-first
  RSI loop and isolated v0.1 execution controls.
- Documentation/demo commit: `39c103e66e3248bd08a01d22ab5978a281077255`.
- Handoff commit: `c06d6fa68b7196ded643ed6cd43addd6c1479c49`.
- Remote README edit `78d17e76c503b76c816277668b6d8c5c35b175a3` was preserved by
  merging origin/main into rsi-v0.2, including its full-stack product tagline.
  This did not merge rsi-v0.2 into main. Final HEAD is in the completion receipt.
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
