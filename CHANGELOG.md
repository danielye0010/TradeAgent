# Changelog

## 1.7.3 — 2026-10-26

Python distribution version: `1.7.3`.

- Simpler public pages. Weekly: headline with the next-pick countdown, a leaderboard
  (value, return, holdings), the value chart and this week's GPT and Claude picks with
  one-line reasons. Daily: headline with the track record, results chart and table,
  latest picks and the pick log. Statistical, pipeline, comparison and data-quality
  panels are removed from the public view; all data stays in weekly.json/data.json.
- Header tabs read "Weekly portfolios" and "Daily picks".
- Display only: research protocols, frozen identities and schedules are unchanged.

## 1.7.2 — 2026-10-26

Python distribution version: `1.7.2`.

- Redesign the weekly portfolios page (public site, dashboard `/weekly` and
  `tradeagent weekly serve`): next-decision clock and weekly timeline, GPT portfolio
  value with sparkline, weeks-ahead-of-random gauge, on-the-record counters, a
  five-portfolio board with settlement toggle, value chart with hover, six-step
  pipeline with the next step marked, filterable decision log (buy/hold/sell/cash/
  missed) with each model's reasons and sources, positions, weekly returns,
  head-to-head, data quality and reading notes. Light and dark themes; fits phones.
- Full English and Chinese for the weekly page, the site header tabs and page title,
  following the site's language switch; engine reasons are shown in the reader's
  language. Smoother Chinese copy on the intraday page.
- Display only: weekly protocol code, frozen identities, schedules and data are unchanged.

## 1.7.1 — 2026-10-26

Python distribution version: `1.7.1`.

- Unify public weekly portfolios and intraday research under one homepage with
  accessible horizon tabs. Legacy weekly URLs return to the weekly tab.
- Both publishing jobs use the same complete-site builder and a shared publish lock.
  Research runtimes, frozen protocol identities, accounts and schedules are unchanged.

## 1.7.0 — 2026-10-26

Python distribution version: `1.7.0`.

- Add isolated weekly-hold-v1 SHADOW research: five independent virtual accounts,
  frozen native-search model decisions, cross-week append-only lot/cash ledgers,
  T+1 funding restrictions, settled-only sensitivity and mechanical risk exits.
- Add historical quant/random/SPY replay with data-quality gates and explicit
  daily-price/survivorship limitations; historical and prospective evidence stay separate.
- Add a read-only weekly holdings dashboard and complete public weekly results page.
- Existing shortline protocols, schedules, frozen observations and LIVE settings are unchanged.

## 1.6.0 — 2026-10-20

Python distribution version: `1.6.0`.

- Public page redesigned as an institutional track record: a factsheet panel (cumulative
  net return, excess over SPY and a random pick, days ahead of SPY, hit rate), a
  performance table for every arm including the quant engine alone, the six-stage
  pipeline from universe to exit, "Same AI. Inside a quant desk." comparing TradeAgent
  with asking an LLM directly, the latest decision memo with thesis, catalyst and
  sources, and a full trade log. All times are New York (ET).
- The published snapshot includes the research design it describes (universe, shortlist
  size and modeled costs).
- Install from PyPI: `pipx install tradeagent`.

## 1.5.2 — 2026-10-19

Python distribution version: `1.5.2`.

- Public page rewritten as a launch page: "The AI trading agent that has to prove it",
  the agent against SPY and a random pick up front, the live arena with GPT vs Claude,
  and "Not another AI arena": how TradeAgent compares with AI trading arenas, signal
  bots and asking a chatbot.

## 1.5.1 — 2026-10-19

Python distribution version: `1.5.1`.

- The public page and the READMEs lead with the agent and its public track record: the
  agent against SPY and a random pick from the same shortlist, a "why you can trust the
  record" section, and GPT vs Claude as the choice of which AI runs your agent.
- Publishing refuses a Claude record that follows a different `daily-news-v2` run, and a
  session whose shortlist includes SPY reports SPY's own move.

## 1.5.0 — 2026-10-19

Python distribution version: `1.5.0`.

