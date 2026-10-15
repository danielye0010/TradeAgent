# Event-driven SEC / Codex SHADOW v6

Research question: does source-grounded Codex interpretation of corporate
disclosures improve cost-adjusted performance over the same delayed deterministic
event/price baseline? This is an exploratory unattended SHADOW experiment, with no
broker writes, option/short exposure, leverage, account changes or profitability claim.

## Public event discovery

Official [SEC submissions APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
and original `www.sec.gov/Archives/edgar/data/...` documents are the only event
sources. The initial issuer pool is the 21 US operating companies in the already
measured 31-symbol equity universe; the ten ETFs are excluded. No coverage expansion.
The official company-ticker file maps symbols to CIKs. Accession plus CIK is the
immutable event identity; the initial recent-filings snapshot is not a live-event sample.

Relevant forms: 8-K, 10-Q and 10-K, with amendments separately recorded/rejected.
Item 2.02 earnings has highest priority, followed by material agreements,
acquisitions, debt, restructuring, nonreliance, periodic financial reports, then
management, regulation FD and other material/listing/charter events. Other 8-Ks are
retained as nonmaterial. Candidates are not forced to a fixed number. All relevant
metadata survives, including rejected, historical, unavailable and budget-skipped events.

The poll schedule is weekdays 06:02–21:47 America/New_York, every 15 minutes,
covering SEC's [06:00–22:00 weekday filing window](https://www.sec.gov/submit-filings/filer-support-resources/how-do-i-guides/understand-edgar-its-three-websites).
Holiday polls may return unchanged cached data; actual market eligibility always
uses the pinned XNYS calendar. Source requests are sequential with at least 0.75s
between receipts and the next request, conditional ETag/Last-Modified caching,
bounded bodies, 15s network deadlines, and a 90s collection budget. HTTP 403/429
stop the current cycle and impose a one-hour request cooldown; there is no access
workaround. Network errors, missing documents and budget deferrals remain explicit.

A configurable genuine-contact User-Agent is required under SEC's
[fair-access policy](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).
Keep it in a private local text file, not Git. No API key is used. A filing event
retains acceptance time if timezone-qualified, filing date separately, first actual
discovery, original URL, items, retrieval receipts, content hashes and bounded text.
The primary filing and at most one linked 99.1 exhibit are extracted as inert HTML
text, at most 24,000 characters each. Source bytes are retained by SHA-256.

Only events accepted on/after activation, actually discovered thereafter and
successfully retrieved qualify as prospective. Unknown publication timing and
pre-installation filings never become prospective, even when read successfully.
Acceptance time is not assumed identical to public dissemination; detection latency
is explicitly acceptance-to-discovery, and the first actual discovery is the
conservative information anchor for price reaction. Filing date is never an exact clock.

## Event-triggered subscription AI

The existing safeguarded `assessor.run` executes `codex exec` with the supplied
SEC text and available point-in-time market context. It requires existing ChatGPT
authentication, rejects API-configured environments, disables web/search/shell/apps/
plugins and other tools, uses a read-only ephemeral directory, a structured JSON
schema, a 180s process-group timeout and bounded output. No paid API or new account.

There is one assessor process, at most three claimed invocations per New York
calendar day, and a 30-minute per-symbol cooldown. All skipped events are recorded.
A durable per-event claim is written before invoking Codex. Interrupted/failed
claims are terminal UNAVAILABLE and never automatically retried as fresh events.
Historical connectivity checks do not invoke the model. No material price move
is needed for a genuinely new event to reach analysis.

The response contains event classification, source-supported facts with original
URLs and exact source quotes, bullish/bearish/neutral interpretation, supported
surprise versus inference/unknown, short-term/multi-day relevance, uncertainty,
contradictions, LONG/WATCH/AVOID and priced-in risk. Source quotes are checked
against the supplied original text; that is lexical grounding, not an independent
financial audit. Consensus is UNAVAILABLE. The model is never asked to manufacture
expected returns, numerical probabilities or earnings surprise versus consensus.
Filing text is always untrusted data and cannot authorize actions or tools.

## Three arms and actual entry timing

- **Event Quant Fast:** freeze the deterministic event/price decision without waiting
  for AI. In regular hours it gets the first declared eligible entry slot.
- **Event Quant Delayed:** rerun the identical deterministic rule using a fresh
  capture after the Codex completion/failure/skip receipt.
- **Event Quant + Codex:** LONG only when that same delayed Quant decision is LONG
  and a successfully parsed bullish Codex assessment recommends LONG; otherwise
  CASH when a valid assessment and market evidence support abstention. Failed AI
  stays UNAVAILABLE, even if a CASH result would look favorable.

Only LONG/CASH are executable-style hypothetical choices. Bearish interpretations
are diagnostic direction labels versus later observed underlying movement, never
short/Put P&L. Quant receives completed bars, actual bid/ask, SPY-relative movement,
volume, volatility, spread, prior close and session context. Its reaction anchor is
the last completed price before first discovery, or the genuinely available prior
regular close for premarket/after-hours events. Missing anchors or fresh data remain
UNAVAILABLE. Source acceptance and first-discovery clocks are both retained.

The frozen long rule requires positive own and SPY-relative reaction, spread <=0.5%,
trailing completed mean dollar volume >=$1m/min, and an **UNCALIBRATED** point estimate
clip(0.5 times relative reaction, -3%, +3%) exceeding observed spread plus 10bp
round-trip slippage plus a 10bp buffer. This is a research screen, not calibrated EV.
No large-move threshold gates material-event AI analysis.

Entries use the first minute `:45` slot strictly after evidence/analysis availability,
starting 09:46 ET, ending with at least 60s before regular close. Minutes divisible
by five are reserved for v5 discovery. Each quote's two venue-side timestamps and
receipt must fall in [target,target+60s), and quote age must be <=15s. Actual ask,
receipt and freeze time persist. This is a bounded observation-window model, not a fill.
After-hours disclosures may be analyzed immediately; Fast/Delayed/AI then share the
next eligible regular slot if AI finished before it. A late or restarted worker
records missed entry observations instead of backfilling or silently moving the slot.

AI and Delayed share the same immutable entry capture and current liquidity decision.
Fast-versus-Delayed isolates waiting cost on matched events. The equal-weight event
basket freezes eligible issuer names from the same discovery-poll cohort at Fast
entry and retains observed sides at each arm's actual window; SPY and CASH are
separate references. Missing basket constituents are not renormalized away.

## Forward multi-session outcomes and economics

Primary exit: close of the first trading session **after the actual entry session**.
Secondary exits: closes of the third and fifth subsequent sessions. Each endpoint
observes the final 60 seconds before XNYS close, including early closes and holidays.
Quotes are collected forward, with actual times and holding duration; missed/stale
quotes remain UNAVAILABLE. Overnight gaps are included by the actual entry/exit sides.
All costs are frozen: entry ask/exit bid, observed spread, 5bp slippage per side and
assumed zero fees. Independent overlapping hypotheses never become a fictional account.

Corporate-action safeguard: preserve the provider's raw and adjusted previous close
for every intervening trading session. A missing forward reference, mismatch against
the expected prior session, detected adjustment or relevant action disclosure flags
the raw price-return diagnostic and excludes it from qualified net comparisons.
No split ratio, changed share count or missing observation is inferred. The check
means **no provider adjustment observed**, not an independent corporate-action audit.
Cash distributions/total-return reinvestment are not modeled. Provider errors and
unverified metadata remain limitations rather than silently adjusted profits.

Primary statistic: AI-minus-Delayed net-return difference on the same eligible
event/entry capture and qualified outcome. Average within entry trading sessions,
then report across-session mean and standard error (unavailable with <2 sessions).
Report Delayed-minus-Fast separately, and each arm versus its matched event basket,
SPY and CASH with its own explicit sample size. Multiple filings are not independent
trading days. Missing AI never votes as CASH; unmatched, negative, stale and rejected
events remain visible. Small prospective samples do not establish a profitable strategy.

## Persistence, independent scheduling and deployment

The independent local Linux SQLite `experience.sqlite3` contains append-only
`event_records`, with mutable HTTP cache metadata separate from immutable evidence.
Source bodies and normalized captures live in content-addressed `evidence/`.
Independent named locks cover polling, assessment, market capture and reporting;
slow Codex cannot hold the market worker's lock. Claims and observations survive restart.

`scripts/install_events.py` prepares four new service/timer pairs: poll, assess,
market and report. It never writes an existing unit. They yield to v1/v2 oneshots
(including activating), market work orders after v5 and reserves v5 discovery minutes;
bounded collection also stops before the next v5 discovery boundary. The installed
external v5 `priority.conf` and `capture_priority.py` must remain intact.

Before enabling, install a merged wheel in its own environment, prepare/verify units,
then run an authenticated live SEC cycle with `--verify-historical`. A historical
document can prove original-content access but does not enter the prospective cohort.
Poll enablement requires complete 21-issuer live coverage plus an original document
receipt at the current protocol version. AI enablement separately requires ChatGPT
CLI authentication. A blocked new capability leaves only its affected timer disabled.

```bash
python -m tradeagent.prospective.events poll --state-dir /abs/event-driven-v6 \
  --sec-user-agent-file /private/sec-user-agent.txt --verify-historical > /private/readiness.json
python scripts/install_events.py --python /abs/v6-venv/bin/python \
  --state-dir /abs/event-driven-v6 --sec-user-agent-file /private/sec-user-agent.txt \
  --v5-state-dir /abs/continuous-quant-v5 --readiness /private/readiness.json --enable
```

Every report tick writes `daily-status.json`, readable `daily-report.md`, dated
`reports/YYYY-MM-DD.md`, and a separate saved-evidence v5 supplement. Regenerate
without collection or AI:

```bash
python -m tradeagent.prospective.events report --state-dir /abs/event-driven-v6 \
  --v5-state-dir /abs/continuous-quant-v5
```

## Read-only v5 supplement

`v5_supplement.py` opens saved v5 SQLite with `mode=ro` and `query_only=ON`. It calls
the unchanged original summarizer, then reports independent pairwise 1-vs-3/5/10
delay matches, per-delay matching versus SPY/full frozen basket, and completeness
by delay and morning/midday/afternoon with explicit n. These cohorts may differ and
are supplementary. It does not change v5's configuration/version, original four-delay
complete-case primary comparison, event journal, timer or installed environment.
