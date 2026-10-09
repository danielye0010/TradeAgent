# Daily opportunity research

The repository Skill `.agents/skills/trade-opportunity-analyst/SKILL.md` completes a
manual daily analysis through the current Codex subscription. No LLM API key or
new service is needed. It uses available Codex search tools and the checkout CLI.
The default invocation is read-only RESEARCH; an explicit owner LIVE request also
completes the production entry/exit lifecycle in that same invocation.

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
silent provider fallback. Read-only Robinhood compatibility is checked against the
frozen input/output schemas and safety annotations of both market tools; a server
version change alone does not reject identical contracts. Changed schemas still halt,
and broker write contracts and authorization remain separate and unchanged.
A failed scan returns `INCOMPLETE / NO_TRADE` with exit code 2 and the original provider
error. Its scan ID also identifies a stable saved failure under `invocations/`; it
creates no executable decision ID or TradePlan. The Skill stops before assessment or
decision. Retrying evidence/decide/show against that scan returns the same failure,
including for older failed scans without source metadata. After close the last
complete session is explicitly historical/research-only; missing data returns
INCOMPLETE or NO_TRADE.

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

`decide` freezes the three immutable price-based Alpha hypotheses for EVERY symbol
in the configured scanner universe with a valid contemporaneous snapshot. The full
`quantitative_observations` collection is separate from the at-most-three
`ranked_candidates` used for the paired Quant/Codex comparison. Scanner rejection,
missing Codex assessment and economic NO_TRADE do not remove quantitative coverage.
SPY uses a predefined genuine QQQ benchmark because the immutable snapshot contract
forbids self-benchmarking; every other symbol retains SPY. Benchmark identity is
persisted and must match in comparable evidence, so SPY/QQQ-benchmarked observations
cannot contaminate ordinary SPY-benchmarked estimates. No benchmark is fabricated.
Missing/stale/invalid signal paths remain explicit per-symbol limitations; historical
captures never become prospective forecasts. No prior session is forecast retroactively.

For LIVE, the Skill passes the existing owner `--config` to scan and decide. Scan reads
only the local allowlist and ranks Codex candidates within it; it retains all broad
research observations plus `research_candidates` and `excluded_live_opportunities`.
Decide locally revalidates the frozen allowlist; a change requires a new aligned scan.
No account reads, TOML writes, symbol expansion or order operations occur here. The
existing execution engine independently checks current authorization and risk.
All paired arms use the same frozen shortlist. Performance partitions LIVE allowlists
and the new Skill protocol from older or broader research comparisons.

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

The existing Experience SQLite database retains append-only daily scan, assessment,
decision, outcome and execution tables and adds append-only per-symbol modeled outcomes. Local ignored `data/opportunities/` holds
the database, captured evidence, `latest-candidates.json`, `latest-plan.json` and
immutable `decisions/<id>.json`. Latest files are views; SQLite history is frozen.
The decision records all scanned/rejected candidates, forecasts/versions, sources,
economic reasons, entry cap/window, invalidation and exit logic. No SHADOW state is
changed. Keep this state on local Linux storage.

Scan and decide automatically attempt pending resolution using their actual later
captures. When real provider reads are enabled, they also fetch the original past
sessions required by matured prospective forecasts; each later capture is retained
under `captures/`. Explicit input files cause no implicit network access. An unavailable
provider, missing path or mismatched source stays pending with diagnostics. This is
invocation-driven collection, not a timer: another daily invocation (or explicit
resolution with a genuine later capture) is needed after maturity. Resolution does
not invent bars or relabel retrospective predictions as prospective.

Each symbol resolves independently so one incomplete instrument does not block
usable peer evidence. Original decision/source/receipt times, exact entry/exit prices,
frozen costs and later observation-kind/capture identity remain recorded; paired
comparison outcomes wait for all required shortlist paths. Older decision records
remain immutable and continue through their existing resolution semantics.

Only the first frozen forecast in a predefined symbol/strategy/version/source/pool/
benchmark/horizon/delay/minute slot contributes economic evidence, chosen BEFORE knowing its
outcome. If that first forecast is unresolved, later repeats cannot substitute for
it. Unique prediction IDs deduplicate storage paths, and symbol-day/day clustering
prevents intraday repeats or many peer symbols from inflating active-day counts.
Losses and inactive predictions remain recorded; only prior long-active observations
meeting the existing outcome-blind cohort rules enter profitability estimates.

Each economic plan shows `total_resolved_days`, `resolved_days_by_pool`,
`same_pool_resolved_days`, `matching_cohort_days`, `target_days` and ranked
`exclusion_categories`. Exclusions count the first failed filter per observation,
not independent days. Historical/synthetic evidence can explain coverage but cannot
satisfy a prospective gate. Twenty total research days need not mean twenty matching
active cohort days, and one invocation cannot create twenty independent days.

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

