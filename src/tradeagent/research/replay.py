"""Thin chronological commissioning driver for the frozen research pipeline."""

import json
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars

from ..strategy import implementation_hash
from .domain import MarketSnapshot, canonical, identity, iso
from .evolution import evaluate, evolve_weekly
from .history_data import load_history
from .lab import scan, seed
from .learning import learn_daily, metric
from .outcomes import resolve
from .store import Experience

NY = ZoneInfo("America/New_York")
DECISION_TIME = time(9, 32)
# Fixed commissioning diagnostics, declared before results; not strategy parameters.
GATE = {
    "minimum_sessions": 30,
    "coverage_ratio_floor": 0.5,
    "degradation_minimum_days": 20,
    "material_daily_gap": 0.002,
    "degraded_day_fraction": 0.75,
    "weight_jump": 0.5,
    "oscillation_reversals": 6,
    "saturation_fraction": 0.8,
}


@dataclass
class ReplayClock:
    now: float

    def advance(self, instant):
        if instant < self.now:
            raise ValueError("replay clock cannot move backwards")
        self.now = instant
        return self.now


def session_window(as_of, count):
    if type(count) is not int or not 1 <= count <= 252:
        raise ValueError("sessions must be between 1 and 252")
    run_date = date.fromisoformat(as_of) if as_of else datetime.now(NY).date()
    if run_date > datetime.now(NY).date():
        raise ValueError("as-of cannot include future sessions")
    cal = exchange_calendars.get_calendar("XNYS")
    sessions = cal.sessions_in_range(
        run_date - timedelta(days=count * 3 + 30), run_date - timedelta(days=1)
    )
    if len(sessions) < count + 1:
        raise ValueError("insufficient calendar coverage")
    selected = sessions[-count:]
    previous = sessions[-count - 1]
    rows = []
    for session in selected:
        decision = datetime.combine(session.date(), DECISION_TIME, NY).timestamp()
        rows.append(
            {
                "date": str(session.date()),
                "open": cal.session_open(session).timestamp(),
                "close": cal.session_close(session).timestamp(),
                "decision": decision,
                "previous_close": cal.session_close(previous).timestamp(),
            }
        )
        previous = session
    return run_date.isoformat(), rows


def exact_path(bars, start, end):
    return bool(
        bars
        and bars[0].start == start
        and bars[-1].end == end
        and len(bars) == (end - start) / 60
        and all(a.end == b.start for a, b in zip(bars, bars[1:], strict=False))
    )


def decision_snapshot(history, session, symbol, benchmark, now):
    if now != session["decision"]:
        raise ValueError("snapshot must use the historical decision clock")
    bars = history.window(symbol, session["open"], now, now)
    benchmark_bars = history.window(benchmark, session["open"], now, now)
    previous = history.window(
        symbol, session["previous_close"] - 60, session["previous_close"], now
    )
    if not all(
        (
            exact_path(bars, session["open"], now),
            exact_path(benchmark_bars, session["open"], now),
            exact_path(previous, session["previous_close"] - 60, session["previous_close"]),
        )
    ):
        raise ValueError(f"{symbol}: missing decision/benchmark/previous-close minute")
    # The existing prediction contract needs bid/ask. Close is an explicit proxy,
    # never an observed executable quote. Alpha score/features do not use bid/ask.
    return MarketSnapshot(
        symbol,
        now,
        history.metadata["source"],
        "replay",
        benchmark,
        bars,
        benchmark_bars,
        bars[-1].close,
        bars[-1].close,
        now,
        now,
        bars[0].open,
        previous[-1].close,
        (),
        session["open"],
        session["previous_close"],
    )


def evidence_digest(store):
    """Stable fingerprint of all canonical evidence, also used for duplicate probes."""
    tables = sorted(store._columns)
    return identity(
        {
            table: sorted(
                (dict(r) for r in store.db.execute(f"SELECT * FROM {table}")),
                key=canonical,
            )
            for table in tables
        }
    )


def duplicate_probe(store, operation):
    before = evidence_digest(store)
    operation()
    return int(before != evidence_digest(store))


