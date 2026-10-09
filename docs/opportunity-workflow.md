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

`evidence --template` provides the assessment JSON. Complete thesis, catalyst
(or explain that none is identified), priced_in, contradictions, invalidation,
stance (long supports, avoid challenges, watch abstains), ordinal rank and actual
model identity (or not-exposed). The optional `hypothesis_type` is `price_action`
or `event_driven`; old assessments default to price action unless the scanner
candidate is event-only. Freeze research before viewing quant economic eligibility.
No subjective confidence, expected-return or sizing field is accepted.

News is optional for price/volume/relative-strength hypotheses. Codex can support
them with the saved timestamped market evidence and empty `sources`. Relevant news
may support or contradict the interpretation. Sources still retain HTTPS URL,
title/claim, publication time or null, actual observation time and role. Only explicit
event-driven claims require a supporting publication within 24 hours or the scanner's
fresh verified event. Undated/old news is context, not a new catalyst.

`decide` evaluates the three immutable price-based Alpha hypotheses on each shortlist.
Quant Only ranks eligible measured economics independently of assessments. Codex Only
records independently frozen directional hypotheses, research-only with executable
NO_TRADE; it does not inherit the quant plan or calibrated return. Quant + Codex can
choose among economically eligible candidates supported by Codex, including market-only
opportunities. Challenge/watch/missing assessments abstain in the combined arm.
Each arm shares the same candidate pool, timestamp, outcome price model, horizon and
cost assumptions. Performance reports partition paired comparisons by the frozen
Skill/evidence-policy versions. Mixed-protocol aggregates are marked descriptive;
they are not a single prospective treatment estimate.

## Comparable economic evidence

Daily opportunity plans use frozen policy `opportunity-cohorts-v1`; the legacy exact
policy and frozen Alpha experiments are unchanged. The cohort design is deliberately
specified before outcomes, with no best-performing grouping search:

- Keep exact strategy/version, source, total horizon, entry delay and evidence pool.
  Different feeds and holding periods are not interchangeable without new evidence.
- Replace exact decision minute with a fixed 30-minute band. Match the original
  trend/range regime, opening realized-volatility band (<0.3%, 0.3–1%, >=1%), and
  observed decision-spread band (<10, 10–25, >=25 bp).
- Pool known stocks within fixed sectors, broad equity ETFs, and sector equity ETFs
  only in their separate predefined groups. Bond/commodity ETFs and unclassified stocks retain
  their own symbol group. This assumes conditional comparability, not universal alpha.
- Use only outcomes resolved strictly before this decision from the last 180 calendar
  days. Cohort features come from the original frozen prediction and quote evidence,
  never future realized volatility, current symbol status or AI confidence.
- Average repeated/overlapping signals within symbol-day, then give each symbol an
  equal share of ONE daily cohort vote. Twenty signals or symbols on one date are
  still one day. All active losses are retained. Require >=20 cohort days and >=5
  target-symbol days, with both represented within the existing 30-day freshness limit.

Uncertainty uses the larger ordinary and three-lag Bartlett/Newey–West standard
error of ordered active-day means. Use the conservative t(4) 97.5% critical value
2.776 for both cohort and target estimates. Discount the cohort lower estimate by
between-symbol mean dispersion; take the LESSER of that estimate and the target's
own lower estimate. Apply the unchanged current spread/slippage/fee stress costs,
and require the resulting net lower estimate to be strictly positive. Peer winners
cannot override an unsupported or losing target. There is no return rescaling across
horizons and no reduction of the 20-day cohort or positive stress-net requirements.

The five-day target gate is a local transport check, not proof of a symbol-specific
edge. Peer dispersion, sparse/serially dependent data and adaptive research can still
invalidate comparability. These are conservative research screens, not calibrated
confidence coverage or a profitability claim. Clustering follows the dependence
concern in [Cameron and Miller](https://escholarship.org/uc/item/1jq5d0pq);
the serial-dependence adjustment follows [Newey and West](https://www.nber.org/papers/t0055).
The cohort boundaries, local gate and dispersion discount are explicit project choices.

Evidence remains insufficient when comparability metadata is missing, fewer than 20
cohort or five target days exist, target support is stale, target/peer uncertainty or
dispersion erases the stress-net edge, or a prospective pool has only historical or
synthetic samples. A price anomaly and an enthusiastic assessment do not change this.
Supported total horizons remain 30/35/60/65/120/125 minutes (delay plus holding).

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
