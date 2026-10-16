# Changelog

## Unreleased

- `tradeagent dashboard`: a local, read-only web dashboard. Today's session as a
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
