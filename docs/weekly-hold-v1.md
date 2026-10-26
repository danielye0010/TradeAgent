# weekly-hold-v1 (SHADOW, unvalidated)

Protocol frozen 2026-10-26. Five independent $10,000 virtual accounts: Codex,
Claude, long-only 12-1 momentum, deterministic random and SPY buy-and-hold.
No broker writes, account reads or owner live configuration. No automatic LIVE promotion.

## Frozen universe

The complete 106-name frozen research profile, excluding these ETFs:
SPY QQQ IWM DIA XLF XLK XLE XLV TLT GLD XLU XLI XLP XLY.
The remaining names are common equities; no leveraged/inverse product remains.
Universe (ordered, unchanged during this protocol):

AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, AVGO, AMD, ORCL, CRM, NFLX, UBER, JPM, GS, LLY, UNH, XOM, CVX, COST, WMT, BAC, C, WFC, MS, SCHW, V, MA, AXP, BLK, PGR, CB, JNJ, MRK, ABBV, PFE, AMGN, GILD, TMO, ABT, ISRG, MDT, BMY, CVS, HD, LOW, TGT, NKE, SBUX, MCD, KO, PEP, PM, MO, ADBE, INTU, NOW, IBM, CSCO, QCOM, INTC, MU, TXN, AMAT, LRCX, KLAC, PANW, CRWD, PLTR, GE, CAT, DE, HON, RTX, LMT, BA, UPS, UNP, FDX, WM, DIS, CMCSA, T, VZ, TMUS, BKNG, ABNB, PYPL, COIN, SLB, COP, OXY

Canonical ordered-list SHA-256: `0928078e8ef2c734f825329a2ed3a53aa1aaee959e5eb072730ccf28ec9b3e4d`.

## Clock and decisions

All schedules use America/New_York and the pinned XNYS calendar. First session
of each ISO week: information cutoff 11:30, model completion deadline 11:55.
A pre-cutoff packet captures completed daily total-return features and current
quotes/positions. Both models receive byte-identical packets (including both
model accounts, explicitly labeled) and prompts/schema, concurrently. Each
answers only for its own account. Native web search is enabled; publication times
must be known, timezone-aware and no later than cutoff. Source URLs, publication
times, claims, search events, alias/resolved model when available, CLI versions,
raw-output hashes, rationale, invalidation and advisory exits persist.
Search is observed, not inferred from a flag. Unknown immutable weight identity is
explicit. Invalid/late/absent decisions retain positions, prohibit rebalance and
are missing coverage; portfolio marks still persist but that week's performance
is excluded from paired inference.

## Allocation, fills and settlement

Active groups choose at most three equities, each targeted to decision NAV/3;
unselected thirds remain cash. Random always selects three using SHA-256 of
protocol ID plus decision date. Momentum uses the previous session's adjusted
close at t-21 divided by t-252 minus one; all intervening XNYS sessions must exist.
Continued holdings trade only if deviation exceeds 2% of decision NAV. Fractional
shares round down to six decimals. SPY invests once and stays invested.

12:00 <= receipt AND venue quote < 12:10, first valid quote per symbol. Quotes
must be active, positive, noncrossed, and at most 15 seconds old. Sell before buy,
using bid/ask plus the existing 5bp per-side slippage model; extra fees assumed
zero and explicitly labeled. No double-counted spread. Weighted-average cost basis is retained on partial sales;
funding and holding-duration lots remain individually traceable. Cash affordability includes
costs. Every decision and target plan is durably frozen before fills. Missing
quotes never become fills, zero returns or a later catch-up trade. Retained names
trade net deltas. A durable plan/quote/fill journal resumes exactly once on restart.

Primary: cash account T+1. Unsettled sale proceeds may fund a purchase, with
funding settlement dates retained by lot. That funded stock cannot be sold before
payment settles. A blocked risk exit is retried only at the first subsequent
scheduled risk check after settlement. Sensitivity books replay the SAME decisions
using settled cash only; unfilled buy targets have one next-session 12:00-12:10
window after sale settlement. Missing that window is terminal. Available funds,
partial fills and cash exposure remain explicit, never assumed full investment.

