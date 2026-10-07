"""Exact horizon, contiguous future observations; missing data remains unresolved."""

import json
import math
import statistics
from dataclasses import asdict

from .domain import Bar, OptionBook, canonical, identity
from .expressions import counterfactuals


def ingest(store, dataset, observed_at):
    source = dataset["source"]
    if not isinstance(source, str) or not source:
        raise ValueError("observation source is required")
    for item in dataset.get("bars", []):
        bar = Bar.from_dict(item)
        if bar.available_at > observed_at:
            raise ValueError("unavailable future observation")
        row = {
            "observation_id": identity([source, bar.symbol, bar.start, bar.end]),
            **asdict(bar),
            "source": source,
            "observed_at": observed_at,
        }
        old = store.db.execute(
            "SELECT * FROM observations WHERE observation_id=?", (row["observation_id"],)
        ).fetchone()
        if old:
            if any(old[k] != v for k, v in row.items() if k != "observed_at"):
                raise ValueError("observation revision conflicts with immutable evidence")
        else:
            store.insert("observations", row)
    for item in dataset.get("options", []):
        quote = OptionBook.from_dict(item)
        if quote.available_at > observed_at:
            raise ValueError("unavailable future option quote")
        row = {
            "quote_id": identity([source, quote.contract_id, quote.asof]),
            "contract_id": quote.contract_id,
            "asof": quote.asof,
            "available_at": quote.available_at,
            "observed_at": observed_at,
            "source": source,
            "bid": quote.bid,
            "ask": quote.ask,
            "payload": canonical(asdict(quote)),
        }
        old = store.db.execute(
            "SELECT * FROM option_observations WHERE quote_id=?", (row["quote_id"],)
        ).fetchone()
        if old:
            if old["payload"] != row["payload"]:
                raise ValueError("option observation revision conflicts with evidence")
        else:
            store.insert("option_observations", row)


def path(store, symbol, start, end, source):
    bars = store.db.execute(
        "SELECT * FROM observations WHERE symbol=? AND source=? AND start>=? AND end<=? ORDER BY start,end",
        (symbol, source, start, end),
    ).fetchall()
    if not bars or bars[0]["start"] != start or bars[-1]["end"] != end:
        return None
    if any(a["end"] != b["start"] for a, b in zip(bars, bars[1:], strict=False)):
        return None
    return bars


def resolve(store, dataset, now):
    resolved = []
    with store.db:
        ingest(store, dataset, now)
        pending = store.db.execute(
            "SELECT p.*,s.source,s.benchmark FROM predictions p JOIN market_snapshots s USING(snapshot_id) "
            "LEFT JOIN outcomes o USING(prediction_id) WHERE o.prediction_id IS NULL "
            "AND p.decision_time+p.horizon<=?",
            (now,),
        ).fetchall()
        for p in pending:
            end = p["decision_time"] + p["horizon"]
            bars = path(store, p["symbol"], p["decision_time"], end, p["source"])
            if bars is None:
                continue
            f = json.loads(p["features"])
            snapshot = json.loads(
                store.db.execute(
                    "SELECT payload FROM market_snapshots WHERE snapshot_id=?", (p["snapshot_id"],)
                ).fetchone()[0]
            )
            delayed = (
                snapshot.get("signal_bar_begins_at") is None
                and snapshot["bars"][-1]["end"] < p["decision_time"]
            )
            # A delayed signal close is a feature, not a decision-time entry price.
            entry = bars[0]["open"] if delayed else f["entry_close"]
            raw = bars[-1]["close"] / entry - 1
            benchmark_path = path(store, p["benchmark"], p["decision_time"], end, p["source"])
            if benchmark_path is None:
                continue
            benchmark_entry = benchmark_path[0]["open"] if delayed else f["benchmark_close"]
            benchmark = benchmark_path[-1]["close"] / benchmark_entry - 1
            direction = p["direction"]
            up = max(b["high"] for b in bars) / entry - 1
            down = min(b["low"] for b in bars) / entry - 1
            mfe, mae = (
                (max(0.0, up), min(0.0, down))
                if direction >= 0
                else (max(0.0, -down), min(0.0, -up))
            )
            closes = [entry] + [b["close"] for b in bars]
            logs = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]
            metadata = {
                "observation_ids": [b["observation_id"] for b in bars],
                "benchmark_observation_ids": [b["observation_id"] for b in benchmark_path or []],
                "exit_close": bars[-1]["close"],
                "entry_price": entry,
                "benchmark_entry_price": benchmark_entry,
                "entry_price_model": "decision-minute open" if delayed else "signal close",
                "signal_bar_end": snapshot["bars"][-1]["end"],
                "bar_count": len(bars),
                "volatility_definition": "population stddev of observed bar log returns, not annualized",
                "sector_adjustment": "unavailable",
            }
            outcome = {
                "prediction_id": p["prediction_id"],
                "outcome_time": end,
                "resolved_at": now,
                "raw_return": raw,
                "benchmark_return": benchmark,
                "residual_return": raw - benchmark if benchmark is not None else None,
                "mfe": mfe,
                "mae": mae,
                "realized_volatility": statistics.pstdev(logs),
                "source": p["source"],
                "metadata": canonical(metadata),
            }
            store.insert("outcomes", outcome)
            counterfactuals(store, p, {**outcome, "metadata": metadata}, now)
            resolved.append(p["prediction_id"])
    return {
        "resolved": len(resolved),
        "prediction_ids": resolved,
        "unresolved": store.db.execute(
            "SELECT COUNT(*) FROM predictions p LEFT JOIN outcomes o USING(prediction_id) WHERE o.prediction_id IS NULL"
        ).fetchone()[0],
    }