def report(store, config, sessions, events, metadata, idempotency_failures):
    rows = [
        dict(r)
        for r in store.db.execute(
            "SELECT p.*,o.residual_return,s.selected,s.baseline_selected "
            "FROM predictions p JOIN outcomes o USING(prediction_id) "
            "JOIN selections s USING(prediction_id) ORDER BY p.decision_time,p.prediction_id"
        )
    ]
    grouped = defaultdict(list)
    for row in rows:
        grouped[iso(row["decision_time"])[:10]].append(row)
    modes = {
        "learned": "selected",
        "raw": "baseline_selected",
        "null": "null_control",
        "random": "random_control",
    }
    daily, coverage = {}, {}
    opportunities = store.db.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    for mode, key in modes.items():
        daily[mode] = []
        chosen_all = []
        for day, members in sorted(grouped.items()):
            chosen = [
                r
                for r in members
                if (r[key] if mode in {"learned", "raw"} else r["strategy_id"] == key)
            ]
            chosen_all.extend(chosen)
            daily[mode].append(
                {
                    "day": day,
                    "score": statistics.fmean(metric(r) for r in chosen) if chosen else 0.0,
                    "predictions": len(chosen),
                }
            )
        if mode in {"learned", "raw"}:
            counts = store.db.execute(
                f"SELECT COUNT(*),COUNT(DISTINCT p.snapshot_id) FROM predictions p "
                f"JOIN selections s USING(prediction_id) WHERE s.{key}=1"
            ).fetchone()
            active = store.db.execute(
                "SELECT COUNT(*) FROM predictions p JOIN strategy_versions v "
                "ON p.strategy_id=v.strategy_id AND p.strategy_version=v.version "
                "WHERE p.direction!=0 AND v.role='champion'"
            ).fetchone()[0]
            coverage[mode] = {
                "selected_predictions": counts[0],
                "selected_snapshot_opportunities": counts[1],
                "snapshot_opportunities": opportunities,
                "selection_coverage": counts[1] / opportunities if opportunities else 0.0,
                "active_champion_predictions": active,
                "active_prediction_coverage": active / (5 * opportunities)
                if opportunities
                else 0.0,
            }
    comparison = {
        mode: {
            "mean_daily_score": statistics.fmean(d["score"] for d in days) if days else None,
            "daily_clusters": len(days),
            "days": days,
        }
        for mode, days in daily.items()
    }
    weight_rows = [
        dict(r)
        for r in store.db.execute(
            "SELECT s.strategy_id,s.version,s.weight,s.n,l.asof FROM strategy_scores s "
            "JOIN learning_runs l USING(run_id) ORDER BY l.asof,s.strategy_id,s.version"
        )
    ]
    by_version = defaultdict(list)
    for row in weight_rows:
        by_version[(row["strategy_id"], row["version"])].append(row["weight"])
    behavior = []
    for (strategy, version), values in by_version.items():
        changes = [b - a for a, b in zip(values, values[1:], strict=False)]
        jumps = [v for v in changes if abs(v) >= GATE["weight_jump"]]
        reversals = sum(a * b < 0 for a, b in zip(jumps, jumps[1:], strict=False))
        behavior.append(
            {
                "strategy": strategy,
                "version": version,
                "updates": len(values),
                "initial": values[0],
                "final": values[-1],
                "min": min(values),
                "max": max(values),
                "max_step": max(map(abs, changes), default=0),
                "bound_fraction": sum(v <= 0.1000001 or v >= 1.9999999 for v in values)
                / len(values),
                "large_step_reversals": reversals,
                "changed": max(values) - min(values) > 1e-12,
            }
        )
    inspected = store.inspect()
    counts = inspected["counts"]
    violations = store.db.execute(
        "SELECT COUNT(*) FROM predictions p JOIN selections s USING(prediction_id) "
        "WHERE p.created_at!=p.decision_time OR "
        "(s.state_asof IS NOT NULL AND s.state_asof>=p.decision_time)"
    ).fetchone()[0]
    violations += store.db.execute(
        "SELECT COUNT(*) FROM outcomes o JOIN predictions p USING(prediction_id) "
        "WHERE o.outcome_time!=p.decision_time+3600 OR o.resolved_at<o.outcome_time"
    ).fetchone()[0]
    violations += store.db.execute(
        "SELECT COUNT(*) FROM market_snapshots WHERE evidence_kind!='replay'"
    ).fetchone()[0]
    complete = sum(e["status"] == "completed" for e in events)
    skipped = [e for e in events if e["status"] == "skipped"]
    failed, review = [], []
    if (
        violations
        or idempotency_failures
        or inspected["integrity"] != "ok"
        or store.db.execute("PRAGMA foreign_key_check").fetchall()
    ):
        failed.append("chronology, persistence or idempotency violation")
    if counts["promotions"]:
        failed.append("historical evidence promoted a challenger")
    if counts["outcomes"] != counts["attributions"]:
        failed.append("resolved evidence missing attribution")
    if counts["predictions"] - counts["outcomes"] > sum(e.get("unresolved", 0) for e in skipped):
        failed.append("unexplained unresolved predictions")
    if complete < GATE["minimum_sessions"] or skipped:
        review.append("limited completed sessions or source gaps")
    if metadata.get("availability") != "point_in_time":
        review.append("vendor bar availability/revisions are assumed, not point-in-time certified")
    if metadata.get("source", "").startswith("fixture"):
        review.append("synthetic fixture is implementation validation only")
    raw_cov = coverage["raw"]["selection_coverage"]
    if (
        opportunities >= 20
        and raw_cov
        and coverage["learned"]["selection_coverage"] < raw_cov * GATE["coverage_ratio_floor"]
    ):
        failed.append("severe learned selection coverage collapse")
    if any(b["large_step_reversals"] >= GATE["oscillation_reversals"] for b in behavior):
        failed.append("repeated large weight oscillations")
    if any(
        b["updates"] >= 20 and b["bound_fraction"] >= GATE["saturation_fraction"] for b in behavior
    ):
        review.append("persistent bound saturation needs interpretation")
    if complete >= 20 and not any(b["changed"] for b in behavior):
        review.append("no observed weight adaptation")
    learned = {d["day"]: d["score"] for d in daily["learned"]}
    persistent = []
    for mode in ("raw", "null", "random"):
        gaps = [d["score"] - learned[d["day"]] for d in daily[mode]]
        persistent.append(
            bool(
                len(gaps) >= GATE["degradation_minimum_days"]
                and statistics.fmean(gaps) >= GATE["material_daily_gap"]
                and sum(g > 0.001 for g in gaps) / len(gaps) >= GATE["degraded_day_fraction"]
            )
        )
    if opportunities >= 20 and not raw_cov:
        review.append("no active selection coverage to assess")
    if any(persistent) and not all(persistent):
        review.append("material degradation against some comparators needs interpretation")
    if all(persistent):
        failed.append("persistent material degradation versus raw and both controls")
    return {
        "status": "FAIL" if failed else "REVIEW" if review else "PASS",
        "reasons": failed + review,
        "configuration": config,
        "data": metadata,
        "session_range": [sessions[0]["date"], sessions[-1]["date"]],
        "integrity": {
            "requested_sessions": len(sessions),
            "completed_sessions": complete,
            "skipped_sessions": skipped,
            "predictions_generated": counts["predictions"],
            "predictions_resolved": counts["outcomes"],
            "unresolved_predictions": counts["predictions"] - counts["outcomes"],
            "attributions": counts["attributions"],
            "learner_updates": counts["learning_runs"],
            "challengers": counts["mutations"],
            "evolution_evaluations": len(inspected["evaluations"]),
            "promotions": counts["promotions"],
            "timestamp_leakage_violations": violations,
            "idempotency_failures": idempotency_failures,
            "database": inspected["integrity"],
            "foreign_key_violations": len(store.db.execute("PRAGMA foreign_key_check").fetchall()),
        },
        "selector_comparison": comparison,
        "coverage": coverage,
        "weight_behavior": behavior,
        "weights_over_time": weight_rows,
        "gate_diagnostics": GATE,
        "real_broker_calls": 0,
        "options_enabled": False,
        "metric": "existing frozen clipped directional residual minus 10bp; daily mean; not PnL",
        "score_denominator": "resolved UTC daily clusters; no selection on a resolved day scores zero",
        "quote_proxy": "decision bar close for required bid/ask; no executable-price claim",
        "historical_evidence_is_promotion_evidence": False,
    }


