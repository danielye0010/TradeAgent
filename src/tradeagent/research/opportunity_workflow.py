"""One interactive analyst, existing forecasts/economics and append-only daily memory."""

import json
import math
import statistics
import time
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import urlparse

from .alpha_signals import FAMILIES, AlphaStrategy
from .domain import Payload, Prediction, canonical, identity, timestamp
from .opportunities import research_snapshot
from .store import Experience
from .tradeplan import COSTS, build_plan, plan_dict, plan_from_dict

SKILL_VERSION = "trade-opportunity-analyst-v1"
ARMS = ("quant_only", "codex_only", "quant_codex")


class DailyResearch:
    """Additional research records in Experience; no execution state or new scheduler."""

    def __init__(self, directory):
        self.experience = Experience(Path(directory))
        self.db = self.experience.db
        for name in ("scans", "assessments", "decisions", "outcomes", "executions"):
            table = "opportunity_" + name
            self.db.execute(
                f"CREATE TABLE IF NOT EXISTS {table}(id TEXT PRIMARY KEY, recorded_at REAL NOT NULL, payload TEXT NOT NULL)"
            )
            for operation in ("UPDATE", "DELETE"):
                self.db.execute(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'append-only research'); END"
                )
            self.db.execute(
                f"CREATE TRIGGER IF NOT EXISTS no_replace_{table} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE id=NEW.id) BEGIN SELECT RAISE(ABORT,'append-only research'); END"
            )
        self.db.commit()

    def close(self):
        self.experience.close()

    def save(self, kind, item, key=None, now=None):
        if kind not in {"scans", "assessments", "decisions", "outcomes", "executions"}:
            raise ValueError("unknown daily research record")
        key = key or identity(item)
        encoded = canonical(item)
        old = self.db.execute(
            f"SELECT payload FROM opportunity_{kind} WHERE id=?", (key,)
        ).fetchone()
        if old:
            if old[0] != encoded:
                raise ValueError(
                    "decision evidence is frozen; start a new scan rather than rewriting history"
                )
            return key
        with self.db:
            self.db.execute(
                f"INSERT INTO opportunity_{kind} VALUES(?,?,?)",
                (key, time.time() if now is None else now, encoded),
            )
        return key

    def records(self, kind):
        if kind not in {"scans", "assessments", "decisions", "outcomes", "executions"}:
            raise ValueError("unknown daily research record")
        return [
            json.loads(r[0])
            for r in self.db.execute(
                f"SELECT payload FROM opportunity_{kind} ORDER BY recorded_at,id"
            )
        ]

    def scan(self, scan_id=None):
        values = self.records("scans")
        matches = [s for s in values if not scan_id or s["scan_id"] == scan_id]
        if not matches:
            raise ValueError("no saved scanner run; run opportunity scan first")
        return matches[-1]

    def assessment(self, item, now):
        required = {
            "scan_id",
            "symbol",
            "observed_at",
            "stance",
            "rank",
            "thesis",
            "catalyst",
            "priced_in",
            "contradictions",
            "invalidation",
            "sources",
            "model",
        }
        if set(item) != required:
            raise ValueError(
                "assessment must match the documented schema; no subjective sizing or invented forecast fields"
            )
        report = self.scan(item["scan_id"])
        if item["symbol"] not in {r["symbol"] for r in report["candidates"]}:
            raise ValueError("assess only a saved shortlisted candidate")
        observed = timestamp(item["observed_at"])
        if not report["decision_time"] <= observed <= now:
            raise ValueError("assessment observation is outside the actual research window")
        if (
            item["stance"] not in {"long", "watch", "avoid"}
            or type(item["rank"]) is not int
            or item["rank"] < 1
        ):
            raise ValueError("stance must be long/watch/avoid with a positive ordinal rank")
        if not isinstance(item["contradictions"], list) or not isinstance(item["sources"], list):
            raise ValueError("contradictions and sources must be lists")
        for name in ("thesis", "catalyst", "priced_in", "invalidation", "model"):
            if not isinstance(item[name], str) or not item[name].strip():
                raise ValueError("assessment text/model provenance missing")
        for source in item["sources"]:
            if set(source) != {"url", "title", "published_at", "observed_at", "claim", "role"}:
                raise ValueError(
                    "source needs publication and observation timestamps, claim and supporting/contradicting/context role"
                )
            if (
                urlparse(source["url"]).scheme != "https"
                or not source["claim"]
                or source["role"] not in {"supporting", "contradicting", "context"}
            ):
                raise ValueError("invalid cited source")
            receipt = timestamp(source["observed_at"])
            if not report["decision_time"] <= receipt <= observed:
                raise ValueError("source observation is outside this scan/research window")
            if source["published_at"] is not None and timestamp(source["published_at"]) > receipt:
                raise ValueError("future publication timestamp")
        return self.save("assessments", item, identity([item["scan_id"], item["symbol"]]), now)

    def prior_rows(self):
        rows = [r for o in self.records("outcomes") for r in o.get("economic_rows", [])]
        for r in self.db.execute(
            "SELECT p.*,o.raw_return,o.resolved_at FROM predictions p JOIN outcomes o USING(prediction_id)"
        ):
            context, features = json.loads(r["context"]), json.loads(r["features"])
            rows.append(
                {
                    "prediction_id": r["prediction_id"],
                    "strategy": r["strategy_id"],
                    "version": r["strategy_version"],
                    "symbol": r["symbol"],
                    "horizon": r["horizon"],
                    "decision_time": r["decision_time"],
                    "resolved_at": r["resolved_at"],
                    "evidence_kind": context["evidence_kind"],
                    "source": context["source"],
                    "decision_offset": features.get("decision_offset"),
                    "entry_delay_seconds": features.get("entry_delay_seconds", 0),
                    "regime": context.get("regime"),
                    "active": r["direction"] > 0,
                    "gross": r["raw_return"],
                }
            )
        return rows

    def decide(self, report, dataset, *, now, holding_seconds=3600, delay_seconds=300):
        if (
            type(holding_seconds) is not int
            or holding_seconds <= 0
            or type(delay_seconds) is not int
            or delay_seconds < 0
        ):
            raise ValueError("holding horizon positive and delay nonnegative")
        if holding_seconds + delay_seconds not in (1800, 2100, 3600, 3900, 7200, 7500):
            raise ValueError(
                "use a frozen Alpha horizon: holding plus delay must be 1800, 2100, 3600, 3900, 7200 or 7500 seconds"
            )
        if (
            now < report["decision_time"]
            or dataset["source"] != report["source"]
            or dataset["evidence_kind"] != report["evidence_kind"]
            or set(dataset.get("symbols", [])) != set(report["universe"])
        ):
            raise ValueError(
                "fresh evaluation must use the same frozen source, universe and evidence pool"
            )
        assessments = {
            r["symbol"]: r
            for r in self.records("assessments")
            if r["scan_id"] == report["scan_id"] and timestamp(r["observed_at"]) <= now
        }
        rows, candidates = self.prior_rows(), []
        for candidate in report["candidates"]:
            symbol = candidate["symbol"]
            research = assessments.get(symbol)
            predictions, plans, errors = [], [], []
            try:
                snapshot = research_snapshot(dataset, symbol, now)
                for family in FAMILIES:
                    prediction = AlphaStrategy(family, holding_seconds + delay_seconds).predict(
                        snapshot, now
                    )
                    prediction = replace(
                        prediction,
                        prediction_id=identity(
                            [prediction.prediction_id, "opportunity-delay-v1", delay_seconds]
                        ),
                        features=Payload.of(
                            {
                                **prediction.features.plain(),
                                "entry_delay_seconds": delay_seconds,
                                "decision_offset": int((now - dataset["session_open"]) // 60),
                            }
                        ),
                    )
                    plan = build_plan(prediction, snapshot, rows)
                    if plan.exit_at > dataset["session_close"]:
                        plan = replace(
                            plan,
                            decision=replace(
                                plan.decision,
                                kind="NO_TRADE",
                                instrument=None,
                                entry_limit=None,
                                reason="forecast horizon exceeds regular session",
                            ),
                            rejection_reasons=(
                                *plan.rejection_reasons,
                                "forecast horizon exceeds regular session",
                            ),
                        )
                    predictions.append(prediction_dict(prediction))
                    plans.append(plan_dict(plan))
            except (ValueError, KeyError, TypeError) as error:
                errors.append(str(error))
            approved = [p for p in plans if p["decision"]["kind"] == "UNDERLYING"]
            best = max(approved, key=lambda p: p["economics"]["lower_net_estimate"], default=None)
            source_ready = bool(
                research
                and any(
                    s["role"] == "supporting"
                    and s["published_at"] is not None
                    and 0 <= now - timestamp(s["published_at"]) <= 86400
                    for s in research["sources"]
                )
            )
            combined = best if research and research["stance"] == "long" and source_ready else None
            candidates.append(
                {
                    "symbol": symbol,
                    "scanner": candidate,
                    "research": research,
                    "predictions": predictions,
                    "economic_plans": plans,
                    "outcome_costs": plans[0]["economics"]
                    if plans
                    else {
                        "base_side_cost": COSTS["base"].side,
                        "stress_side_cost": COSTS["stress"].side,
                        "provenance": "fixed research cost assumptions; contemporaneous spread unavailable",
                    },
                    "quant_plan": best,
                    "combined_plan": combined,
                    "limitations": errors,
                    "rejection_reasons": errors
                    + sorted({reason for p in plans for reason in p["rejection_reasons"]})
                    + (
                        []
                        if source_ready
                        else ["no supporting Codex source published within 24 hours"]
                    ),
                    "entry_condition": "fresh US equity quote, original price cap, entry window and existing owner risk checks",
                    "invalidation": research["invalidation"]
                    if research
                    else "no adequate evidence or failed freshness/cost/risk condition",
                    "exit_logic": "time exit at forecast endpoint, regular-session deadline and existing execution recovery",
                }
            )
        quant = max(
            (r for r in candidates if r["quant_plan"]),
            key=lambda r: r["quant_plan"]["economics"]["lower_net_estimate"],
            default=None,
        )
        combined = min(
            (r for r in candidates if r["combined_plan"]),
            key=lambda r: (
                r["research"]["rank"],
                -r["combined_plan"]["economics"]["lower_net_estimate"],
            ),
            default=None,
        )
        ai = min(
            (r for r in candidates if r["research"] and r["research"]["stance"] == "long"),
            key=lambda r: r["research"]["rank"],
            default=None,
        )
        arms = {}
        for name, chosen in (("quant_only", quant), ("codex_only", ai), ("quant_codex", combined)):
            plan = (
                chosen.get("quant_plan" if name == "quant_only" else "combined_plan")
                if chosen and name != "codex_only"
                else None
            )
            arms[name] = {
                "symbol": chosen["symbol"] if chosen else None,
                "decision": plan["decision"]["kind"] if plan else "NO_TRADE",
                "plan": plan,
                "shadow_direction": 1 if chosen else 0,
                "research_only": name == "codex_only" or bool(plan and plan["research_only"]),
                "reason": "dated qualitative support plus measured prior net-edge gate"
                if name == "quant_codex" and plan
                else "positive prior cost gate"
                if plan
                else "AI directional hypothesis is uncalibrated and research-only"
                if name == "codex_only" and chosen
                else "insufficient supported net edge",
            }
        result = {
            "schema_version": 1,
            "skill_version": SKILL_VERSION,
            "recorded_at": now,
            "decision_time": now,
            "scan_id": report["scan_id"],
            "market_observed_at": dataset["observed_at"],
            "source": dataset["source"],
            "evidence_kind": dataset["evidence_kind"],
            "holding_seconds": holding_seconds,
            "entry_after": now + delay_seconds,
            "entry_deadline": now + delay_seconds + 120,
            "exit_at": now + delay_seconds + holding_seconds,
            "outcome_entry_time": math.ceil((now + delay_seconds) / 60) * 60,
            "outcome_exit_time": math.ceil((now + delay_seconds + holding_seconds) / 60) * 60,
            "candidate_pool": report["candidate_pool"],
            "ranked_candidates": candidates,
            "comparisons": arms,
            "final_decision": arms["quant_codex"]["decision"],
            "selected_plan": arms["quant_codex"]["plan"],
            "decision_status": "AWAITING_ENTRY_WINDOW"
            if arms["quant_codex"]["plan"]
            else "NO_TRADE",
            "orders_submitted": 0,
            "news_is_calibrated_return_prediction": False,
        }
        result["decision_id"] = identity(result)
        self.save("decisions", result, result["decision_id"], now)
        return result

    def decision(self, decision_id=None):
        values = [
            r
            for r in self.records("decisions")
            if not decision_id or r["decision_id"] == decision_id
        ]
        if not values:
            raise ValueError("no saved decision")
        return values[-1]

    def resolve(self, dataset, now):
        from .opportunities import grouped

        histories = grouped(dataset, now)
        done = {r["decision_id"] for r in self.records("outcomes")}
        resolved, pending = [], []
        for decision in self.records("decisions"):
            if decision["decision_id"] in done:
                continue
            if dataset["source"] != decision["source"] or (
                dataset["evidence_kind"] != decision["evidence_kind"]
                and not (
                    decision["evidence_kind"] == "prospective"
                    and dataset["evidence_kind"] == "historical_market"
                )
            ):
                pending.append(
                    {
                        "decision_id": decision["decision_id"],
                        "reason": "source/evidence pool mismatch",
                    }
                )
                continue
            observed, economic = {}, []
            for candidate in decision["ranked_candidates"]:
                symbol = candidate["symbol"]
                bars = histories.get(symbol, [])
                entry = next(
                    (b for b in bars if b.start == decision["outcome_entry_time"]),
                    None,
                )
                exit_bar = next(
                    (b for b in bars if b.end == decision["outcome_exit_time"]),
                    None,
                )
                if not entry or not exit_bar or exit_bar.start < entry.end:
                    continue
                path = [b for b in bars if entry.start <= b.start <= exit_bar.start]
                if any(a.end != b.start for a, b in zip(path, path[1:], strict=False)):
                    continue
                spy = histories.get("SPY", [])
                spy_path = [b for b in spy if entry.start <= b.start <= exit_bar.start]
                if [(b.start, b.end) for b in spy_path] != [(b.start, b.end) for b in path]:
                    continue
                market_gross = spy_path[-1].close / spy_path[0].open - 1
                gross = exit_bar.close / entry.open - 1
                costs = candidate.get("outcome_costs")
                if costs is None:
                    continue
                observed[symbol] = {
                    "gross": gross,
                    "spy_gross": market_gross,
                    "residual_gross": gross - market_gross,
                    "entry_price": entry.open,
                    "exit_price": exit_bar.close,
                    "entry_time": entry.start,
                    "exit_time": exit_bar.end,
                    "max_adverse": min(b.low / entry.open - 1 for b in path),
                    "base_net": (1 + gross)
                    * (1 - costs["base_side_cost"])
                    / (1 + costs["base_side_cost"])
                    - 1,
                    "stress_net": (1 + gross)
                    * (1 - costs["stress_side_cost"])
                    / (1 + costs["stress_side_cost"])
                    - 1,
                    "price_model": "first complete minute open in planned window; next complete endpoint close; frozen estimated round-trip costs, no broker fills",
                }
                for prediction in candidate["predictions"]:
                    economic.append(
                        {
                            "prediction_id": prediction["prediction_id"],
                            "strategy": prediction["strategy_id"],
                            "version": prediction["strategy_version"],
                            "symbol": symbol,
                            "horizon": prediction["horizon"],
                            "decision_time": prediction["decision_time"],
                            "resolved_at": now,
                            "evidence_kind": prediction["context"]["evidence_kind"],
                            "source": prediction["context"]["source"],
                            "decision_offset": prediction["features"].get("decision_offset"),
                            "entry_delay_seconds": prediction["features"].get(
                                "entry_delay_seconds", 0
                            ),
                            "regime": prediction["context"].get("regime"),
                            "active": prediction["direction"] > 0,
                            "gross": gross,
                        }
                    )
            required = {r["symbol"] for r in decision["ranked_candidates"] if r["predictions"]}
            required |= {
                arm["symbol"] for arm in decision["comparisons"].values() if arm["shadow_direction"]
            }
            if not required <= set(observed) or now < decision["exit_at"]:
                pending.append(
                    {
                        "decision_id": decision["decision_id"],
                        "reason": "missing completed synchronized candidate outcome prices",
                    }
                )
                continue
            arms = {
                name: observed.get(arm["symbol"])
                if arm["shadow_direction"]
                else {"gross": 0, "base_net": 0, "stress_net": 0}
                for name, arm in decision["comparisons"].items()
            }
            item = {
                "decision_id": decision["decision_id"],
                "resolved_at": now,
                "candidate_outcomes": observed,
                "comparisons": arms,
                "economic_rows": economic,
                "evidence_kind": decision["evidence_kind"],
                "observation_evidence_kind": dataset["evidence_kind"],
                "source": decision["source"],
            }
            self.save("outcomes", item, decision["decision_id"], now)
            resolved.append(item)
        return {"resolved": resolved, "pending": pending, "orders_submitted": 0}

    def performance(self):
        outcomes = self.records("outcomes")
        pools = {}
        for pool in sorted({o["evidence_kind"] for o in outcomes}):
            subset = [o for o in outcomes if o["evidence_kind"] == pool]
            arm_stats = {
                name: {
                    "decisions": len(subset),
                    "observed": sum(o["comparisons"][name] is not None for o in subset),
                    "mean_base_net": statistics.fmean(
                        o["comparisons"][name]["base_net"]
                        for o in subset
                        if o["comparisons"][name] is not None
                    )
                    if any(o["comparisons"][name] is not None for o in subset)
                    else None,
                }
                for name in ARMS
            }
            paired = {
                name: statistics.fmean(
                    o["comparisons"][name]["base_net"] - o["comparisons"]["quant_only"]["base_net"]
                    for o in subset
                )
                for name in ("codex_only", "quant_codex")
            }
            pools[pool] = {"arms": arm_stats, "paired_mean_net_minus_quant": paired}
        return {
            "evidence_pools": pools,
            "pending_decisions": len(self.records("decisions")) - len(outcomes),
            "actual_execution_records": len(self.records("executions")),
            "actual_performance": [
                {
                    "decision_id": r["decision_id"],
                    "research_arm": r["research_arm"],
                    "realized_pnl": r["report"].get("realized_pnl"),
                    "entry_executed_notional": r["report"].get("entry_executed_notional"),
                    "known_fees": r["report"].get("known_fees"),
                    "verification": r["verification"],
                }
                for r in self.records("executions")
            ],
            "interpretation": "paired fixed-pool/horizon/cost shadow comparisons; no established AI uplift or independence claim",
        }


def prediction_dict(prediction):
    return {
        **asdict(prediction),
        "features": prediction.features.plain(),
        "context": prediction.context.plain(),
        "distribution": prediction.distribution.plain(),
    }


def prediction_from_dict(value):
    value = dict(value)
    for key in ("features", "context", "distribution"):
        value[key] = Payload.of(value[key])
    return Prediction(**value)


def selected_execution(decision, mode="quant_codex"):
    if mode not in ARMS or mode == "codex_only":
        raise ValueError("uncalibrated Codex-only hypotheses cannot authorize execution")
    arm = decision["comparisons"][mode]
    if not arm["plan"]:
        raise ValueError("decision is NO_TRADE")
    plan = plan_from_dict(arm["plan"])
    prediction = next(
        p
        for c in decision["ranked_candidates"]
        for p in c["predictions"]
        if p["prediction_id"] == plan.decision.prediction_id
    )
    return plan, prediction_from_dict(prediction)
