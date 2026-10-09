"""Candidate discovery from completed provider observations, never order signals."""

import json
import math
import statistics
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request
from zoneinfo import ZoneInfo

from ..calendar import session_bounds
from ..prospective.providers import open_provider
from .domain import Bar, MarketSnapshot, identity, iso, timestamp

UNIVERSE = "AAPL MSFT NVDA AMZN META GOOGL TSLA AVGO AMD ORCL CRM NFLX UBER JPM GS LLY UNH XOM CVX COST WMT SPY QQQ IWM DIA XLF XLK XLE XLV TLT GLD".split()
SCANNER_VERSION = "opportunities-v1"


def universe(symbols):
    if (
        not symbols
        or len(set(symbols)) != len(symbols)
        or any(
            not isinstance(s, str)
            or not s.isascii()
            or not s.replace(".", "").isalpha()
            or s != s.upper()
            for s in symbols
        )
    ):
        raise ValueError("provide unique US equity/ETF symbols")
    return list(symbols)


def latest_session(now):
    day = datetime.fromtimestamp(now, ZoneInfo("America/New_York")).date()
    for offset in range(14):
        bounds = session_bounds(day - timedelta(days=offset))
        if bounds and bounds[0] < now:
            return bounds
    raise ValueError("no recent XNYS session available")


