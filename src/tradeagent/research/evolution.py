"""Versioned challengers with fixed prospective tests, daily pairing and alpha spending."""

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from ..strategy import validate_params
from .domain import canonical, identity, iso
from .learning import metric

PLAN = {
    "metric": "clipped_directional_residual_less_10bp_v1",
    "min_days": 60,
    "clip": 0.02,
    "minimum_improvement": 0.001,
    "maximum_calendar_days": 180,
    "comparison": "paired symbol/decision/horizon; daily cluster mean; first 60 days",
    "test": "single fixed look; Hoeffding bounded-difference screen",
    "independence_caveat": "daily clustering does not establish independent market samples",
}


def propose(store, now, proposal=None, kind="prospective"):
    if kind not in {"prospective", "synthetic", "replay"}:
        raise ValueError("unknown challenger evidence pool")
    week = datetime.fromtimestamp(now, timezone.utc).strftime("%G-W%V")
    parents = store.db.execute(
        "SELECT v.* FROM strategies s JOIN strategy_versions v "
        "ON s.strategy_id=v.strategy_id AND s.champion_version=v.version "
        "WHERE s.enabled=1 AND v.role!='control' ORDER BY s.strategy_id"
    ).fetchall()
    created = []
    for parent in parents:
        if proposal and parent["strategy_id"] != proposal["strategy_id"]:
            continue
        pending = store.db.execute(
            "SELECT 1 FROM mutations m LEFT JOIN promotions p USING(mutation_id) "
            "LEFT JOIN rejections r USING(mutation_id) WHERE m.strategy_id=? "
            "AND p.mutation_id IS NULL AND r.mutation_id IS NULL",
            (parent["strategy_id"],),
        ).fetchone()
        if pending:
            continue
        params = json.loads(parent["params"])
        lesson = store.db.execute(
            "SELECT * FROM lessons WHERE scope='alpha' AND "
            "json_extract(condition,'$.strategy_id')=? AND json_extract(condition,'$.evidence_kind')=? "
            "ORDER BY updated_at DESC LIMIT 1",
            (parent["strategy_id"], kind),
        ).fetchone()
        if proposal:
            if proposal["parent_version"] != parent["version"]:
                raise ValueError("agent proposal parent is no longer the incumbent")
            params = proposal["params"]
            hypothesis = proposal["hypothesis"]
            if not isinstance(hypothesis, str) or not hypothesis.strip():
                raise ValueError("challenger needs an explicit hypothesis")
        else:
            params["threshold"] = min(0.05, params["threshold"] * 1.25)
            hypothesis = "A stronger abstention threshold may reduce noise and direction errors; requires paired prospective evidence"
        validate_params(params)
        if params == json.loads(parent["params"]):
            continue
        mutation_id = identity([parent["strategy_id"], parent["version"], week, kind])
        if store.db.execute(
            "SELECT 1 FROM mutations WHERE mutation_id=?", (mutation_id,)
        ).fetchone():
            continue
        attempt = store.db.execute("SELECT COUNT(*) FROM mutations").fetchone()[0] + 1
        plan = {
            **PLAN,
            "alpha": 0.05 / (attempt * (attempt + 1)),
            "attempt": attempt,
            "evidence_kind": kind,
            "prospective_after": now,
            "evaluation_deadline": now + PLAN["maximum_calendar_days"] * 86400,
        }
        version = "challenger-" + mutation_id[:12]
        with store.db:
            # Begin before register so registration and mutation share a transaction.
            store.db.execute("BEGIN IMMEDIATE")
            store.register(
                parent["strategy_id"],
                version,
                parent["family"],
                params,
                now,
                parent=parent["version"],
                role="challenger",
            )
            store.insert(
                "mutations",
                {
                    "mutation_id": mutation_id,
                    "strategy_id": parent["strategy_id"],
                    "parent_version": parent["version"],
                    "challenger_version": version,
                    "hypothesis": hypothesis,
                    "lesson_id": lesson["lesson_id"] if lesson else None,
                    "created_at": now,
                    "evaluation_plan": canonical(plan),
                    "replay_status": "parameter_bounds_sanity_passed; historical replay not used as proof",
                },
            )
        created.append(mutation_id)
    if proposal and not created:
        raise ValueError("proposal has no eligible incumbent or an active/duplicate challenger")
    return created


