# Changelog

## Unreleased

- Exit-only recovery and read-only reconciliation keep working when Robinhood bumps the MCP
  server version without changing tool schemas. New entries still require the reviewed version.

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