def capture(symbols, provider="robinhood", oauth_helper=None, *, clock=time.time):
    """Reuse provider authentication and read transport; market tools only."""
    symbols = universe(symbols)
    requested = list(dict.fromkeys([*symbols, "SPY"]))
    started = clock()
    bounds = latest_session(started)
    end = min(started, bounds[1])
    bars, quotes, references, missing = [], {}, {}, []
    with open_provider(provider, Path(__file__).resolve().parents[3], oauth_helper) as adapter:
        if provider == "robinhood":
            for index in range(0, len(requested), 5):
                batch = requested[index : index + 5]
                history = adapter.bridge.read(
                    "get_equity_historicals",
                    {
                        "symbols": batch,
                        "start_time": iso(bounds[0]),
                        "end_time": iso(end),
                        "interval": "minute",
                        "bounds": "regular",
                        "adjustment_type": "none",
                    },
                )["data"]
                received = clock()
                missing.extend(history.get("not_found") or [])
                for item in history.get("results") or []:
                    if (
                        item["symbol"] not in batch
                        or item["interval"] != "minute"
                        or item["bounds"] != "regular"
                    ):
                        raise ValueError("unexpected provider history identity")
                    for raw in item.get("bars") or []:
                        start = timestamp(raw["begins_at"])
                        if (
                            raw.get("interpolated")
                            or raw.get("session") != "reg"
                            or start + 60 > end
                        ):
                            continue
                        bars.append(
                            asdict(
                                Bar(
                                    item["symbol"],
                                    start,
                                    start + 60,
                                    received,
                                    *[
                                        float(raw[k])
                                        for k in (
                                            "open_price",
                                            "high_price",
                                            "low_price",
                                            "close_price",
                                            "volume",
                                        )
                                    ],
                                )
                            )
                        )
            response = adapter.bridge.read("get_equity_quotes", {"symbols": requested})["data"]
            received = clock()
            for item in response.get("results") or []:
                raw = item["quote"]
                symbol = raw["symbol"]
                if symbol not in requested or symbol in quotes:
                    raise ValueError("unexpected or duplicate quote identity")
                try:
                    quotes[symbol] = {
                        "bid": float(raw["bid_price"]),
                        "ask": float(raw["ask_price"]),
                        "asof": min(
                            timestamp(raw["venue_bid_time"]), timestamp(raw["venue_ask_time"])
                        ),
                        "observed_at": received,
                    }
                    previous = session_bounds(
                        datetime.fromisoformat(raw["previous_close_date"]).date()
                    )
                    if previous:
                        references[symbol] = {
                            "previous_close": float(raw["previous_close"]),
                            "previous_close_time": previous[1],
                        }
                except (KeyError, ValueError, TypeError):
                    missing.append(symbol + ": quote/reference unavailable")
        elif provider == "alpaca":
            # Existing external credential loader/opener; no new auth or paid feed.
            def get(path, params):
                request = Request(
                    "https://data.alpaca.markets/v2/stocks/" + path + "?" + urlencode(params),
                    headers={
                        "APCA-API-KEY-ID": adapter._key,
                        "APCA-API-SECRET-KEY": adapter._secret,
                    },
                )
                with adapter._opener.open(request, timeout=20) as response:
                    return json.load(response)

            params = {
                "symbols": ",".join(requested),
                "timeframe": "1Min",
                "start": iso(bounds[0]),
                "end": iso(end),
                "adjustment": "raw",
                "feed": "iex",
                "limit": 10000,
            }
            pages = set()
            while True:
                payload = get("bars", params)
                received = clock()
                for symbol, items in payload.get("bars", {}).items():
                    for raw in items:
                        start = timestamp(raw["t"])
                        if start + 60 <= end:
                            bars.append(
                                asdict(
                                    Bar(
                                        symbol,
                                        start,
                                        start + 60,
                                        received,
                                        *[float(raw[k]) for k in ("o", "h", "l", "c", "v")],
                                    )
                                )
                            )
                cursor = payload.get("next_page_token")
                if not cursor:
                    break
                if cursor in pages:
                    raise ValueError("repeated market-data pagination cursor")
                pages.add(cursor)
                params["page_token"] = cursor
            payload = get("snapshots", {"symbols": ",".join(requested), "feed": "iex"})
            received = clock()
            for symbol, item in payload.items():
                raw = item.get("latestQuote") or {}
                if raw:
                    quotes[symbol] = {
                        "bid": raw["bp"],
                        "ask": raw["ap"],
                        "asof": timestamp(raw["t"]),
                        "observed_at": received,
                    }
                previous = item.get("prevDailyBar")
                if previous:
                    prior = session_bounds(
                        datetime.fromtimestamp(
                            timestamp(previous["t"]), ZoneInfo("America/New_York")
                        ).date()
                    )
                    if prior:
                        references[symbol] = {
                            "previous_close": previous["c"],
                            "previous_close_time": prior[1],
                        }
        else:
            raise ValueError("unknown provider")
    observed = clock()
    return {
        "schema_version": 1,
        "source": provider + "-opportunity-minute-v1",
        "observed_at": observed,
        "requested_at": started,
        "evidence_kind": "prospective"
        if bounds[0] <= observed < bounds[1]
        else "historical_market",
        "session_open": bounds[0],
        "session_close": bounds[1],
        "interval_seconds": 60,
        "symbols": symbols,
        "bars": bars,
        "quotes": quotes,
        "references": references,
        "limitations": missing
        + (
            ["Alpaca IEX is single-exchange evidence, not consolidated NBBO"]
            if provider == "alpaca"
            else []
        ),
    }


def grouped(dataset, now):
    if timestamp(dataset["observed_at"]) > now or dataset["evidence_kind"] not in {
        "prospective",
        "historical_market",
        "synthetic",
        "replay",
    }:
        raise ValueError("future or unknown market evidence")
    result = {}
    for item in dataset["bars"]:
        bar = Bar.from_dict(item)
        if bar.available_at > dataset["observed_at"] or bar.available_at > now:
            raise ValueError("future bar availability")
        result.setdefault(bar.symbol, []).append(bar)
    for bars in result.values():
        bars.sort(key=lambda b: b.start)
        if any(a.end > b.start for a, b in zip(bars, bars[1:], strict=False)):
            raise ValueError("duplicate/overlapping market bars")
    return result