def paired(store, mutation, plan, now):
    rows = store.db.execute(
        "SELECT p.*,o.residual_return,s.evidence_kind FROM predictions p JOIN outcomes o USING(prediction_id) "
        "JOIN market_snapshots s USING(snapshot_id) WHERE p.strategy_id=? "
        "AND p.strategy_version IN (?,?) AND p.decision_time>? AND p.decision_time<=? "
        "AND o.resolved_at<=? AND o.residual_return IS NOT NULL AND s.evidence_kind=?",
        (
            mutation["strategy_id"],
            mutation["parent_version"],
            mutation["challenger_version"],
            plan["prospective_after"],
            plan["evaluation_deadline"],
            now,
            plan["evidence_kind"],
        ),
    ).fetchall()
    matched = defaultdict(dict)
    for row in rows:
        matched[(row["symbol"], row["decision_time"], row["horizon"])][row["strategy_version"]] = (
            dict(row)
        )
    daily = defaultdict(list)
    for pair in matched.values():
        if mutation["parent_version"] in pair and mutation["challenger_version"] in pair:
            parent, child = pair[mutation["parent_version"]], pair[mutation["challenger_version"]]
            daily[iso(parent["decision_time"])[:10]].append(
                (metric(child) - metric(parent), parent["prediction_id"], child["prediction_id"])
            )
    return [
        (day, statistics.fmean(v[0] for v in values), values)
        for day, values in sorted(daily.items())
    ][: plan["min_days"]]


def evaluate(store, now):
    reports = []
    mutations = store.db.execute(
        "SELECT m.* FROM mutations m LEFT JOIN promotions p USING(mutation_id) "
        "LEFT JOIN rejections r USING(mutation_id) WHERE p.mutation_id IS NULL AND r.mutation_id IS NULL"
    ).fetchall()
    for mutation in mutations:
        plan = json.loads(mutation["evaluation_plan"])
        days = paired(store, mutation, plan, now)
        digest = identity([plan, days, now >= plan["evaluation_deadline"]])
        old = store.db.execute(
            "SELECT * FROM challenger_evaluations WHERE mutation_id=? AND evidence_hash=?",
            (mutation["mutation_id"], digest),
        ).fetchone()
        if old:
            reports.append(dict(old))
            continue
        n = len(days)
        mean = statistics.fmean(v for _, v, _ in days) if days else 0.0
        # Difference of two scores in [-clip,clip] has total range 4*clip.
        radius = (
            4 * plan["clip"] * math.sqrt(math.log(2 / plan["alpha"]) / (2 * n))
            if n
            else 4 * plan["clip"]
        )
        decision = "continue_testing"
        incumbent = store.db.execute(
            "SELECT champion_version FROM strategies WHERE strategy_id=?",
            (mutation["strategy_id"],),
        ).fetchone()[0]
        if n >= plan["min_days"] or now >= plan["evaluation_deadline"]:
            decision = (
                "promote"
                if (
                    n >= plan["min_days"]
                    and mean - radius > plan["minimum_improvement"]
                    and incumbent == mutation["parent_version"]
                    and plan["evidence_kind"] == "prospective"
                )
                else "reject"
            )
        report = {
            "mutation_id": mutation["mutation_id"],
            "evidence_hash": digest,
            "evaluated_at": now,
            "n": n,
            "mean_delta": mean,
            "lower_bound": mean - radius,
            "upper_bound": mean + radius,
            "decision": decision,
            "metadata": canonical(
                {
                    "plan": plan,
                    "paired_days": days,
                    "repeated_reads_are_not_new_observations": True,
                    "synthetic_evidence_cannot_promote": True,
                }
            ),
        }
        with store.db:
            store.insert("challenger_evaluations", report)
            if decision == "promote":
                store.insert(
                    "promotions",
                    {
                        "mutation_id": mutation["mutation_id"],
                        "strategy_id": mutation["strategy_id"],
                        "parent_version": mutation["parent_version"],
                        "challenger_version": mutation["challenger_version"],
                        "created_at": now,
                        "evidence_hash": digest,
                    },
                )
                store.db.execute(
                    "UPDATE strategies SET champion_version=? WHERE strategy_id=? AND champion_version=?",
                    (
                        mutation["challenger_version"],
                        mutation["strategy_id"],
                        mutation["parent_version"],
                    ),
                )
            elif decision == "reject":
                store.insert(
                    "rejections",
                    {
                        "mutation_id": mutation["mutation_id"],
                        "created_at": now,
                        "evidence_hash": digest,
                        "reason": "fixed test failed, expired, synthetic-only, or incumbent changed",
                    },
                )
        reports.append(report)
    return reports


def evolve_weekly(store, now, proposal=None, kind="prospective"):
    reports = evaluate(store, now)
    created = propose(store, now, proposal, kind)
    return {"created": created, "evaluations": reports}


def retire(store, strategy_id, version, now, reason):
    if (
        not reason
        or not store.db.execute(
            "SELECT 1 FROM strategy_versions WHERE strategy_id=? AND version=?",
            (strategy_id, version),
        ).fetchone()
    ):
        raise ValueError("retirement needs a durable version and reason")
    with store.db:
        store.insert(
            "retirements",
            {"strategy_id": strategy_id, "version": version, "created_at": now, "reason": reason},
        )
        store.db.execute(
            "UPDATE strategies SET enabled=0 WHERE strategy_id=? AND champion_version=?",
            (strategy_id, version),
        )
