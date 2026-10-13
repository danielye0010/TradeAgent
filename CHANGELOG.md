# Changelog

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