def scan(dataset, *, now=None, window_minutes=60, candidate_count=5, events=()):
    now = time.time() if now is None else timestamp(now)
    if (
        type(window_minutes) is not int
        or window_minutes < 5
        or type(candidate_count) is not int
        or candidate_count < 1
    ):
        raise ValueError("window must be at least five minutes and candidate count positive")
    histories = grouped(dataset, now)
    pool, benchmark = [], histories.get("SPY", [])
    for symbol in universe(dataset["symbols"]):
        row = {
            "symbol": symbol,
            "observation_time": dataset["observed_at"],
            "source": dataset["source"],
            "features": {},
            "categories": [],
            "shortlist_reasons": [],
            "limitations": [],
            "status": "REJECTED",
            "ranking_score": 0.0,
        }
        own = histories.get(symbol, [])
        if len(own) < 5:
            row["limitations"].append("insufficient completed bars")
            pool.append(row)
            continue
        end = own[-1].end
        window = [b for b in own if end - window_minutes * 60 < b.end <= end]
        matched = [b for b in benchmark if window[0].start <= b.start and b.end <= end]
        quote = dataset.get("quotes", {}).get(symbol)
        row["market_evidence"] = {
            "window_start": window[0].start,
            "window_end": end,
            "window_open": window[0].open,
            "window_close": window[-1].close,
            "completed_bars": len(window),
            "quote": quote,
        }
        if not quote:
            row["limitations"].append("actual bid/ask quote unavailable")
        elif (
            not quote.get("asof", now + 1) <= quote.get("observed_at", now + 1) <= now
            or now - quote["asof"] > 120
        ):
            row["limitations"].append("quote stale/future; fresh execution evidence required")
        f = row["features"]
        f["window_return"] = window[-1].close / window[0].open - 1
        returns = [math.log(b.close / b.open) for b in window]
        rv = math.sqrt(sum(r * r for r in returns))
        f["realized_volatility"] = rv
        f["observed_bar_end"] = end
        scale = max(rv, 0.001)
        score = abs(f["window_return"]) / scale
        if abs(f["window_return"]) >= max(0.003, 1.5 * rv):
            row["categories"].append("abnormal_move")
        if matched and [(b.start, b.end) for b in matched] == [(b.start, b.end) for b in window]:
            f["spy_return"] = matched[-1].close / matched[0].open - 1
            f["relative_strength"] = f["window_return"] - f["spy_return"]
            score += abs(f["relative_strength"]) / scale
            if abs(f["relative_strength"]) > 0.002:
                row["categories"].append(
                    "relative_strength" if f["relative_strength"] > 0 else "relative_weakness"
                )
        else:
            row["limitations"].append(
                "SPY window not synchronized; relative-strength feature omitted"
            )
        split = len(window) // 2
        early, late = window[:split], window[split:]
        early_volume = statistics.fmean(b.volume for b in early)
        if early_volume > 0:
            f["volume_ratio"] = statistics.fmean(b.volume for b in late) / early_volume
            score += min(3, max(0, f["volume_ratio"] - 1)) / 2
            if f["volume_ratio"] >= 2:
                row["categories"].append("unusual_volume")
        first_rv = math.sqrt(statistics.fmean(r * r for r in returns[:split]))
        if first_rv > 0:
            f["volatility_ratio"] = (
                math.sqrt(statistics.fmean(r * r for r in returns[split:])) / first_rv
            )
            if f["volatility_ratio"] >= 1.5:
                row["categories"].append("volatility_change")
        reference = dataset.get("references", {}).get(symbol, {})
        if own[0].start == dataset["session_open"] and reference.get("previous_close", 0) > 0:
            f["gap"] = own[0].open / reference["previous_close"] - 1
            if abs(f["gap"]) > 0.002:
                row["categories"].append(
                    "gap_continuation" if f["gap"] * f["window_return"] > 0 else "gap_reversal"
                )
        f["deviation_from_window_mean"] = (
            window[-1].close / statistics.fmean(b.close for b in window) - 1
        )
        if window[-1].close > max(b.high for b in window[:-1]):
            row["categories"].append("breakout")
        if any(a.end != b.start for a, b in zip(window, window[1:], strict=False)):
            row["limitations"].append("missing bars in scan window")
        if now - end > 120:
            row["limitations"].append("completed prices are historical, not execution-fresh")
        row["ranking_score"] = round(score, 6)
        row["status"] = "CANDIDATE" if row["categories"] else "REJECTED"
        row["shortlist_reasons"] = row["categories"] or ["no material unusual feature"]
        pool.append(row)
    for event in events:
        from urllib.parse import urlparse

        if (
            event.get("symbol") not in dataset["symbols"]
            or not event.get("verified")
            or urlparse(event.get("url", "")).scheme != "https"
            or not timestamp(event["published_at"]) <= timestamp(event["observed_at"]) <= now
            or now - timestamp(event["published_at"]) > 86400
        ):
            raise ValueError("event candidate requires dated fresh verified source evidence")
        row = next((r for r in pool if r["symbol"] == event["symbol"]), None)
        if row:
            row["categories"].append("verified_event")
            row["shortlist_reasons"].append(event["claim"])
            row["event_evidence"] = event
            row["ranking_score"] = max(3, row["ranking_score"])
            row["status"] = "CANDIDATE"
    eligible = sorted(
        (r for r in pool if r["status"] == "CANDIDATE"),
        key=lambda r: (-r["ranking_score"], r["symbol"]),
    )[:candidate_count]
    report = {
        "schema_version": 1,
        "scanner_version": SCANNER_VERSION,
        "decision_time": now,
        "market_observed_at": dataset["observed_at"],
        "source": dataset["source"],
        "evidence_kind": dataset["evidence_kind"],
        "window_minutes": window_minutes,
        "candidate_count": candidate_count,
        "universe": dataset["symbols"],
        "status": "READY"
        if dataset["evidence_kind"] == "prospective" and now - dataset["observed_at"] <= 120
        else "RESEARCH_ONLY",
        "limitations": dataset.get("limitations", []),
        "candidates": eligible,
        "candidate_pool": pool,
        "interpretation": "ranking measures unusual observations, not expected trading return",
    }
    report["scan_id"] = identity(report)
    return report