15:45 each session: sell a whole active-group holding if bid/WAC <= 0.90.
No reentry until the next weekly rebalance. Early-close sessions have no 15:45
regular-session window and record a skipped check; no invented quote. Overnight
gaps may exceed 10%; this is a trigger, not a guaranteed loss cap. SPY buy-and-hold
has no strategy stop. AI exit suggestions are recorded, never acted upon.

## Corporate actions and valuation

Official market capability exposes only quotes and historical bars, not a corporate
action endpoint. Yahoo public chart events is the explicit permitted fallback;
raw responses, source URLs and receipt times persist. Cash dividends are accrued
for shares held before the ex-date; payable-date absence is pending cash entitlement,
not guessed spendable cash. Splits adjust shares and cost, not economic value.
Absent/ambiguous events remain pending and disqualify affected performance.
Daily marks use observed completed closing prices; no interpolation. 17:15 writes
JSON plus daily/weekly Markdown reports. Weeks end at the next decision-day
pretrade NAV, so there is no hidden gap between weekly observations.

## Comparisons and evidence

Primary: paired same-week Codex-minus-random and Claude-minus-random net returns.
Secondary: SPY, momentum, Codex-minus-Claude and daily-exposure-matched SPY.
Report n, mean difference and descriptive standard error; weeks are not assumed
independent. Costs, turnover, drawdown, cash exposure, actual holding duration,
coverage, missing/deferred actions and sensitivity books are always visible.
Missing weekly coverage is excluded from performance inference, not hidden from
NAV/account records. Two primary hypotheses require multiplicity control for any
future confirmatory inference; this version makes no significance/winner claim.
Week 4 checks reliability/accounting; week 13 checks initial economic differences.
Non-significance does not establish no effect. Short profit does not authorize LIVE.

Historical quant/random/SPY simulation has separate databases, source hashes,
quality gates and output paths. Daily open execution is a proxy for noon quotes;
5bp side slippage plus an explicitly assumed spread is modeled. Current-universe
survivorship, retrospective adjustment and missing point-in-time information are
limitations. Historical data never enters the prospective journal or public
prospective performance metrics. No AI historical-performance claim.

The first prospective week is the first weekly XNYS session strictly AFTER deployment.
CONFIG hash and implementation SHA-256 are recorded on activation; drift refuses
existing state. Existing 21-day experiment, timers and frozen observations remain intact.

Frozen CONFIG SHA-256: `951c64c82f304056ecfc51ffeb0b3cf651ffd4def3978412683f561cd3334483`.

## Commands and isolated deployment

```bash
tradeagent weekly prepare --output work/weekly-readonly-preflight
tradeagent weekly backtest --output work/weekly-baseline-history
tradeagent weekly init
python scripts/install_weekly.py --python /path/to/weekly-venv/bin/python --prepare-only
python scripts/install_weekly.py --python /path/to/weekly-venv/bin/python --enable
```

The installer writes only `tradeagent-weekly-*` units. Native model jobs yield when
existing shortline services are active. Decision ticks repeat within the deadline,
but an already-started interrupted assessment is missing, never silently rerun.
Packet creation happens at 11:29 so no after-cutoff quote enters the model packet.
The standalone weekly view binds only to `127.0.0.1:8643`; the normal dashboard also
provides `/weekly`. It reads the explicit paper projection, never owner LIVE settings.
A separate 17:25 publisher preserves daily files and links the new `weekly.html`.
Existing publisher units and interpreters are not modified.

Activation stores both CONFIG and dependency implementation identities. The first
eligible weekly session is strictly after deployment. A stopped machine produces
missing weeks/checks, not backfilled trades. Known dividend amounts without a verified
pay date remain pending cash entitlements and are excluded from available funds.
