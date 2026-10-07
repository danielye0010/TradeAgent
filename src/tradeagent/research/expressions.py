"""Alpha-independent expressions and executable-side shadow counterfactuals."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .domain import canonical, identity


@dataclass(frozen=True)
class TradePlan:
    prediction_id: str
    kind: str
    instrument: str | None
    entry_limit: float | None
    reason: str


def plan(prediction, snapshot, selected, option_kind=None):
    if not selected or prediction.direction == 0:
        return TradePlan(
            prediction.prediction_id, "NO_TRADE", None, None, "abstention or selector rejection"
        )
    if option_kind is not None:
        desired = "call" if prediction.direction > 0 else "put"
        candidates = [
            q
            for q in snapshot.options
            if q.kind == desired
            and q.expiration
            > datetime.fromtimestamp(snapshot.decision_time, timezone.utc).date().isoformat()
        ]
        if not candidates:
            return TradePlan(
                prediction.prediction_id, "NO_TRADE", None, None, "option quote unavailable"
            )
        chosen = min(candidates, key=lambda q: ((q.ask - q.bid) / q.ask, q.contract_id))
        return TradePlan(
            prediction.prediction_id,
            "LONG_CALL" if desired == "call" else "LONG_PUT",
            chosen.contract_id,
            chosen.ask,
            "long premium at ask; no option EV estimate",
        )
    if prediction.direction < 0:
        return TradePlan(
            prediction.prediction_id,
            "NO_TRADE",
            None,
            None,
            "underlying shorting is outside capital boundaries",
        )
    return TradePlan(
        prediction.prediction_id,
        "UNDERLYING",
        snapshot.symbol,
        snapshot.ask,
        "unleveraged underlying",
    )


def record_expressions(store, prediction, snapshot):
    rows = [
        ("NO_TRADE", "", 0.0, 1, "available", {}),
        (
            "UNDERLYING",
            "",
            snapshot.ask,
            1,
            "available",
            {
                "bid": snapshot.bid,
                "ask": snapshot.ask,
                "asof": snapshot.quote_time,
                "price_model": "entry ask; exit observed bar close less decision half-spread",
            },
        ),
    ]
    for kind, option_kind in (("LONG_CALL", "call"), ("LONG_PUT", "put")):
        quotes = [q for q in snapshot.options if q.kind == option_kind]
        if not quotes:
            rows.append((kind, "", None, 100, "unavailable", {"reason": "no decision-time quote"}))
        for q in quotes:
            rows.append((kind, q.contract_id, q.ask, q.multiplier, "available", asdict(q)))
    for kind, contract, entry, multiplier, status, quote in rows:
        store.insert(
            "shadow_expressions",
            {
                "expression_id": identity([prediction.prediction_id, kind, contract]),
                "prediction_id": prediction.prediction_id,
                "kind": kind,
                "contract_id": contract,
                "entry": entry,
                "multiplier": multiplier,
                "status": status,
                "quote_snapshot": canonical(quote),
            },
        )


def counterfactuals(store, prediction, outcome, now):
    snapshot = json.loads(
        store.db.execute(
            "SELECT payload FROM market_snapshots WHERE snapshot_id=?", (prediction["snapshot_id"],)
        ).fetchone()[0]
    )
    for row in store.db.execute(
        "SELECT * FROM shadow_expressions WHERE prediction_id=?", (prediction["prediction_id"],)
    ).fetchall():
        if store.db.execute(
            "SELECT 1 FROM counterfactuals WHERE expression_id=?", (row["expression_id"],)
        ).fetchone():
            continue
        quote = json.loads(row["quote_snapshot"])
        status, pnl, fraction, exit_price = row["status"], None, None, None
        metadata = {
            "selected_independently_of_alpha": True,
            "unit": "one share or one standard contract",
        }
        if row["kind"] == "NO_TRADE":
            pnl, fraction, exit_price = 0.0, 0.0, 0.0
        elif row["kind"] == "UNDERLYING":
            # A cost model is explicit; a bar close is never claimed to be an executable bid.
            half_spread = (snapshot["ask"] - snapshot["bid"]) / 2
            exit_price = max(0.0, outcome["metadata"]["exit_close"] - half_spread)
            pnl = exit_price - row["entry"]
            fraction = pnl / row["entry"]
            metadata["fill_model"] = (
                "estimated exit bid = bar close - decision half-spread; excludes fees"
            )
            metadata["executable_quote_observed"] = False
        elif row["status"] == "available":
            observed = store.db.execute(
                "SELECT * FROM option_observations WHERE contract_id=? AND asof=? AND source=?",
                (row["contract_id"], outcome["outcome_time"], outcome["source"]),
            ).fetchone()
            if observed:
                exit_quote = json.loads(observed["payload"])
                static = ("contract_id", "underlying", "kind", "expiration", "strike", "multiplier")
                if any(quote[k] != exit_quote[k] for k in static):
                    raise ValueError("option exit contract differs from decision contract")
                exit_price = observed["bid"]
                pnl = (exit_price - row["entry"]) * row["multiplier"]
                fraction = (exit_price - row["entry"]) / row["entry"]
                metadata.update(
                    entry_quote=quote,
                    exit_quote=exit_quote,
                    fill_model="buy ask / sell bid; excludes commissions",
                    executable_quote_observed=True,
                )
            else:
                status = "unavailable"
                metadata["reason"] = "no exact-horizon exit option quote; no mark-based substitute"
        store.insert(
            "counterfactuals",
            {
                "expression_id": row["expression_id"],
                "prediction_id": prediction["prediction_id"],
                "kind": row["kind"],
                "status": status,
                "pnl": pnl,
                "return_fraction": fraction,
                "exit": exit_price,
                "resolved_at": now,
                "metadata": canonical(metadata),
            },
        )
