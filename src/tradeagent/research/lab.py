"""One-shot scan: every enabled version predicts; only champions are selected."""

import json
from dataclasses import asdict

from ..strategy import DEFAULT_FAMILIES, DEFAULT_PARAMS, BaselineStrategy, implementation_hash
from .domain import canonical
from .expressions import plan, record_expressions
from .market import features, snapshot_identity
from .selector import rank


def seed(store, registered_at):
    for family in DEFAULT_FAMILIES:
        store.register(
            family,
            "v1",
            family,
            DEFAULT_PARAMS,
            registered_at,
            role="control" if family.endswith("control") else "champion",
        )


def scan(store, snapshot, now, max_selected=3):
    if snapshot.decision_time > now or now - snapshot.decision_time > 120:
        raise ValueError("scan must capture contemporaneous information within 120 seconds")
    if store.db.execute(
        "SELECT 1 FROM observations WHERE symbol=? AND end>? AND observed_at<=? LIMIT 1",
        (snapshot.symbol, snapshot.decision_time, now),
    ).fetchone():
        raise ValueError("future outcomes already known; use a separate replay database")
    versions = store.db.execute(
        "SELECT v.* FROM strategy_versions v JOIN strategies s USING(strategy_id) "
        "LEFT JOIN retirements r ON r.strategy_id=v.strategy_id AND r.version=v.version "
        "LEFT JOIN mutations m ON m.strategy_id=v.strategy_id AND m.challenger_version=v.version "
        "LEFT JOIN rejections j ON j.mutation_id=m.mutation_id "
        "WHERE s.enabled=1 AND r.version IS NULL AND j.mutation_id IS NULL "
        "AND v.registered_at<=? ORDER BY v.strategy_id,v.version",
        (snapshot.decision_time,),
    ).fetchall()
    if not versions:
        raise ValueError("no strategies registered before the decision; run init first")
    if any(v["implementation_hash"] != implementation_hash() for v in versions):
        raise ValueError("strategy implementation changed without a new durable version")
    snap_id = snapshot_identity(snapshot)
    f = features(snapshot)
    old = store.db.execute(
        "SELECT snapshot_id FROM market_snapshots WHERE symbol=? AND decision_time=? AND source=? AND evidence_kind=?",
        (snapshot.symbol, snapshot.decision_time, snapshot.source, snapshot.evidence_kind),
    ).fetchone()
    if old:
        if old[0] != snap_id:
            raise ValueError("decision snapshot cannot be revised")
        return {
            "status": "duplicate_suppressed",
            "snapshot_id": snap_id,
            "predictions": store.db.execute(
                "SELECT COUNT(*) FROM predictions WHERE snapshot_id=?", (snap_id,)
            ).fetchone()[0],
        }
    predictions = [
        BaselineStrategy(
            v["strategy_id"], v["version"], v["family"], json.loads(v["params"])
        ).predict(snapshot, now)
        for v in versions
    ]
    ranking = rank(store, predictions, snapshot, max_selected)
    plans = []
    with store.db:
        store.insert(
            "market_snapshots",
            {
                "snapshot_id": snap_id,
                "symbol": snapshot.symbol,
                "decision_time": snapshot.decision_time,
                "captured_at": now,
                "source": snapshot.source,
                "evidence_kind": snapshot.evidence_kind,
                "benchmark": snapshot.benchmark,
                "regime": f["regime"],
                "features": canonical(f),
                "payload": canonical(asdict(snapshot)),
            },
        )
        for p in predictions:
            row = {
                k: v
                for k, v in asdict(p).items()
                if k not in {"features", "context", "distribution"}
            }
            row.update(
                features=p.features.encoded,
                context=p.context.encoded,
                distribution=p.distribution.encoded,
            )
            store.insert("predictions", row)
            record_expressions(store, p, snapshot)
        for row in ranking:
            store.insert("selections", {**row, "rationale": canonical(row["rationale"])})
            p = next(p for p in predictions if p.prediction_id == row["prediction_id"])
            trade_plan = asdict(plan(p, snapshot, row["selected"]))
            store.insert("trade_plans", trade_plan)
            plans.append(trade_plan)
    return {
        "status": "persisted",
        "snapshot_id": snap_id,
        "predictions": len(predictions),
        "ranking": ranking,
        "trade_plans": plans,
        "real_broker_calls": 0,
    }
