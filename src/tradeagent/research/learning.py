"""Decayed daily clusters, zero-centered shrinkage and durable lesson revisions."""

import json
import math
import statistics
from collections import defaultdict

from .attribution import attribute
from .domain import canonical, identity, iso

ALGORITHM = "daily-cluster-shrinkage-v1"
CONFIG = {
    "prior_days": 10,
    "half_life_days": 30,
    "prior_daily_sigma": 0.01,
    "cost": 0.001,
    "clip": 0.02,
    "selector_weight_cap": 2.0,
    "recent_days": 5,
}


def evidence(store, now, kind="prospective"):
    return [
        dict(r)
        for r in store.db.execute(
            "SELECT p.*,o.raw_return,o.residual_return,o.resolved_at,a.alpha,a.calibration,a.expression "
            "FROM predictions p JOIN outcomes o USING(prediction_id) JOIN market_snapshots s USING(snapshot_id) "
            "JOIN attributions a USING(prediction_id) WHERE o.resolved_at<=? AND s.evidence_kind=? "
            "AND o.residual_return IS NOT NULL ORDER BY p.prediction_id",
            (now, kind),
        )
    ]


def metric(row):
    # Fixed normalized direction diagnostic; NOT executable PnL or leverage.
    value = row["direction"] * row["residual_return"] - (
        CONFIG["cost"] if row["direction"] else 0.0
    )
    return max(-CONFIG["clip"], min(CONFIG["clip"], value))


