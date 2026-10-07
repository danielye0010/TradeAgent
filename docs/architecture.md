# Architecture

TradeAgent is a prospective short-horizon quantitative laboratory. Its loop is:

```mermaid
flowchart LR
    Market --> Population[Strategy population]
    Population --> Predictions[Immutable predictions]
    Predictions --> Selector[Meta selector]
    Selector --> Expression[Trade expression]
    Predictions --> Shadow[Shadow all versions]
    Shadow --> Outcomes
    Outcomes --> Attribution
    Attribution --> Daily[Daily learning]
    Attribution --> Lessons
    Lessons --> Hypotheses
    Hypotheses --> Challenger[Weekly challenger]
    Challenger --> Population
    Daily --> Selector
    Outcomes --> Evaluation[Prospective comparison]
    Evaluation --> Promotion[Promotion or rejection]
    Promotion --> Population
    Expression --> Execution[Separate execution substrate]
```

## Package boundaries

- `strategy.py`: account-free `Strategy.predict(MarketSnapshot, created_at)` interface;
  five initial families plus null/random controls. Strategies can abstain.
- `research/domain.py`, `market.py`: deeply immutable records, timestamps and
  deterministic completed-bar features. Input provenance is recorded; truthful vendor
  timestamps remain a data-provider responsibility.
- `research/store.py`: normalized SQLite experience schema, migration version and
  append-only SQL triggers. `experience.sqlite3` is separate from v0.1 `state.sqlite3`.
- `research/lab.py`: contemporaneous one-shot capture. All enabled nonrejected and
  nonretired versions predict, including challengers and former champions. Predictions,
  selections, plans and quote snapshots commit together.
- `research/outcomes.py`: deterministic exact-horizon path resolution, benchmark
  residuals, excursions, volatility and traceable observation IDs.
- `research/expressions.py`: NO_TRADE, long underlying, long call/put plans and
  conservative option counterfactuals. No broker sizing or order placement.
- `research/attribution.py`: immutable numerical diagnostics; unavailable observations
  are explicit. See [Research protocol](research-protocol.md).
- `research/learning.py`, `selector.py`: decayed, shrunk version/regime statistics;
  prior-state-only transparent ranking and a raw-score comparator.
- `research/reporting.py`, `feedback.py`: generation/regime and selector comparisons;
  externally reconciled execution feedback and independent append-only fill attribution.
- `research/evolution.py`: agent proposals or deterministic threshold hypotheses,
  immutable parent/child versions, frozen paired evaluation and durable decisions.
- `research/agent_market.py`: future input boundary with zero live weight.
- `broker.py`, `options.py`, `supervised.py`, `state.py`, `risk.py`, `accounting.py`,
  `schema.py`, `codex_bridge.py`, `standalone_mcp.py`: preserved execution substrate.
- `legacy/`: v0.1 EMA proposals, signed deployment policy, canary/autonomous wrappers,
  standalone orchestration and old CLI. These are outside the default research path.

## Prediction and information integrity

A Prediction records ID, exact strategy/version, snapshot ID, symbol, UTC decision
time, horizon seconds, direction (-1/0/+1), expected return, coarse confidence,
deeply immutable features/context, distribution metadata and creation/model version.
Initial distributions are explicitly uncalibrated point estimates with historical
bar-volatility metadata, not fitted probabilities.

Version parameters and implementation SHA-256 are immutable. A source change fails
closed for existing registered versions. Strategy code changes need an explicit
version migration; daily learning never rewrites source.

Bars have start/end/availability times; quotes and session references have their own
times. Future, stale, overlapping, unfinished or misaligned bars/quotes fail capture.
The scan clock is system time in the CLI: historical files cannot masquerade as
contemporaneous predictions. Replay and synthetic pools never enter prospective
selector state or promotion evidence. Known future observations block a backdated scan.

Every mutable champion pointer requires an explicit promotion record. Daily state and
lessons are appended as revisions. Former champions and losing experiments remain
queryable. Local operator access is not a tamper-proof remote evidence service.

## Responsibility split

Agents interpret numerical attribution, reason about regimes, propose hypotheses and
choose high-level research priorities. `evolve-weekly --proposal` imports a human/agent
JSON hypothesis and constrained parameters. No LLM service is called automatically.

Quant code owns features, returns, residuals, counterfactuals, score updates, clustering,
calibration diagnostics and evaluation decisions. Narratives cannot replace facts.
Execution code owns risk bounds, durable order identity, broker requests, fills and
reconciliation. There is no LLM-to-order path.

## Persistence and migration

Experience schema version 1 uses `PRAGMA user_version` and `schema_migrations`, with
transactional DDL and FULL synchronous commits. Normalized tables include strategies,
strategy_versions, market_snapshots, predictions, observations, option_observations,
outcomes, selections, trade_plans, shadow_expressions, live_expressions, live_attributions,
counterfactuals, attributions, learning_runs, strategy_scores, regime_scores, lessons,
mutations, challenger_evaluations, promotions, rejections and retirements.

Only strategy enabled/champion metadata is mutable. SQL UPDATE/DELETE and replacement guards protect
the other research records. Foreign keys and timing/identity triggers bind predictions
to snapshots and versions and ensure outcomes follow prediction creation.

v0.1 journals are not opened or migrated by research operations. Start a new research
directory; there is no scientifically defensible conversion of old EMA execution
events into prospective Predictions. Unknown/unversioned experience schemas halt
rather than rewriting data. Execution tables and recovery remain unchanged.

Research commands use an OS process lock and transaction boundaries. Execution keeps
its existing process lock plus fenced lease. Both databases require supported
persistent local Linux storage; external scheduling remains an operator choice.