- `tradeagent publish`: a public results page built from the paper research. It shows
  each day's frozen GPT and Claude picks with results after costs, SPY over the same
  hour, a running chart, the latest reasoning with sources, a head-to-head scoreboard and
  every pick. `publish install --remote URL --enable` rebuilds it and pushes it to the
  repository's `gh-pages` branch every weekday at 17:20 New York time. The live account
  is never read.
- READMEs link the live results page and describe each dashboard page.
- The dashboard's session outcomes include SPY's move over the same window.

## 1.4.0 — 2026-10-19

Python distribution version: `1.4.0`.

- Claude can now answer the daily research question next to Codex. The new
  `daily-news-claude-v1` service follows `daily-news-v2`: it takes the shortlist v2
  froze, asks Claude (Claude Code CLI, `claude -p`, on the owner's Claude subscription;
  never an API key) the same prompt with the same schema and deadline, with web search
  only for the news arm, and scores Claude's picks over the v2 decision's own entry,
  exit and costs. Answers frozen after the v2 entry time, failed calls and late starts
  are recorded as unavailable, never as cash; the v2 records are only read.
- `tradeagent claude doctor [--probe]`, `tradeagent claude install --enable` and
  `tradeagent claude uninstall` set it up as a systemd timer next to v2.
- `tradeagent autotrade install --model claude` trades Claude's news pick instead of
  Codex's. Codex stays the default, and existing installs are unchanged.
- Dashboard: a **GPT vs Claude** page with each model's added-up net return, wins,
  agreement, a verdict on the paired difference after 20 sessions, a chart and every
  session with both picks; the session view shows both models' reasoning side by side.
  The Account page names whichever model autotrade follows.
- `tradeagent doctor` lists Claude Code as an optional check.
- Each concurrent Claude answer keeps its own completion time, so a slow price-only
  answer cannot make a finished news answer late; sessions with a terminal v2
  resolution failure are no longer listed as pending.
- READMEs rewritten around what a user does and sees: a trading day at a glance,
  three-step setup, automatic trading, and adding Claude.

## 1.3.0 — 2026-10-19

Python distribution version: `1.3.0`.

- Dashboard redesigned around the account, in the style of a retail trading app. The new
  **Account** home shows the live account value, profit in dollars and percent, a value
  chart you can scrub (1W / 1M / 3M / All), recent trades with buy and sell prices, and a
  card for today's trade (waiting, picked, holding with the sell time, result, or the
  plain reason nothing was bought). The **Trades** page lists every trade with total
  profit and win rate; a trade opens to its prices, times and timeline.
- Gain and loss colors switch between green up and red up (red up by default in
  Chinese). Cleaner white and black themes.
- Research charts show cumulative net return per group from the second scored session.
- The dashboard reads the account value each live run started from, the entry fill of a
  run still in progress, and the autotrade timer's daily outcomes. Still read-only.

## 1.2.0 — 2026-10-16

Python distribution version: `1.2.0`.

- `tradeagent autotrade` now copies the daily news-Codex research pick instead of
  trading its own quant signal. Each weekday at 10:30 New York time it waits for the
  frozen `daily-news-v2` decision, takes the `codex_news` arm's selection and, when it is
  a long position in an allowed symbol, buys the owner's dollar amount ($1–$5) at the
  decision's entry minute and sells one hour after the fill. Cash, an unavailable
  assessment or a symbol outside `[live] symbols` is recorded as `NO_TRADE`; nothing is
  re-decided. One entry per New York day, exit-only recovery first, KILL respected; each
  day's outcome is saved with the research decision id. `install` finds the installed
  `daily-news-v2` timer (or takes `--follow-dir`), requires `hold_seconds = 3600`, and
  lists research symbols missing from the allowlist. The `--at` option is removed.
- The dashboard labels the timer "Daily live mirror".

## 1.1.0 — 2026-10-16

Python distribution version: `1.1.0`.

- `tradeagent autotrade`: one small LIVE trade each trading day with nothing to run by
  hand. `autotrade install --enable` adds a weekday systemd timer (10:15 New York time by
  default; a missed morning is skipped, never traded late). Each run finishes any
  interrupted lifecycle first (exit only), scans, freezes an experimental decision and
  buys only when a predefined quant long signal is eligible; otherwise the day is
  `NO_TRADE`. The existing engine still owns sizing, every risk limit, KILL, the timed
  exit and reconciliation. `install` reports the account value the risk fractions need
  for the configured amount.