## Explicit owner LIVE invocation

```text
$trade-opportunity-analyst LIVE：分析今天的市场机会，输出购买计划，符合全部条件就通过 Robinhood 真实下单，并完成退出和盈亏记录。
```

This direct owner instruction authorizes execution for this invocation. Development
requests and quoted examples do not. RESEARCH remains read-only. After scan/assessment/
decision the Skill invokes the existing `opportunity execute --decision-id ID --config
/path/to/private.toml --live` itself, using the checkout's `.venv/bin/tradeagent` when
available. A second manually assembled order command is unnecessary. Broker/platform/
tool confirmations remain authoritative. Account selection, sizing and risk settings
come from the existing owner TOML; no secrets or settings are rewritten.

`execute` saves and prints the purchase plan before placement: symbol/direction/type,
selection reason, measured economics/uncertainty and Codex conclusion, owner dollar
budget/share quantity and order type, price estimate/cap, original entry window,
account/risk constraints, planned exit/invalidation and immutable decision ID.
Initial account checks are marked pending. Inside the production engine, the same
`validated_plan_entry` boundary independently refreshes quote/account/risk evidence;
its observer saves and displays the complete validated transaction before review/place.
A failed pre-placement display/persistence blocks entry. Later progress/feedback I/O
failures do not interrupt the engine's position management; they are reported.

The command waits in its current foreground invocation for the original entry time.
It never extends the window: EXPIRED returns without new entry. The final owner send
gate rechecks the frozen window and original quote cap after slow reads. Market orders
preserve configured dollar/fractional sizing; the quote cap is a transaction-time
condition, not a guaranteed market fill price. Limit orders retain the stricter owner-configured or frozen-plan broker cap.

The existing `run_live`, official MCP transport, order identity, journal, entry/exit,
cash/position reconciliation and recovery remain the execution implementation. Hold
the foreground process through its existing planned exit. No detached trading job,
new broker adapter, scheduler or timer is added. Broker approval requirements block
execution; this workflow never disables them. Unsupported options and Codex Only
cannot execute. Quant + Codex is the Skill default with no fallback; the existing
explicit CLI `--mode quant_only` capability remains available separately.

Retries of the same active immutable plan use `recover=True` (no new entry) and
duplicate suppression, including after its entry window expires. A different new
plan may call the existing `new-run` routine, which freshly proves prior closure and
archives the complete old state and receipts. Open/ambiguous exposure prevents this.
Plan identity is checked again under the owner lock to exclude a competing lifecycle.
An already attempted archived decision cannot be replayed. HALTED/RECOVERY_REQUIRED
surfaces `tradeagent status --config CONFIG` and the existing exit-only
`tradeagent recover --config CONFIG`; do not initiate an unrelated entry.

Truthful progress includes PLAN_CREATED, AWAITING_ENTRY, SUBMISSION_UNKNOWN (send
started, acknowledgment uncertain), ORDER_SUBMITTED (broker acknowledged),
PARTIALLY_FILLED, POSITION_OPEN, exit-fill confirmation, CLOSED, NO_TRADE, EXPIRED,
HALTED and RECOVERY_REQUIRED. Acknowledgment does not establish a fill. CLOSED
requires the engine's reconciled terminal status, confirmed flat bot position and
cash proof. Realized PnL stays pending until broker-confirmed flat/cash reconciliation proof;
a separately halted incident can retain proven PnL without being labelled successful. Observed fills/average prices/fees,
actual final positions and broker IDs are retained when observed. A halt can have
zero orders, known residual exposure or unknown submission; do not conflate them.

Append-only execution feedback links the engine report to the original decision and
research arm. Every report is preserved; `compare` uses the latest appended report
per decision/arm so recovery does not double-count PnL. It retains actual execution
status/PnL separately from modeled paired returns. Private Linux artifacts are in `data/opportunities/live/<decision-id>/
<attempt-id>/`: purchase-plan-created.json, purchase-plan-validated.json (when
validated), events.jsonl, execution-report.json (when engine observed), and result.json.
The original immutable TradePlan and owner engine journal/report remain preserved.

The optional `handoff --decision-id ID --config /path/to/private.toml` is still a
read-only account/risk check when explicitly requested. `feedback --decision-id ID
--input existing-execution-report.json` still imports a previously broker-confirmed,
cash-reconciled, flat LIVE report carrying the matching prediction identity; it
does not make a fresh broker query or submit anything.

## Realizable late-session research

