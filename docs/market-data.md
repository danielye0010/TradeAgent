# Market-data providers

Default: **Robinhood market data + Robinhood execution**. Optional:
**Alpaca market data + Robinhood execution**. The provider affects market data,
not strategy parameters or execution authorization. There is no automatic fallback.

## Capability matrix

| Requirement | Robinhood | Alpaca | Semantics / default / optional status |
|---|---|---|---|
| Frozen feature history | Explicit raw OHLCV with interval=minute | Raw completed SIP minute snapshot | Robinhood default; current features need opening bars, not a multiday RSI lookback |
| Current snapshot | Bid/ask with separate venue timestamps; raw previous close and date | Latest bid/ask and previous daily close | Reject stale, zero, future, inactive or missing quotes; Alpaca optional |
| QQQ/IWM alignment | QQQ/IWM/SPY in one historical request | All three symbols in one snapshot request | Same contiguous opening history and aligned SPY endpoint checks |
| Latest completed observation | UTC begins_at, explicitly left-edge labeled | UTC t, minute start | End = start + 60; exclude forming intervals and observations received after decision |
| One-hour outcome | Explicit minute history spanning decision through exact endpoint | Forward snapshots accumulated minute by minute | Same exact 60-minute symbol/SPY path; no interpolation or nearest-close substitute |
| Availability | Actual local receipt, distinct from UTC bar start/end | Actual local receipt; post-boundary emission | Conservative receipt cutoff shared; no invented historical availability |
| Calendar/session | Request regular bounds; accept session=reg | Bound normalized bars to XNYS session | Shared pinned XNYS calendar and New York DST; no extended-session evidence |
| Failure | Missing/auth/schema/data failure blocks or skips | Missing credential/SIP/data failure blocks or skips | No broker writes, no provider fallback, no backfilled predictions |

The frozen strategy uses momentum, relative momentum, opening return, gap,
mean deviation and historical bar volatility. It has no RSI indicator. At the
opening decision it requires at least two completed opening minutes; the five-bar
mean uses whatever valid opening history is available, as before. No new lookback,
indicator or threshold has been introduced.

## Robinhood contract and evidence

The authenticated official MCP 1.7.0 catalog was inspected, including descriptions,
input/output schemas and read-only annotations. The two market schemas are pinned
in [market-data-1.7.0.json](../src/tradeagent/contracts/market-data-1.7.0.json).
They explicitly specify minute granularity, UTC left-edge labels, regular bounds,
raw adjustment selection and synthesized interpolated bars.

A safe read-only check on October 7, 2026 returned 90 one-minute regular-session
bars for each of QQQ, IWM and SPY from 09:30 through 11:00 New York, with matching
bar starts/endpoints and OHLCV. Quotes returned bid/ask venue times and prior close
dates. This demonstrates availability of the needed inputs. It does not prove
opening-session latency, completion of a future prospective capture, or profitability.

There is no explicit final/revision flag. Completion means start + 60 <= receipt;
forming and interpolated bars are excluded. Actual received facts are frozen.
A second read through the default adapter returned all 390 regular-session minutes
for each symbol, including the exact one-hour path. The market was closed, and
these observations were refused as prospective input.

The contract does not promise publication latency or a maximum historical lookback
for every interval. The runtime requests only the current regular session, at most
390 one-minute bars per symbol, rather than relying on unverified longer limits.
Subsequent polls request from the earliest missing minute with one completed-minute
overlap; restart reconstructs this cursor from persisted bars. Missing intervals
are retained in the request range rather than skipped by a latest-timestamp cursor.

Quotes use the older of bid/ask timestamps after validating freshness of both.
The gap reference uses raw previous_close and its trading date, matching
unadjusted OHLCV; adjusted previous close is not silently substituted.
Historical close_price is a bar close, not an official settled daily close.

Robinhood documents the official market-data tools in
[Trading with your agent](https://robinhood.com/us/en/support/articles/trading-with-your-agent/).
Alpaca's [streaming timing documentation](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data)
describes minute-start labels, emission after the boundary and later corrections.
The runtime's normalizer uses actual receipts for either provider.

## Small adapter boundary

MarketDataProvider.fetch(bounds, startup) returns provider data plus request and
receipt times; normalize maps it once to bars, quotes and session references.
Collection validates and persists those facts, constructs MarketSnapshot,
and supplies outcome observations. It owns all shared cutoff, alignment and
persistence logic. Strategy code never reads a provider client.

Robinhood calls only get_equity_historicals and get_equity_quotes through
the existing official read-only MCP transport. No account/order API is needed.
Alpaca uses its fixed market-data endpoint and encrypted credential loader.
Broker execution retains its separate official Robinhood boundary.