- Experimental LIVE accepts any owner dollar amount from $1 to $5 (previously exactly $5).
- The dashboard shows the autotrade timer as "Daily live trade".

## 1.0.0 — 2026-10-16

Python distribution version: `1.0.0`. First stable release.

- `tradeagent dashboard`: a local web dashboard. Today's session as a
  timeline with the scheduled steps, every frozen AI decision with Codex's thesis,
  catalyst, invalidation and sources, per-arm results against SPY and Random (verdicts
  only after 20 paired sessions), the strategy lab's weights, and live/paper trades
  with fills, P&L and the execution timeline. Finds installed timers and the live run
  automatically; English and Simplified Chinese; light and dark. Loopback only, GET
  only, Host-checked, strict CSP, SQLite opened read-only; never reads tokens.
- READMEs rewritten: a TL;DR up top, dashboard screenshots, about a third shorter;
  details moved to `docs/` (new `docs/dashboard.md`). The Chinese README and dashboard
  copy are written natively rather than translated.

## 1.0.0-beta.9 — 2026-10-15

Python distribution version: `1.0.0b9`. First release published to PyPI
(`pipx install tradeagent`).

- Robinhood MCP updates are checked for compatibility instead of an exact version and
  schema pin. Additive changes (new optional inputs, new response fields, new tools)
  keep trading working and are reported by `live-check`; incompatible changes to equity
  or account tools fail closed with the tool and reason; a broken option tool disables
  only option features. Both real past upgrades replay as compatible. Legacy
  Codex/canary paths keep the strict pin.
- `tradeagent doctor`: offline setup checklist with the next command to run.
- `tradeagent login` / `logout`: browser sign-in to the official Robinhood Trading MCP
  (standard OAuth discovery, dynamic client registration and PKCE). The access and
  refresh tokens are stored owner-only; the engine still obtains tokens only through the external
  helper, which `login` installs at the existing default path.
- `login` creates `~/.config/tradeagent/tradeagent.toml`; LIVE commands use it when
  `--config` is omitted. `state_dir` and `oauth_helper` now have per-user defaults.
- `tradeagent` with no arguments prints grouped help. `demo`, `run-once --paper` and
  `live-check` add a short human summary on interactive terminals; JSON on stdout is
  unchanged. `live-check` names the missing sign-in instead of a generic failure.
- Tests isolate HOME/XDG directories so they never see a developer's real config.
- README rewritten for new users, with a Simplified Chinese version (`README.zh-CN.md`)
  linked from the top of each: comparison with existing tools, two-minute tour,
  safety guarantees and honest results status.

## 1.0.0-beta.8 — 2026-10-15

Python distribution version: `1.0.0b8`.

- Add isolated event-driven v6: official SEC submissions/original disclosures,
  declared fair access, deduplicated prospective events and budgeted subscription
  Codex interpretation. Historical connectivity never becomes a live opportunity.
- Independent Fast, Delayed and Quant+Codex arms share the appropriate fresh
  quotes, with 1/3/5 subsequent-session close observations and corporate-action
  evidence checks. Match incremental comparisons and cluster by entry session.
- Add four independent SHADOW workers/timers and a read-only v5 supplement with
  pairwise delays, separate benchmark cohorts and completeness by session period.
  Preserve existing protocols, installed environments, timers and v5 priority guard.

## 1.0.0-beta.7 — 2026-10-15

Python distribution version: `1.0.0b7`.

- Add isolated continuous Quant SHADOW v5, five-minute rolling discovery and real
  forward bid/ask observations at 1/3/5/10-minute delays and a common signal+60m exit.
- Freeze signal transitions, liquidity, uncalibrated forecasts, costs and the eligible
  basket. Preserve missing and stale signals; compare matched delays by session clusters.
- Independent SQLite/captures and systemd units; no LLM calls or broker writes.
  Existing v1/v2 captures take priority and v3/v4 activation remains unchanged.