Before issuing predictions, decide checks the captured regular-session open/close,
original entry window and rounded modeled entry/exit endpoints. A forecast ending
past close is not created; the decision records NO_TRADE and an unavailable research
window without reducing the chosen frozen horizon. An endpoint exactly at close can
resolve from a genuine completed final bar. Early closes follow the provider's session
bounds; experimental execution also verifies the pinned XNYS calendar.

`tradeagent opportunity resolution-status` performs no market/account reads. It appends
`UNRESOLVABLE_SESSION_WINDOW` evidence for old impossible forecasts, preserving their
original decisions, files and predictions. New late requests are marked
`NO_FORECAST_SESSION_WINDOW`. These records contribute no economic rows, paired
performance or pending-session requests. Genuine missing synchronized data remains
pending. Classification also runs during normal manual resolution/scan invocations.

## Owner-controlled execution universe

The default 31-symbol research universe remains unchanged. The larger LIVE universe
already comes from the existing owner TOML `[live].symbols` array, not a code constant.
Only the owner may explicitly approve and edit that array. Include only desired
supported equities/ETFs also in the research universe; adding an allowed symbol does
not by itself add research coverage outside those 31. Keep `[entry].dollar_amount = "5"`
and all current risk settings. If an existing `[entry].symbol` or `preferred_symbols`
list is present, it must remain a valid subset of the owner allowlist. The plan handoff
uses the selected allowed symbol and the unchanged owner size.

After an approved allowlist edit, start a new scan with `--config CONFIG` and pass
that same config to decide/execute. Old frozen scans cannot silently adopt new
permissions. The Skill ranks its Codex shortlist only inside the allowlist and retains
all research coverage and exclusions; it never edits private settings itself.

## Explicit experimental policy

Ordinary LIVE retains `evidence-gated-v1` and the unchanged `opportunity-cohorts-v1`
net-edge gates. Experimental LIVE is a different, explicitly requested policy,
`experimental-live-v1`. It can explore unvalidated signals without claiming edge.

It selects positive long signals from the three frozen Alpha families across the
current authorized research universe, with deterministic raw quantitative signal
ranking and symbol/family tie breaks. It never ranks by AI confidence, realized
future performance or scanner anomaly score. The ordinary Quant Only, Codex Only
and Quant + Codex arms remain independent Evidence-Gated comparisons; they do not
become experimental orders. The Codex shortlist stays at most three.

An experimental plan must retain prospective signal provenance, exact frozen Alpha
version/horizon, a valid XNYS session and complete exit endpoint, fresh executable
quotes, and its original cap/window. It uses the SAME production plan-entry, broker
authorization, risk/notional/spread/exposure/ownership/duplicate, reconciliation and
exit/recovery boundaries. It requires the owner's existing $5 dollar sizing and
cannot create a new position when the quantitative hypotheses abstain or are bearish.
No paid API, new broker implementation, timer or SHADOW policy is added.

A durable exclusive daily reservation beside the existing owner lifecycle directory
(`STATE_DIR.experimental-days/YYYY-MM-DD.json`, America/New_York dates) admits at most
one new experimental entry attempt. It survives whole-lifecycle archival and is shared
by all research directories using that owner state. Failed/unknown attempts retain
the reservation; this is deliberately stricter than one filled position. Receipts
are never automatically deleted or released. Existing same-lifecycle recovery makes
no new entry and does not consume another allowance. A new day does not override
unresolved exposure or any existing broker protection. Keep the same established
owner state directory; creating a new owner state is not a daily-limit reset workflow.

Decisions, purchase plans and actual execution feedback carry their policy identity.
`compare` separates experimental modeled outcomes and actual performance by policy;
ordinary paired aggregates exclude experimental decisions. Modeled quantitative
observations from either invocation remain eligible only under the original outcome-blind
research sampling rules; actual experimental fills never turn into economic eligibility
or an automatic promotion. There is no established profitable Alpha in either policy.

Manual modes:

```text
$trade-opportunity-analyst 分析今天的交易机会，生成 TradePlan。
$trade-opportunity-analyst LIVE：分析今天的市场机会，输出购买计划，符合全部条件就通过 Robinhood 真实下单，并完成退出和盈亏记录。
$trade-opportunity-analyst EXPERIMENTAL LIVE：使用预定义量化假设分析今天的市场机会；仅在信号、行情、时段和全部执行保护通过时，以现有 $5 配置最多新开一个实验仓位，并完成退出和独立盈亏记录。不宣称盈利能力已验证。
```

The Skill supplies `decide --policy experimental --config CONFIG` and
`execute --mode experimental --live --config CONFIG` only for the third request.
Plain LIVE keeps the existing CLI defaults. Quoted examples and development requests
are not operational authorization. See [the separate v2 experiment](opportunity-research-v2.md)
for future research; it does not replace Evidence-Gated LIVE.
