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
from .evidence_cohorts import POLICY, comparison_context
from .opportunities import research_snapshot
from .store import Experience
from .tradeplan import (
    COSTS,
    EVIDENCE_GATED,
    EXPERIMENTAL,
    build_experimental_plan,
    build_plan,
    plan_dict,
    plan_from_dict,
)

SKILL_VERSION = "trade-opportunity-analyst-v4"
ARMS = ("quant_only", "codex_only", "quant_codex")


def research_support(candidate, assessment, now):
    """Price action can support a thesis without news; event-only claims need proof."""
    event = candidate.get("event_evidence") or {}
    event_only = set(candidate["categories"]) == {"verified_event"}
    hypothesis = (assessment or {}).get(
        "hypothesis_type", "event_driven" if event_only else "price_action"
    )
    needs_event = hypothesis == "event_driven"
    dated_source = bool(
        assessment
        and any(
            s["role"] == "supporting"
            and s["published_at"] is not None
            and 0 <= now - timestamp(s["published_at"]) <= 86400
            for s in assessment["sources"]
        )
    )
    verified_event = bool(
        event.get("verified")
        and event.get("symbol") == candidate["symbol"]
        and event.get("published_at") is not None
        and event.get("observed_at") is not None
        and 0 <= now - timestamp(event["published_at"]) <= 86400
        and timestamp(event["published_at"]) <= timestamp(event["observed_at"]) <= now
    )
    reasons = []
    if not assessment:
        reasons.append("Codex abstained: no frozen assessment")
    elif assessment["stance"] != "long":
        reasons.append(
            "Codex challenged the thesis"
            if assessment["stance"] == "avoid"
            else "Codex abstained: watch stance"
        )
    if needs_event and not (dated_source or verified_event):
        reasons.append(
            "event-driven thesis lacks verified supporting evidence published within 24 hours"
        )
    return {
        "hypothesis_type": hypothesis,
        "news_required": needs_event,
        "fresh_event_evidence": dated_source or verified_event,
        "supported": not reasons,
        "reasons": reasons,
    }


def scan_failure(report):
    """A collection failure is terminal evidence, never an economic abstention."""
    if (
        report.get("status") != "INCOMPLETE"
        and report.get("source") not in (None, "", "unavailable")
        and report.get("evidence_kind") not in (None, "", "unavailable")
    ):
        return None
    limitations = report.get("limitations") or ["market-data source unavailable"]
    return {
        "schema_version": 1,
        "status": "INCOMPLETE",
        "decision": "NO_TRADE",
        "failure_kind": "MARKET_DATA_UNAVAILABLE",
        "failure_id": report["scan_id"],
        "invocation_id": report["scan_id"],
        "scan_id": report["scan_id"],
        "recorded_at": report["decision_time"],
        "reason": "; ".join(limitations),
        "limitations": limitations,
        "orders_submitted": 0,
        "submission_status": "NOT_SUBMITTED",
        "execution_invoked": False,
    }