def research_snapshot(dataset, symbol, now):
    histories = grouped(dataset, now)
    q = dataset.get("quotes", {}).get(symbol)
    if not q or dataset["evidence_kind"] not in {"prospective", "synthetic", "replay"}:
        raise ValueError(
            "fresh contemporaneous quote unavailable; historical capture remains research-only"
        )
    own, benchmark = histories[symbol], histories["SPY"]
    ends = {b.end for b in own} & {b.end for b in benchmark}
    if not ends:
        raise ValueError("symbol and SPY bars do not align")
    end = max(ends)
    own, benchmark = (
        tuple(b for b in own if b.end <= end),
        tuple(b for b in benchmark if b.end <= end),
    )
    for path in (own, benchmark):
        if any(a.end != b.start for a, b in zip(path, path[1:], strict=False)):
            raise ValueError("incomplete synchronized signal path")
    if [(b.start, b.end) for b in own] != [(b.start, b.end) for b in benchmark]:
        raise ValueError("incomplete synchronized symbol/SPY signal path")
    reference = dataset.get("references", {}).get(symbol, {})
    opening_known = own[0].start == dataset["session_open"]
    return MarketSnapshot(
        symbol,
        now,
        dataset["source"],
        dataset["evidence_kind"],
        "SPY",
        own,
        benchmark,
        q["bid"],
        q["ask"],
        q["asof"],
        q["observed_at"],
        own[0].open if opening_known else None,
        reference.get("previous_close"),
        (),
        dataset["session_open"] if opening_known else None,
        reference.get("previous_close_time"),
    )
