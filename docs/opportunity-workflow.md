# Daily opportunity research

The repository Skill `.agents/skills/trade-opportunity-analyst/SKILL.md` completes a
manual daily analysis through the current Codex subscription. No LLM API key or
new service is needed. It uses available Codex search tools and the installed CLI.

```text
$trade-opportunity-analyst 分析今天的交易机会，生成 TradePlan。
```

## Reusable commands

```bash
tradeagent opportunity scan --candidates 3
tradeagent opportunity evidence --template
tradeagent opportunity assess --input data/opportunities/assessments/today.json
tradeagent opportunity decide --refresh --hold-seconds 3600 --delay-seconds 300
tradeagent opportunity show
tradeagent opportunity resolve --input later-market-capture.json
tradeagent opportunity compare
```

The Skill runs these steps itself. Put `--state-dir DIR` before the subcommand to
use independent state. `scan --symbols AAPL MSFT ... --window-minutes 60` changes
the universe/window; the default is 31 liquid equities/ETFs. Robinhood market reads
use the existing external OAuth helper. `--provider alpaca` reuses its configured
credentials and free IEX feed; it is single-exchange data, not consolidated NBBO.
Neither scanner reads account state nor calls broker write tools. There is no
silent provider fallback. After close the last complete session is explicitly
historical/research-only; missing data returns INCOMPLETE or NO_TRADE.

`scan --input FILE` and `decide --input FILE` accept captured timestamped market
evidence: source, evidence_kind, observed_at, session_open/close, interval_seconds,
symbols, Bar dictionaries, actual bid/ask quotes with asof/observed_at, optional
previous-close references, and limitations. Quotes cannot be inferred from bars.
`scan --events FILE` accepts verified symbol/url/claim/published_at/observed_at
events no more than 24 hours old, including candidates without a price anomaly.
Keep synthetic, replay, historical_market and prospective observations distinct.

## Decisions and memory

`evidence --template` provides the exact assessment JSON. Complete thesis, catalyst,
priced_in, contradictions, invalidation, stance (long/watch/avoid), ordinal rank,
actual model identity (or not-exposed), and cited sources. Each source records HTTPS
URL/title/claim, publication time or null, observation time, and role
supporting/contradicting/context. Qualitative direction is not a calibrated return
forecast or sizing instruction. Unknown publication time does not pass the dated
support gate. Assessments are frozen per scan/symbol; a revision needs a new scan.

`decide` evaluates all three immutable Alpha hypotheses on each shortlisted
candidate, without choosing the simple regime rule in advance. The prior-only
EconomicPlan gate needs 20 active daily clusters of the same symbol, strategy
version, source, horizon, entry delay, decision offset and evidence pool, with a positive stress-cost
lower estimate. Sparse evidence yields NO_TRADE. The lower estimate is descriptive,
not a calibrated confidence claim. Supported frozen total horizons are 30, 35,
60, 65, 120 and 125 minutes (delay plus holding).

Quant Only ranks eligible measured economics; Quant + Codex filters them using
supporting research published within 24 hours and the analyst's rank. Codex Only records a directional
research hypothesis independently, with an executable NO_TRADE until quantitatively
supported. All three arms retain the same candidate pool, decision time, price
observations, planned delay/horizon and per-candidate cost model. Cash scores zero.
This distinguishes research preference from authorization to take risk.

The existing Experience SQLite database gains append-only daily scan, assessment,
decision, outcome and execution tables. Local ignored `data/opportunities/` holds
the database, captured evidence, `latest-candidates.json`, `latest-plan.json` and
immutable `decisions/<id>.json`. Latest files are views; SQLite history is frozen.
The decision records all scanned/rejected candidates, forecasts/versions, sources,
economic reasons, entry cap/window, invalidation and exit logic. No SHADOW state is
changed. Keep this state on local Linux storage.

`resolve` requires subsequently observed, contiguous completed bars from the same
source. Prospective decisions may resolve from a later after-close historical-market
capture of the same actual provider; the decision stays in its original prospective
pool and records the later observation kind. Synthetic/replay sources cannot resolve
prospective decisions. Entry uses the first minute boundary inside the planned window;
exit uses the next completed endpoint close. Every candidate and SPY must have
those exact endpoints and a synchronized contiguous path. It freezes raw/net/stress returns, adverse
excursion and exact prices/times. These are modeled outcomes, not broker fills.
Missing paths stay pending. `compare` separates pools and reports paired mean net
differences against Quant Only; small correlated samples establish no AI uplift.
Only strictly prior resolved outcomes can enter later economic evidence.

## Explicit owner handoff

Decision generation does not submit transactions. In the planned window, the owner
can request read-only validation or explicitly execute an eligible proposal:

```bash
tradeagent opportunity handoff --decision-id ID --config /path/to/private.toml
tradeagent opportunity execute --decision-id ID --config /path/to/private.toml --live
```

The default selection is Quant + Codex; `--mode quant_only` allows operation without
AI. Codex Only never authorizes execution. The existing `validated_plan_entry`
boundary sizes from owner dollar/quantity settings and revalidates fresh quotes,
account/risk constraints and the original price cap. A five-minute delayed signal
can remain valid while its initial two-minute quote expires: execution needs a real
new quote within its own freshness window. No stale timestamp is extended.

Explicit execution calls the established one-shot engine, preserving durable order
identity, ownership, fills, recovery and reconciliation. The plan's exit endpoint
is persisted and reused on restart; a horizon exceeding the regular-session exit
buffer is rejected before placement. The owner's commissioning hold/configuration
is unchanged. Unsupported options remain research-only. No timers are installed.

`feedback --decision-id ID --input existing-execution-report.json` links a previously
broker-confirmed, cash-reconciled, flat LIVE report carrying this prediction identity.
Use `--mode quant_only` for an execution of that arm. `compare` reports imported
actual PnL/notional/fees separately from modeled returns.
It stores the original report as owner-imported evidence, separate from modeled
returns; it makes no fresh broker query and submits nothing.