def write_summary(output, result):
    (output / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    scores = result["selector_comparison"]
    lines = [
        "# Historical commissioning",
        f"Status: **{result['status']}**. Historical replay is not promotion evidence.",
        f"Decision: {result['configuration']['decision_time']}; horizon: 60 minutes; cold start.",
        f"Sessions: {result['session_range']}; integrity: {canonical(result['integrity'])}",
        "Frozen daily residual scores (not PnL):",
        *[f"- {mode}: {values['mean_daily_score']}" for mode, values in scores.items()],
        f"Coverage: {canonical(result['coverage'])}",
        f"Reasons: {'; '.join(result['reasons']) or 'no gate problems detected'}",
        "See summary.json for daily scores and strategy/version weight trajectories.",
        "Bid/ask are bar-close proxies; vendor revision/availability assumptions are explicit.",
    ]
    (output / "SUMMARY.md").write_text("\n\n".join(lines) + "\n")


def run_history(
    output, sessions=60, symbols=None, benchmark="SPY", as_of=None, input_path=None, source=None
):
    output = Path(output).resolve()
    symbols = symbols or ["QQQ", "IWM"]
    symbols = [s.strip().upper() for s in symbols]
    benchmark = benchmark.strip().upper()
    if not symbols or len(set(symbols)) != len(symbols) or benchmark in symbols:
        raise ValueError("distinct target symbols and a separate benchmark are required")
    if not all(s.isalpha() and len(s) <= 5 for s in [*symbols, benchmark]):
        raise ValueError("invalid equity symbol")
    run_date, calendar = session_window(as_of, sessions)
    # Refuse existing normal DBs before opening ANY SQLite file. A completed replay is
    # bound to one configuration/input/code fingerprint; interrupted runs need a fresh dir.
    marker_path = output / "commissioning.json"
    db_path = output / "experience.sqlite3"
    if (output / "state.sqlite3").exists():
        raise ValueError("commissioning output cannot contain an execution journal")
    marker = json.loads(marker_path.read_text()) if marker_path.exists() else None
    if db_path.exists() and not marker:
        raise ValueError("refusing existing non-commissioning research database")
    config = {
        "sessions": sessions,
        "symbols": symbols,
        "benchmark": benchmark,
        "as_of": run_date,
        "decision_time": "09:32 America/New_York (commissioning default; no canonical schedule)",
        "horizon_seconds": 3600,
        "cold_start": True,
        "evidence_kind": "replay",
        "strategy_implementation": implementation_hash(),
        "pipeline_implementation": identity(
            [(p.name, p.read_text()) for p in sorted(Path(__file__).parent.glob("*.py"))]
        ),
    }
    try:
        history, data_digest, cache = load_history(
            output,
            sorted([*symbols, benchmark]),
            calendar[0]["previous_close"] - 60,
            calendar[-1]["close"],
            input_path,
            source,
        )
    except ValueError as exc:
        if marker or db_path.exists() or not str(exc).startswith("historical data unavailable:"):
            raise
        output.mkdir(parents=True, exist_ok=True)
        result = {
            "status": "REVIEW",
            "reasons": [str(exc)],
            "configuration": config,
            "data": {"source": "alpaca-sip-raw", "status": "unavailable"},
            "session_range": None,
            "requested_session_range": [calendar[0]["date"], calendar[-1]["date"]],
            "integrity": {
                "requested_sessions": sessions,
                "completed_sessions": 0,
                "skipped_sessions": [{"session": d["date"], "reason": str(exc)} for d in calendar],
                "predictions_generated": 0,
                "predictions_resolved": 0,
                "unresolved_predictions": 0,
                "attributions": 0,
                "learner_updates": 0,
                "challengers": 0,
                "evolution_evaluations": 0,
                "promotions": 0,
                "timestamp_leakage_violations": 0,
                "idempotency_failures": 0,
                "database": "not_created",
                "foreign_key_violations": 0,
            },
            "selector_comparison": {
                mode: {"mean_daily_score": None, "daily_clusters": 0, "days": []}
                for mode in ("learned", "raw", "null", "random")
            },
            "coverage": {},
            "weight_behavior": [],
            "weights_over_time": [],
            "real_broker_calls": 0,
            "options_enabled": False,
            "historical_evidence_is_promotion_evidence": False,
        }
        write_summary(output, result)
        return result
    config["data_digest"] = data_digest
    config["cache"] = str(cache.relative_to(output))
    # Source-independent isolated workspace; the store enforces persistent Linux storage.
    store = Experience(output)
    try:
        with store.lock():
            marker = json.loads(marker_path.read_text()) if marker_path.exists() else None
            if marker:
                if marker.get("configuration") != config or marker.get("state") != "completed":
                    raise ValueError(
                        "changed or incomplete replay; choose a fresh commissioning output"
                    )
                if marker["evidence_digest"] != evidence_digest(store):
                    raise ValueError("commissioning evidence changed since completion")
                return json.loads((output / "summary.json").read_text())
            marker_path.write_text(canonical({"state": "running", "configuration": config}) + "\n")
            seed(store, calendar[0]["decision"] - 1)
            clock = ReplayClock(calendar[0]["decision"])
            events, failures, week = [], 0, None
            for session in calendar:
                clock.advance(session["decision"])
                event = {"session": session["date"], "decision": iso(clock.now)}
                if clock.now + 3600 > session["close"]:
                    events.append(
                        {**event, "status": "skipped", "reason": "horizon outside regular session"}
                    )
                    continue
                try:
                    snapshots = [
                        decision_snapshot(history, session, s, benchmark, clock.now)
                        for s in symbols
                    ]
                except ValueError as exc:
                    events.append({**event, "status": "skipped", "reason": str(exc)})
                    continue
                for snapshot in snapshots:
                    scan(store, snapshot, clock.now)
                    failures += duplicate_probe(
                        store, lambda snap=snapshot: scan(store, snap, clock.now)
                    )
                # Future bars are requested ONLY after all predictions are durable.
                clock.advance(session["decision"] + 3600)
                future = []
                missing = []
                for symbol in [*symbols, benchmark]:
                    bars = history.window(symbol, session["decision"], clock.now, clock.now)
                    future.extend(asdict(b) for b in bars)
                    if not exact_path(bars, session["decision"], clock.now):
                        missing.append(symbol)
                dataset = {"source": history.metadata["source"], "bars": future}
                resolved = resolve(store, dataset, clock.now)
                failures += duplicate_probe(
                    store, lambda data=dataset: resolve(store, data, clock.now)
                )
                clock.advance(clock.now + 1)
                learned = learn_daily(store, clock.now, "replay")
                failures += duplicate_probe(store, lambda: learn_daily(store, clock.now, "replay"))
                # Normal UTC weekly proposal cadence, after the first completed daily update.
                current_week = datetime.fromtimestamp(clock.now, ZoneInfo("UTC")).strftime("%G-W%V")
                if current_week != week:
                    clock.advance(clock.now + 1)
                    evolve_weekly(store, clock.now, kind="replay")
                    evaluate(store, clock.now)
                    failures += duplicate_probe(
                        store, lambda: evolve_weekly(store, clock.now, kind="replay")
                    )
                    week = current_week
                unresolved_today = store.db.execute(
                    "SELECT COUNT(*) FROM predictions p LEFT JOIN outcomes o USING(prediction_id) "
                    "WHERE p.decision_time=? AND o.prediction_id IS NULL",
                    (session["decision"],),
                ).fetchone()[0]
                events.append(
                    {
                        **event,
                        "status": "skipped" if missing else "completed",
                        "reason": "missing exact-horizon path: " + ",".join(missing)
                        if missing
                        else None,
                        "unresolved": unresolved_today,
                        "resolved": resolved["resolved"],
                        "learner": learned,
                        "learned_asof": iso(clock.now),
                    }
                )
            result = report(store, config, calendar, events, history.metadata, failures)
            (output / "events.json").write_text(json.dumps(events, indent=2) + "\n")
            write_summary(output, result)
            marker_path.write_text(
                canonical(
                    {
                        "state": "completed",
                        "configuration": config,
                        "evidence_digest": evidence_digest(store),
                    }
                )
                + "\n"
            )
            return result
    finally:
        store.close()