## 1.0.0-beta.6 — 2026-10-15

Python distribution version: `1.0.0b6`.

- Add isolated `daily-call-put-v4`: signed Quant and independent Codex Price/News
  choices use long Calls/Puts, genuine entry ask/exit bid, whole-contract allocation,
  idle cash and explicit modeled fees. Equity, SPY and CASH remain separate controls.
- Existing minutely SHADOW pipeline captures endpoints and generates daily reports.
  The 11:00 ET capture avoids v1/v2 Codex windows and checks their service activity.
- Persist option-data HTTP 403 as an access blocker and suppress automatic retries.
  Timer enablement requires successful genuine prospective Call and Put cycles;
  prepare-only installs a verified disabled timer. V1/v2/v3 history remains frozen.

## 1.0.0-beta.5 — 2026-10-14

Python distribution version: `1.0.0b5`.

- Existing daily SHADOW ticks generate `daily-report.md` with frozen P&L, quote/cost
  details, candidate coverage, and separately labeled exploratory basket, ranking,
  disagreement and news comparisons. Missing evidence remains explicit.
- `python -m tradeagent.prospective.daily --report-only --state-dir PATH --protocol NAME`
  regenerates the derived report without collection, inference or a decision tick.
- Preserve v1/v2 selection rules, frozen observations, windows and costs; v3 remains
  dependent on genuine option quotes and explicit operator enablement.

## 1.0.0-beta.4 — 2026-10-13

Python distribution version: `1.0.0b4`. Supersedes the unpublished beta.3.

- New `daily-news-v2` SHADOW experiment: at 10:30 ET two concurrent Codex assessments of the
  same three candidates share one prompt, schema, model and deadline and differ only in web
  access (live search with cited, dated sources vs. search disabled). Both get the same entry,
  exit and costs, alongside Quant only, Quant + news Codex and seeded Random.
  Install it with `install_shadow.py --daily --protocol daily-news-v2` and its own state
  directory. `daily-four-arm-v1` is unchanged.
- `daily-status.json` now reports each arm's net return in excess of the benchmark over the
  same window, and paired excess differences with standard errors, so an arm that is simply
  long more often in a rising market no longer looks like skill.
- Exit-only recovery and read-only reconciliation keep working when Robinhood bumps the MCP
  server version without changing tool schemas. New entries still require the reviewed version.
- The Codex event parser ignores blank lines.

## 1.0.0-beta.3 — 2026-10-12

Python distribution version: `1.0.0b3`.

- Review and pin the authenticated Robinhood MCP 1.7.3 broker contracts.
- Retain any-agent provenance without filtering account orders or changing ownership.
- Permit exact legacy-contract journal inspection through read-only reconciliation; saved reviews and write recovery remain blocked on contract mismatch.
- Preserve owner limits, execution authorization, SHADOW capability and frozen research protocols.

## 1.0.0-beta.2 — 2026-10-12

Python distribution version: `1.0.0b2`.

- Isolate SHADOW market collection to the two existing pinned market-data contracts.
- Batch quote requests at 20 symbols to preserve previous-session close references.
- Preserve full LIVE broker contract validation and review/place/cancel authorization.
- Preserve strategy identities, daily-four-arm-v1, scheduled windows and existing evidence.

## [1.0.0-beta.1](https://github.com/danielye0010/TradeAgent/releases/tag/v1.0.0-beta.1) — 2026-10-09

First packaged beta release. Python distribution version: `1.0.0b1`.

- Market scanning, immutable forecasts and cost-aware quantitative TradePlans.
- Scheduled SHADOW comparison of Quant Only, Codex Only, Quant + Codex and seeded Random.
- Separate, explicitly owner-authorized US equity/ETF execution through the official
  Robinhood Trading MCP, with durable order identity, reconciliation and recovery.
- Synthetic research demos and simulated broker lifecycles.
- Linux support with Python 3.12–3.14; wheel, source distribution and SHA-256 checksums.
- Clarified installation, research/LIVE boundaries and release validation.

Prospective profitability and Codex uplift are not established. SHADOW results are
modeled outcomes, not actual fills. This packaging release preserves strategy
identities and persisted protocol versions.