def clusters(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[iso(row["decision_time"])[:10]].append(row)
    return [
        (day, statistics.fmean(metric(r) for r in members), members)
        for day, members in sorted(grouped.items())
    ]


def estimate(rows, now):
    days = clusters(rows)
    weights = [
        2 ** (-(now - max(r["decision_time"] for r in members)) / 86400 / CONFIG["half_life_days"])
        for _, _, members in days
    ]
    total = sum(weights)
    if not total:
        return {
            "n": 0,
            "effective_n": 0.0,
            "mean_return": 0.0,
            "uncertainty": CONFIG["prior_daily_sigma"],
            "weight": 1.0,
            "calibration": 1.0,
            "degradation": 0.0,
        }
    shrink = total / (total + CONFIG["prior_days"])
    mean = sum(w * value for w, (_, value, _) in zip(weights, days, strict=True)) / total
    variance = (
        sum(w * (value - mean) ** 2 for w, (_, value, _) in zip(weights, days, strict=True)) / total
    )
    effective = total**2 / sum(w * w for w in weights)
    uncertainty = math.sqrt(
        (variance * total + CONFIG["prior_daily_sigma"] ** 2 * CONFIG["prior_days"])
        / (total + CONFIG["prior_days"])
        / (effective + CONFIG["prior_days"])
    )
    recent = statistics.fmean(v for _, v, _ in days[-CONFIG["recent_days"] :])
    degradation = max(0.0, mean - recent) * shrink
    expected = sum(abs(r["expected_return"]) for r in rows if r["direction"])
    realized = sum(max(0.0, r["direction"] * r["raw_return"]) for r in rows if r["direction"])
    ratio = min(2.0, max(0.25, realized / expected)) if expected else 1.0
    calibration = 1.0 + shrink * (ratio - 1.0)
    conservative = mean * shrink - uncertainty * shrink - degradation
    weight = min(CONFIG["selector_weight_cap"], max(0.1, 1.0 + conservative / 0.005))
    return {
        "n": len(days),
        "effective_n": effective,
        "mean_return": mean * shrink,
        "uncertainty": uncertainty,
        "weight": weight,
        "calibration": calibration,
        "degradation": degradation,
    }


def lessons(store, rows, now):
    groups = defaultdict(list)
    for row in rows:
        regime = json.loads(row["context"])["regime"]
        groups[
            (
                row["strategy_id"],
                row["strategy_version"],
                regime,
                json.loads(row["context"])["evidence_kind"],
            )
        ].append(row)
    for (strategy, version, regime, kind), members in groups.items():
        for scope, field, target, observation in (
            (
                "alpha",
                "alpha",
                "direction_error",
                "Directional errors under this version and regime; test stronger abstention",
            ),
            (
                "option_expression",
                "expression",
                "correct_thesis_poor_option_expression",
                "Correct direction accompanied losing executable-side long premium; test expression choice",
            ),
        ):
            informative = [
                r
                for r in members
                if r["direction"]
                and (
                    scope != "option_expression"
                    or r["expression"] != "option_evaluation_unavailable"
                )
            ]
            if not informative:
                continue
            support = [r["prediction_id"] for r in informative if r[field] == target]
            contradiction = [r["prediction_id"] for r in informative if r[field] != target]
            # One daily cluster per evidence unit, not one vote per correlated symbol.
            daily = defaultdict(list)
            for r in informative:
                daily[iso(r["decision_time"])[:10]].append(r[field] == target)
            n = len(daily)
            successes = sum(statistics.fmean(values) for values in daily.values())
            confidence = (successes + 1) / (n + 2)
            lesson_id = identity([scope, strategy, version, regime, kind])
            previous = store.db.execute(
                "SELECT * FROM lessons WHERE lesson_id=? ORDER BY revision DESC LIMIT 1",
                (lesson_id,),
            ).fetchone()
            if previous and previous["status"] == "retired":
                continue
            status = (
                "supported"
                if n >= 10 and confidence >= 0.7
                else (
                    "weakened"
                    if previous
                    and previous["status"] in {"supported", "weakened"}
                    and confidence < 0.6
                    else "provisional"
                )
            )
            store.insert(
                "lessons",
                {
                    "lesson_id": lesson_id,
                    "revision": previous["revision"] + 1 if previous else 1,
                    "scope": scope,
                    "condition": canonical(
                        {
                            "strategy_id": strategy,
                            "version": version,
                            "regime": regime,
                            "evidence_kind": kind,
                        }
                    ),
                    "observation": observation,
                    "evidence_count": n,
                    "supporting_events": canonical(support),
                    "contradicting_events": canonical(contradiction),
                    "status": status,
                    "confidence": confidence,
                    "created_at": previous["created_at"] if previous else now,
                    "updated_at": now,
                },
            )


def retire_lesson(store, lesson_id, now, reason):
    previous = store.db.execute(
        "SELECT * FROM lessons WHERE lesson_id=? ORDER BY revision DESC LIMIT 1", (lesson_id,)
    ).fetchone()
    if previous is None or not reason:
        raise ValueError("lesson retirement needs an existing lesson and explicit reason")
    row = dict(previous)
    row.update(
        revision=row["revision"] + 1,
        status="retired",
        updated_at=now,
        observation=row["observation"] + "; retirement reason: " + reason,
    )
    with store.db:
        store.insert("lessons", row)


def learn_daily(store, now, kind="prospective"):
    if kind not in {"prospective", "synthetic", "replay"}:
        raise ValueError("unknown learning evidence pool")
    attribute(store, now)
    rows = evidence(store, now, kind)
    digest = identity([ALGORITHM, CONFIG, kind, [r["prediction_id"] for r in rows]])
    old = store.db.execute(
        "SELECT run_id FROM learning_runs WHERE evidence_hash=?", (digest,)
    ).fetchone()
    if old:
        return {"status": "unchanged", "run_id": old[0]}
    run_id = identity([digest, now])
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["strategy_id"], row["strategy_version"])].append(row)
    with store.db:
        store.insert(
            "learning_runs",
            {
                "run_id": run_id,
                "asof": now,
                "evidence_hash": digest,
                "algorithm": ALGORITHM,
                "configuration": canonical({**CONFIG, "evidence_kind": kind}),
            },
        )
        for (strategy, version), members in grouped.items():
            state = estimate(members, now)
            store.insert(
                "strategy_scores",
                {"run_id": run_id, "strategy_id": strategy, "version": version, **state},
            )
            regimes = defaultdict(list)
            for row in members:
                regimes[json.loads(row["context"])["regime"]].append(row)
            for regime, subset in regimes.items():
                result = estimate(subset, now)
                store.insert(
                    "regime_scores",
                    {
                        "run_id": run_id,
                        "strategy_id": strategy,
                        "version": version,
                        "regime": regime,
                        "n": result["n"],
                        "mean_return": result["mean_return"],
                        "compatibility": result["weight"],
                    },
                )
        lessons(store, rows, now)
    return {
        "status": "updated",
        "run_id": run_id,
        "versions": len(grouped),
        "observations": len(rows),
    }
