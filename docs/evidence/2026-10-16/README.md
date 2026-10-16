# Real-market Paper results — 2026-10-16

These are saved **prospective SHADOW / Paper results from real market data**. They are
**not broker fills or money earned in a live account**. Decisions were frozen before
their entry windows; the daily pipeline subsequently scored saved Robinhood MCP
minute observations. This snapshot includes every saved v1/v2 frozen session,
all arms, cash decisions, pending results and final capture failures at export time.

Captured: 2026-10-16T15:43:29.696580+00:00 (2026-10-16 10:43:29 America/Chicago).
This is a fixed snapshot, not a live dashboard feed.

## Positive observations and their context

Codex News and Codex Price selected the same symbol in both resolved v2 sessions.
The combined arm also selected that symbol; these are shared observations, not
three independent wins. The prices below are modeled entry/exit prices from
saved minute bars, not executable bid/ask fills.

| Session | Selection | Entry → exit (New York) | Entry price | Exit price | Net after modeled costs | Hypothetical P&L per $1,000 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 2026-10-14 | TLT | 10:37:00 → 11:37:00 | 78.2050 | 78.3501 | +0.1435% | $+1.43 |
| 2026-10-15 | CRM | 10:37:00 → 11:37:00 | 243.5050 | 246.1100 | +0.9856% | $+9.86 |

The v1 strategies lost money on average over their four resolved sessions.
Both protocols are shown below; the positive v2 results do not establish a
repeatable edge. There are only two resolved v2 sessions. News and Price chose
identically, so these observations show no incremental return from news.

## All arms in this snapshot

Means are arithmetic daily means over resolved sessions, including CASH=0.
They are not cumulative or compounded account returns. The protocols run at
different times and cover different dates; this table is not a matched v1/v2 test.

| Protocol | Arm | Frozen | Resolved | Pending / unavailable | Mean daily net |
| --- | --- | ---: | ---: | ---: | ---: |
| daily-four-arm-v1 | Codex Only | 4 | 4 | 0 | -0.2465% |
| daily-four-arm-v1 | Quant + Codex | 4 | 4 | 0 | -0.2465% |
| daily-four-arm-v1 | Quant Only | 4 | 4 | 0 | -0.3017% |
| daily-four-arm-v1 | Random | 4 | 4 | 0 | -0.3161% |
| daily-news-v2 | Codex News | 3 | 2 | 1 | +0.5646% |
| daily-news-v2 | Codex Price | 3 | 2 | 1 | +0.5646% |
| daily-news-v2 | Quant + Codex News | 3 | 2 | 1 | +0.5646% |
| daily-news-v2 | Quant Only | 3 | 2 | 1 | +0.5427% |
| daily-news-v2 | Random | 3 | 2 | 1 | -0.1734% |

v2 on 2026-10-16 is pending in this snapshot; it is not counted as zero or a win.
The 31 arm/session rows are correlated comparisons across seven frozen sessions,
not 31 independent trading days.

## Capture failures retained

| Protocol | Session | Failure |
| --- | --- | --- |
| daily-four-arm-v1 | 2026-10-09 | MISSED_DECISION_WINDOW |
| daily-four-arm-v1 | 2026-10-12 | MARKET_EVIDENCE_UNAVAILABLE |
| daily-news-v2 | 2026-10-13 | MISSED_DECISION_WINDOW |

These failures have no fabricated decision or return. An unresolved v1 COST
candidate path from 2026-10-13 also remains missing in the source report; that
candidate was not selected by an arm and does not invalidate the four resolved
arm comparisons. This export covers selected arms, not every scanned candidate.

## Files and calculation

- [returns.csv](returns.csv): all 31 arm/session records, with times, prices, costs, gross/net/stress returns and decision IDs.
- [summary.csv](summary.csv): all nine arm summaries.
- [records.json](records.json): whitelisted decision/outcome fields, prospective labels, final session states and original payload hashes.
- [SHA256SUMS](SHA256SUMS): byte hashes for the three data files.

CSV/JSON returns are decimal fractions (0.01 = 1%). Empty prices/returns mean
unavailable or pending; resolved CASH is explicitly zero. The $1,000 basis is
hypothetical and resets per arm/session. Source records report zero orders submitted.

The saved price model uses the first complete minute open in the planned entry
window and the complete endpoint close. It requires synchronized symbol/SPY paths.
Base net = (exit / entry) × (1 − frozen side cost) / (1 + frozen side cost) − 1.
Frozen per-side cost = (max(1 bp, observed spread / 2) + 1 bp slippage + 0.1 bp fee) / 10,000.
Stress returns use the separately saved stress cost. These costs and prices model
trading economics; they do not measure actual fills, fees or execution quality.

The export read the append-only opportunity_decisions, opportunity_outcomes and
opportunity_sessions tables with SQLite mode=ro and query_only, within a read
transaction per protocol. Gross/net arithmetic, prospective pool identity and
freeze-before-entry / resolution-after-exit timing were checked against those
records. No strategy, quote, outcome or timer was changed.

Original-payload hashes allow the owner to compare against local source records.
They are not independent broker attestations or cryptographic proof that a record
existed before its outcome. Public files contain only allowlisted market research
fields, without credentials, account IDs, balances, live orders or operator paths.