class DailyResearch:
    """Additional research records in Experience; no execution state or new scheduler."""

    def __init__(self, directory):
        self.experience = Experience(Path(directory))
        self.db = self.experience.db
        for name in (
            "scans",
            "assessments",
            "decisions",
            "outcomes",
            "executions",
            "symbol_outcomes",
            "resolution_failures",
        ):
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
        if kind not in {
            "scans",
            "assessments",
            "decisions",
            "outcomes",
            "executions",
            "symbol_outcomes",
            "resolution_failures",
        }:
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
        if kind not in {
            "scans",
            "assessments",
            "decisions",
            "outcomes",
            "executions",
            "symbol_outcomes",
            "resolution_failures",
        }:
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
        if set(item) not in (required, required | {"hypothesis_type"}):
            raise ValueError(
                "assessment must match the documented schema; no subjective sizing or invented forecast fields"
            )
        if item.get("hypothesis_type", "price_action") not in {"price_action", "event_driven"}:
            raise ValueError("hypothesis_type must be price_action or event_driven")
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
        # Rehydrate comparability from ORIGINAL frozen forecasts/plans for older
        # outcomes. Do not update any decision or persisted economic row.
        frozen = {
            p["prediction_id"]: comparison_context(p["features"], c["outcome_costs"])
            for d in self.records("decisions")
            for c in d.get("quantitative_observations", d["ranked_candidates"])
            for p in c["predictions"]
        }
        invalid_decisions = {r["decision_id"] for r in self.records("resolution_failures")}
        rows = [
            {
                **r,
                "comparison_context": r.get(
                    "comparison_context", frozen.get(r["prediction_id"], {})
                ),
            }
            for o in self.records("outcomes") + self.records("symbol_outcomes")
            if o["decision_id"] not in invalid_decisions
            for r in o.get("economic_rows", [])
        ]
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
                    "benchmark": context.get("benchmark", "SPY"),
                    "decision_offset": features.get("decision_offset"),
                    "entry_delay_seconds": features.get("entry_delay_seconds", 0),
                    "regime": context.get("regime"),
                    "comparison_context": comparison_context(features),
                    "active": r["direction"] > 0,
                    "gross": r["raw_return"],
                }
            )
        # A minute is a predefined sampling slot. Choose its FIRST frozen forecast
        # before looking at outcomes, even if that first forecast is still pending.
        first = {}
        for raw in self.db.execute("SELECT payload FROM opportunity_decisions ORDER BY rowid"):
            d = json.loads(raw[0])
            for c in d.get("quantitative_observations", []):
                for p in c["predictions"]:
                    first.setdefault(p["features"]["sampling_key"], p["prediction_id"])
        unique = {}
        for row in rows:
            key = row.get("sampling_key")
            if key and first.get(key, row["prediction_id"]) != row["prediction_id"]:
                continue
            old = unique.get(row["prediction_id"])
            if old is not None and old != row:
                raise ValueError("conflicting frozen economic observation")
            unique[row["prediction_id"]] = row
        return list(unique.values())

    def terminal_windows(self, now):
        """Append terminal timing evidence; never edit a frozen decision/outcome."""
        failed = {r["decision_id"]: r for r in self.records("resolution_failures")}
        scans = {r["scan_id"]: r for r in self.records("scans")}
        for d in self.records("decisions"):
            did = d["decision_id"]
            if did in failed:
                continue
            bounds = (d.get("session_open"), d.get("session_close"))
            if None in bounds:
                path = scans.get(d["scan_id"], {}).get("market_file")
                if path and Path(path).resolve().is_relative_to(
                    self.experience.directory.resolve()
                ):
                    try:
                        capture = json.loads(Path(path).read_text())
                        bounds = (capture["session_open"], capture["session_close"])
                    except (OSError, KeyError, ValueError):
                        pass
            if None in bounds and d["evidence_kind"] == "prospective":
                from datetime import datetime
                from zoneinfo import ZoneInfo

                from ..calendar import session_bounds

                bounds = session_bounds(
                    datetime.fromtimestamp(d["decision_time"], ZoneInfo("America/New_York")).date()
                ) or (None, None)
            declared_invalid = d.get("research_window", {}).get("valid") is False
            explicit_old_failure = any(
                "forecast horizon exceeds regular session" in p["rejection_reasons"]
                for c in d["ranked_candidates"]
                for p in c["economic_plans"]
            )
            invalid = declared_invalid or explicit_old_failure
            if None not in bounds and d.get("outcome_entry_time") is not None:
                invalid |= (
                    not bounds[0]
                    <= d["decision_time"]
                    <= d["outcome_entry_time"]
                    < d["outcome_exit_time"]
                    <= bounds[1]
                )
            if not invalid:
                continue
            has_forecasts = any(
                c["predictions"] for c in d.get("quantitative_observations", d["ranked_candidates"])
            )
            item = {
                "decision_id": did,
                "recorded_at": now,
                "status": "UNRESOLVABLE_SESSION_WINDOW"
                if has_forecasts
                else "NO_FORECAST_SESSION_WINDOW",
                "reason": "frozen entry/endpoint cannot occur within its regular session; not missing market data",
                "session_open": bounds[0],
                "session_close": bounds[1],
                "frozen_outcome_entry_time": d.get("outcome_entry_time"),
                "frozen_outcome_exit_time": d.get("outcome_exit_time"),
                "economic_rows": [],
            }
            self.save("resolution_failures", item, did, now)
            failed[did] = item
        return list(failed.values())

    def pending_sessions(self, now):
        """Only genuinely prospective, already matured forecasts need later history."""
        done = {o["decision_id"] for o in self.records("outcomes")}
        terminal = {r["decision_id"] for r in self.terminal_windows(now)}
        symbol_done = {(o["decision_id"], o["symbol"]) for o in self.records("symbol_outcomes")}
        sessions = {}
        for d in self.records("decisions"):
            if (
                d["decision_id"] in terminal
                or d["evidence_kind"] != "prospective"
                or d.get("outcome_exit_time") is None
                or now < d["outcome_exit_time"]
            ):
                continue
            coverage = d.get("quantitative_observations", d["ranked_candidates"])
            pending = [
                c["symbol"]
                for c in coverage
                if c["predictions"] and (d["decision_id"], c["symbol"]) not in symbol_done
            ]
            if "quantitative_observations" not in d and d["decision_id"] in done:
                continue
            if pending:
                from .domain import iso

                day = iso(d["decision_time"])[:10]
                group = sessions.setdefault((d["source"], day), set())
                group.update(pending)
                group.update(
                    p["context"].get("benchmark", "SPY")
                    for c in coverage
                    if c["symbol"] in pending
                    for p in c["predictions"]
                )
        return [
            {"source": source, "session_date": day, "symbols": sorted(symbols)}
            for (source, day), symbols in sorted(sessions.items())
        ]

    def decide(
        self,
        report,
        dataset,
        *,
        now,
        holding_seconds=3600,
        delay_seconds=300,
        execution_policy=EVIDENCE_GATED,
    ):
        failure = scan_failure(report)
        if failure:
            return failure
        if execution_policy not in {EVIDENCE_GATED, EXPERIMENTAL}:
            raise ValueError("unknown execution policy")
        if execution_policy == EXPERIMENTAL and report.get("execution_universe") is None:
            raise ValueError("Experimental LIVE requires an owner-aligned scan")
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
        # Newly obtained data can resolve old forecasts, never backfill new ones.
        self.resolve(dataset, now)
        rows, coverage = self.prior_rows(), []
        entry_endpoint = math.ceil((now + delay_seconds) / 60) * 60
        exit_endpoint = math.ceil((now + delay_seconds + holding_seconds) / 60) * 60
        window_valid = (
            dataset["session_open"]
            <= now
            <= entry_endpoint
            < exit_endpoint
            <= dataset["session_close"]
            and entry_endpoint < now + delay_seconds + 120 < now + delay_seconds + holding_seconds
        )
        shortlisted = {c["symbol"] for c in report["candidates"]}
        for candidate in report["candidate_pool"]:
            symbol = candidate["symbol"]
            research = assessments.get(symbol)
            predictions, plans, experimental_plans, errors = [], [], [], []
            try:
                if not window_valid:
                    raise ValueError(
                        "no realizable regular-session entry/exit window; no forecast created"
                    )
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
                                "sampling_key": identity(
                                    [
                                        "opportunity-observation-minute-v1",
                                        symbol,
                                        prediction.strategy_id,
                                        prediction.strategy_version,
                                        dataset["source"],
                                        dataset["evidence_kind"],
                                        prediction.context.value["benchmark"],
                                        holding_seconds + delay_seconds,
                                        delay_seconds,
                                        int(now // 60),
                                    ]
                                ),
                                "decision_offset": int((now - dataset["session_open"]) // 60),
                                "observed_spread_bps": (snapshot.ask - snapshot.bid)
                                / ((snapshot.ask + snapshot.bid) / 2)
                                * 10000,
                            }
                        ),
                    )
                    plan = build_plan(prediction, snapshot, rows, evidence_policy=POLICY)
                    if execution_policy == EXPERIMENTAL:
                        experimental_plans.append(
                            plan_dict(
                                build_experimental_plan(
                                    prediction,
                                    snapshot,
                                    rows,
                                    session_open=dataset["session_open"],
                                    session_close=dataset["session_close"],
                                )
                            )
                        )
                    predictions.append(prediction_dict(prediction))
                    plans.append(plan_dict(plan))
            except (ValueError, KeyError, TypeError) as error:
                errors.append(str(error))
            approved = [p for p in plans if p["decision"]["kind"] == "UNDERLYING"]
            best = max(approved, key=lambda p: p["economics"]["lower_net_estimate"], default=None)
            support = research_support(candidate, research, now)
            combined = best if support["supported"] else None
            coverage.append(
                {
                    "symbol": symbol,
                    "scanner": candidate,
                    "research": research,
                    "codex_support": support,
                    "predictions": predictions,
                    "economic_plans": plans,
                    "outcome_costs": plans[0]["economics"]
                    if plans
                    else {
                        "base_side_cost": COSTS["base"].side,
                        "stress_side_cost": COSTS["stress"].side,
                        "provenance": "fixed research cost assumptions; contemporaneous spread unavailable",
                    },
                    "experimental_plans": experimental_plans,
                    "experimental_plan": max(
                        (p for p in experimental_plans if p["decision"]["kind"] == "UNDERLYING"),
                        key=lambda p: (
                            p["forecast"]["raw_expected_return"],
                            p["forecast"]["strategy"],
                        ),
                        default=None,
                    ),
                    "quant_plan": best,
                    "combined_plan": combined,
                    "limitations": errors,
                    "rejection_reasons": errors
                    + sorted({reason for p in plans for reason in p["rejection_reasons"]})
                    + support["reasons"],
                    "entry_condition": "fresh US equity quote, original price cap, entry window and existing owner risk checks",
                    "invalidation": research["invalidation"]
                    if research
                    else "no adequate evidence or failed freshness/cost/risk condition",
                    "exit_logic": "time exit at forecast endpoint, regular-session deadline and existing execution recovery",
                }
            )
        candidates = [c for c in coverage if c["symbol"] in shortlisted]
        authorized = report.get("execution_universe")
        if authorized is not None:
            candidates = [c for c in candidates if c["symbol"] in authorized]
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
            (r for r in candidates if r["codex_support"]["supported"]),
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
                "reason": "evidence-based Codex support plus measured prior net-edge gate"
                if name == "quant_codex" and plan
                else "positive prior cost gate"
                if plan
                else "AI directional hypothesis is uncalibrated and research-only"
                if name == "codex_only" and chosen
                else "insufficient supported net edge",
            }
        experimental = (
            max(
                (c for c in coverage if c["experimental_plan"] and c["symbol"] in authorized),
                key=lambda c: (
                    c["experimental_plan"]["forecast"]["raw_expected_return"],
                    c["symbol"],
                ),
                default=None,
            )
            if execution_policy == EXPERIMENTAL
            else None
        )
        experimental_arm = (
            {
                "symbol": experimental["symbol"] if experimental else None,
                "decision": "UNDERLYING" if experimental else "NO_TRADE",
                "plan": experimental["experimental_plan"] if experimental else None,
                "shadow_direction": 1 if experimental else 0,
                "research_only": bool(
                    experimental and experimental["experimental_plan"]["research_only"]
                ),
                "reason": "predefined quantitative long signal; profitability unestablished; no economic promotion"
                if experimental
                else "no valid predefined long signal in the authorized universe/session",
            }
            if execution_policy == EXPERIMENTAL
            else None
        )
        selected = experimental_arm if experimental_arm is not None else arms["quant_codex"]
        result = {
            "schema_version": 1,
            "skill_version": SKILL_VERSION,
            "economic_evidence_policy": POLICY,
            "execution_policy": execution_policy,
            "experimental": experimental_arm,
            "session_open": dataset["session_open"],
            "session_close": dataset["session_close"],
            "research_window": {
                "valid": window_valid,
                "requested_horizon": holding_seconds + delay_seconds,
                "requested_exit_at": now + delay_seconds + holding_seconds,
            },
            "recorded_at": now,
            "decision_time": now,
            "scan_id": report["scan_id"],
            "market_observed_at": dataset["observed_at"],
            "source": dataset["source"],
            "evidence_kind": dataset["evidence_kind"],
            "holding_seconds": holding_seconds,
            "entry_after": now + delay_seconds if window_valid else None,
            "entry_deadline": now + delay_seconds + 120 if window_valid else None,
            "exit_at": now + delay_seconds + holding_seconds if window_valid else None,
            "outcome_entry_time": entry_endpoint if window_valid else None,
            "outcome_exit_time": exit_endpoint if window_valid else None,
            "candidate_pool": report["candidate_pool"],
            "ranked_candidates": candidates,
            "quantitative_observations": coverage,
            "research_universe": report["universe"],
            "execution_universe": report.get("execution_universe"),
            "excluded_live_opportunities": report.get("excluded_live_opportunities", []),
            "sampling_policy": "first frozen forecast per symbol/strategy/version/source/pool/benchmark/horizon/delay/minute",
            "comparisons": arms,
            "final_decision": selected["decision"],
            "selected_plan": selected["plan"],
            "decision_status": "AWAITING_ENTRY_WINDOW" if selected["plan"] else "NO_TRADE",
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
        terminal = self.terminal_windows(now)
        terminal_ids = {r["decision_id"] for r in terminal}
        resolved, pending, symbol_resolved = [], [], []
        frozen_symbols = {
            (o["decision_id"], o["symbol"]): o for o in self.records("symbol_outcomes")
        }
        for decision in self.records("decisions"):
            if decision["decision_id"] in terminal_ids:
                continue
            if decision["decision_id"] in done and "quantitative_observations" not in decision:
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
            for candidate in decision.get(
                "quantitative_observations", decision["ranked_candidates"]
            ):
                symbol = candidate["symbol"]
                saved = frozen_symbols.get((decision["decision_id"], symbol))
                if saved:
                    observed[symbol] = saved["modeled_outcome"]
                    economic.extend(saved["economic_rows"])
                    continue
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
                benchmark = (
                    candidate["predictions"][0]["context"].get("benchmark", "SPY")
                    if candidate["predictions"]
                    else "SPY"
                )
                spy = histories.get(benchmark, [])
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
                    "benchmark": benchmark,
                    "benchmark_gross": market_gross,
                    "spy_gross": market_gross if benchmark == "SPY" else None,
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
                symbol_economic = []
                for prediction in candidate["predictions"]:
                    symbol_economic.append(
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
                            "benchmark": prediction["context"].get("benchmark", "SPY"),
                            "decision_offset": prediction["features"].get("decision_offset"),
                            "entry_delay_seconds": prediction["features"].get(
                                "entry_delay_seconds", 0
                            ),
                            "regime": prediction["context"].get("regime"),
                            "comparison_context": comparison_context(prediction["features"], costs),
                            "sampling_key": prediction["features"].get("sampling_key"),
                            "active": prediction["direction"] > 0,
                            "gross": gross,
                        }
                    )
                if candidate["predictions"] and "quantitative_observations" in decision:
                    item = {
                        "decision_id": decision["decision_id"],
                        "symbol": symbol,
                        "resolved_at": now,
                        "modeled_outcome": observed[symbol],
                        "economic_rows": symbol_economic,
                        "source": decision["source"],
                        "evidence_kind": decision["evidence_kind"],
                        "observation_evidence_kind": dataset["evidence_kind"],
                        "capture_id": identity(dataset),
                        "capture_observed_at": dataset["observed_at"],
                    }
                    self.save(
                        "symbol_outcomes", item, identity([decision["decision_id"], symbol]), now
                    )
                    symbol_resolved.append(item)
                    frozen_symbols[(decision["decision_id"], symbol)] = item
                economic.extend(symbol_economic)
            if decision["decision_id"] in done:
                continue
            required = {r["symbol"] for r in decision["ranked_candidates"] if r["predictions"]}
            required |= {
                arm["symbol"] for arm in decision["comparisons"].values() if arm["shadow_direction"]
            }
            if decision.get("experimental") and decision["experimental"]["shadow_direction"]:
                required.add(decision["experimental"]["symbol"])
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
                "experimental": observed.get(decision["experimental"]["symbol"])
                if decision.get("experimental") and decision["experimental"]["shadow_direction"]
                else None,
                "execution_policy": decision.get("execution_policy", EVIDENCE_GATED),
                "economic_rows": [] if "quantitative_observations" in decision else economic,
                "evidence_kind": decision["evidence_kind"],
                "observation_evidence_kind": dataset["evidence_kind"],
                "source": decision["source"],
            }
            self.save("outcomes", item, decision["decision_id"], now)
            resolved.append(item)
        return {
            "resolved": resolved,
            "symbol_resolved": symbol_resolved,
            "pending": pending,
            "terminal_unresolvable": terminal,
            "pending_sessions": self.pending_sessions(now),
            "orders_submitted": 0,
        }

    def performance(self):
        all_outcomes = self.records("outcomes")
        terminal_ids = {r["decision_id"] for r in self.records("resolution_failures")}
        outcomes = [
            o
            for o in all_outcomes
            if o.get("execution_policy", EVIDENCE_GATED) == EVIDENCE_GATED
            and o["decision_id"] not in terminal_ids
        ]

        def paired_stats(subset):
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
            return {"arms": arm_stats, "paired_mean_net_minus_quant": paired}

        # Keep every append-only report, but count one actual lifecycle per decision
        # and arm. Recovery/duplicate-suppressed reports must not duplicate PnL.
        execution_reports = [
            json.loads(row[0])
            for row in self.db.execute("SELECT payload FROM opportunity_executions ORDER BY rowid")
        ]
        actual = {(r["decision_id"], r["research_arm"]): r for r in execution_reports}
        decisions = {d["decision_id"]: d for d in self.records("decisions")}
        pools = {}
        for pool in sorted({o["evidence_kind"] for o in outcomes}):
            subset = [o for o in outcomes if o["evidence_kind"] == pool]
            protocols = {}
            for outcome in subset:
                decision = decisions[outcome["decision_id"]]
                protocol = (
                    decision.get("skill_version", "unspecified")
                    + "/"
                    + decision.get("economic_evidence_policy", "exact-v1")
                    + (
                        "/live-universe:" + identity(sorted(decision["execution_universe"]))[:12]
                        if decision.get("execution_universe") is not None
                        else "/research-universe"
                    )
                )
                protocols.setdefault(protocol, []).append(outcome)
            pools[pool] = {
                **paired_stats(subset),
                "mixed_protocols": len(protocols) > 1,
                "protocols": {p: paired_stats(rows) for p, rows in sorted(protocols.items())},
            }
        actual_performance = [
            {
                "decision_id": r["decision_id"],
                "research_arm": r["research_arm"],
                "execution_policy": decisions[r["decision_id"]].get(
                    "execution_policy", EVIDENCE_GATED
                ),
                "realized_pnl": r["report"].get("realized_pnl"),
                "entry_executed_notional": r["report"].get("entry_executed_notional"),
                "known_fees": r["report"].get("known_fees"),
                "verification": r["verification"],
                "engine_status": r["report"].get("status"),
                "submission_status": r["report"].get("submission_status"),
                "broker_order_count": r["report"].get("broker_order_count"),
                "bought": r["report"].get("bought"),
                "sold": r["report"].get("sold"),
                "final_positions": r["report"].get("final_positions"),
                "reconciliation_status": r["report"].get("reconciliation_status"),
                "execution_report_path": r.get("execution_report_path"),
            }
            for r in actual.values()
        ]
        return {
            "evidence_pools": pools,
            "pending_decisions": len(
                {d["decision_id"] for d in self.records("decisions")}
                - {o["decision_id"] for o in all_outcomes}
                - terminal_ids
            ),
            "terminal_unresolvable": self.records("resolution_failures"),
            "experimental_modeled_outcomes": [
                {
                    "decision_id": o["decision_id"],
                    "evidence_kind": o["evidence_kind"],
                    "modeled_outcome": o.get("experimental"),
                }
                for o in all_outcomes
                if o.get("execution_policy") == EXPERIMENTAL
                and o["decision_id"] not in terminal_ids
            ],
            "actual_execution_records": len(actual),
            "execution_report_records": len(execution_reports),
            "actual_performance": actual_performance,
            "actual_performance_by_policy": {
                policy: [r for r in actual_performance if r["execution_policy"] == policy]
                for policy in (EVIDENCE_GATED, EXPERIMENTAL)
            },
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
    policy = decision.get("execution_policy", EVIDENCE_GATED)
    if (mode == "experimental") != (policy == EXPERIMENTAL):
        raise ValueError("explicit execution mode must match frozen decision policy")
    if mode not in (*ARMS, "experimental") or mode == "codex_only":
        raise ValueError("uncalibrated Codex-only hypotheses cannot authorize execution")
    arm = decision["experimental"] if mode == "experimental" else decision["comparisons"][mode]
    if not arm["plan"]:
        raise ValueError("decision is NO_TRADE")
    plan = plan_from_dict(arm["plan"])
    if plan.execution_policy != policy:
        raise ValueError("selected plan policy differs from frozen decision")
    prediction = next(
        p
        for c in decision.get("quantitative_observations", decision["ranked_candidates"])
        for p in c["predictions"]
        if p["prediction_id"] == plan.decision.prediction_id
    )
    return plan, prediction_from_dict(prediction)
