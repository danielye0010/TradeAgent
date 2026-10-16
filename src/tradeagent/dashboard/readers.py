"""Read-only views over TradeAgent state for the local dashboard.

Nothing here writes: SQLite files open with ``mode=ro`` and JSON files are only
read. Research records are append-only, so parsed decisions and outcomes are
cached by id; mutable files are cached by modification time.
"""

import hashlib
import json
import sqlite3
import threading
import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
MIN_DAYS = 20  # paired sessions before a comparison gets a verdict
ARMS = {
    "daily-four-arm-v1": ["codex_only", "quant_codex", "quant_only", "seeded_random"],
    "daily-news-v2": [
        "codex_news",
        "quant_codex_news",
        "codex_price",
        "quant_only",
        "seeded_random",
    ],
}
BASELINE = {"daily-four-arm-v1": "seeded_random", "daily-news-v2": "seeded_random"}
# How the installer schedules each protocol (New York time); used when no
# systemd timer file is available, e.g. in the demo.
DEFAULT_SCHEDULE = {
    "daily-four-arm-v1": [("10:00", "decide"), ("11:30", "resolve"), ("17:00", "report")],
    "daily-news-v2": [("10:30", "decide"), ("12:00", "resolve"), ("17:05", "report")],
}

_lock = threading.Lock()
_files = {}
_parsed = {}


def source_id(path):
    return hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()[:10]


def _stamp(path):
    try:
        info = Path(path).stat()
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def read_json(path):
    """Parsed JSON, cached until the file changes; None when missing or unreadable."""
    path = Path(path)
    stamp = _stamp(path)
    if stamp is None:
        return None
    with _lock:
        hit = _files.get(path)
        if hit and hit[0] == stamp:
            return hit[1]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    with _lock:
        _files[path] = (stamp, value)
    return value


def connect(path):
    """Open an existing SQLite file read-only; never creates or migrates it."""
    path = Path(path)
    if not path.is_file():
        return None
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
    db.execute("PRAGMA query_only=ON")
    return db


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def kind_of(directory):
    directory = Path(directory)
    if (directory / "daily-deployment.json").is_file():
        return "daily"
    if (directory / "agent" / "state.sqlite3").is_file() or (directory / "report.json").is_file():
        return "execution"
    if (directory / "experience.sqlite3").is_file():
        return "lab"
    return None


def ny_date(timestamp):
    return datetime.fromtimestamp(timestamp, NY).date().isoformat()


# ---------------------------------------------------------------- daily research


def residual(outcome):
    """Net return over the benchmark for the same window; cash (no position) is zero."""
    if not outcome:
        return None
    if "benchmark_gross" not in outcome:
        return 0.0
    return outcome["base_net"] - outcome["benchmark_gross"]


def verdict(stat, min_days=MIN_DAYS):
    days = (stat or {}).get("days") or 0
    if days < min_days:
        return {"state": "collecting", "days": days, "needed": min_days}
    mean, se = stat.get("mean"), stat.get("standard_error")
    if mean is None or not se:
        return {"state": "unclear", "days": days}
    low, high = mean - 1.96 * se, mean + 1.96 * se
    state = "ahead" if low > 0 else "behind" if high < 0 else "unclear"
    return {"state": state, "days": days, "low": low, "high": high}


def _summarize_research(research):
    if not research:
        return None
    sources = research.get("sources") or []
    return {
        "stance": research.get("stance"),
        "rank": research.get("rank"),
        "thesis": research.get("thesis"),
        "catalyst": research.get("catalyst"),
        "priced_in": research.get("priced_in"),
        "invalidation": research.get("invalidation"),
        "contradictions": research.get("contradictions") or [],
        "model": research.get("model"),
        "sources": [
            {"title": s.get("title") or s.get("url"), "url": s.get("url"), "role": s.get("role")}
            for s in sources[:6]
            if str(s.get("url", "")).startswith("https://")
        ],
    }


def summarize_decision(d):
    candidates = []
    for c in d.get("ranked_candidates") or []:
        scanner = c.get("scanner") or {}
        features = scanner.get("features") or {}
        analyses = {"price": _summarize_research(c.get("research"))} if c.get("research") else {}
        candidates.append(
            {
                "symbol": c.get("symbol"),
                "window_return": features.get("window_return"),
                "relative_strength": features.get("relative_strength"),
                "volume_ratio": features.get("volume_ratio"),
                "categories": scanner.get("categories") or [],
                "score": scanner.get("ranking_score"),
                "analysis": analyses,
            }
        )
    arms = {
        name: {
            "symbol": arm.get("symbol"),
            "decision": arm.get("decision"),
            "status": arm.get("status"),
        }
        for name, arm in (d.get("comparisons") or {}).items()
    }
    return {
        "decision_id": d["decision_id"],
        "scan_id": d.get("scan_id"),
        "protocol": d.get("research_protocol"),
        "session_date": ny_date(d["session_open"]),
        "freeze_time": d.get("freeze_time"),
        "entry_after": d.get("entry_after"),
        "exit_time": d.get("outcome_exit_time"),
        "evidence_kind": d.get("evidence_kind"),
        "assessment_status": d.get("assessment_status"),
        "news_assessment_status": d.get("news_assessment_status"),
        "universe_size": len(d.get("quantitative_observations") or []),
        "candidates": candidates,
        "arms": arms,
    }


def summarize_assessment(a):
    return {
        "scan_id": a.get("scan_id"),
        "symbol": a.get("symbol"),
        "assessor": a.get("assessor", "price"),
        "research": _summarize_research(a),
    }


def summarize_outcome(o):
    arms = {}
    for name, value in (o.get("comparisons") or {}).items():
        arms[name] = (
            None
            if value is None
            else {
                "net": value.get("base_net"),
                "stress_net": value.get("stress_net"),
                "excess": residual(value),
            }
        )
    symbols = {}
    for symbol, value in (o.get("candidate_outcomes") or {}).items():
        if isinstance(value, dict) and "gross" in value:
            symbols[symbol] = {"gross": value.get("gross"), "net": value.get("base_net")}
    return {"decision_id": o.get("decision_id"), "arms": arms, "symbols": symbols}


def _records(db, directory, table, summarize):
    """Append-only research rows, parsed once per id."""
    if "opportunity_" + table not in _tables(db):
        return []
    key = (str(directory), table)
    rows = db.execute(f"SELECT id, recorded_at FROM opportunity_{table} ORDER BY recorded_at")
    rows = rows.fetchall()
    with _lock:
        cache = _parsed.setdefault(key, {})
        missing = [row_id for row_id, _ in rows if row_id not in cache]
    for row_id in missing:
        payload = db.execute(
            f"SELECT payload FROM opportunity_{table} WHERE id=?", (row_id,)
        ).fetchone()
        try:
            value = summarize(json.loads(payload[0])) if payload else None
        except (KeyError, TypeError, ValueError):
            value = None
        with _lock:
            cache[row_id] = value
    with _lock:
        return [(cache[row_id], recorded) for row_id, recorded in rows if cache[row_id]]


def raw_decision(directory, decision_id):
    db = connect(Path(directory) / "experience.sqlite3")
    if db is None:
        return None
    try:
        row = db.execute(
            "SELECT payload FROM opportunity_decisions WHERE id=?", (decision_id,)
        ).fetchone()
        if row is None:
            row = next(
                (
                    r
                    for r in db.execute("SELECT payload FROM opportunity_decisions")
                    if json.loads(r[0]).get("decision_id") == decision_id
                ),
                None,
            )
        return json.loads(row[0]) if row else None
    finally:
        db.close()


def daily_state(directory):
    directory = Path(directory)
    deployment = read_json(directory / "daily-deployment.json") or {}
    config = deployment.get("config") or {}
    protocol = config.get("protocol", "daily-four-arm-v1")
    status = read_json(directory / "daily-status.json") or {}
    sessions, decisions, outcomes = [], {}, {}
    db = connect(directory / "experience.sqlite3")
    if db is not None:
        try:
            if "opportunity_sessions" in _tables(db):
                for (payload,) in db.execute(
                    "SELECT payload FROM opportunity_sessions ORDER BY recorded_at"
                ):
                    try:
                        sessions.append(json.loads(payload))
                    except ValueError:
                        continue
            analyses = {}
            for item, _ in _records(db, directory, "assessments", summarize_assessment):
                analyses[(item["scan_id"], item["symbol"], item["assessor"])] = item["research"]
            for item, _ in _records(db, directory, "decisions", summarize_decision):
                if item["protocol"] in (None, protocol):
                    for c in item["candidates"]:
                        for assessor in ("price", "news"):
                            found = analyses.get((item["scan_id"], c["symbol"], assessor))
                            if found:
                                c["analysis"][assessor] = found
                    decisions[item["decision_id"]] = item
            for item, recorded in _records(db, directory, "outcomes", summarize_outcome):
                item["resolved_at"] = recorded
                outcomes[item["decision_id"]] = item
        finally:
            db.close()
    finals = {}
    failures = []
    for s in sessions:
        if s.get("phase") == "FINAL":
            finals[s.get("session_date")] = s
        elif s.get("failure_kind") and not s.get("phase"):
            failures.append(s)
    runs = []
    by_day = {d["session_date"]: d for d in decisions.values()}
    for day in sorted(set(finals) | set(by_day)):
        final = finals.get(day, {})
        decision = by_day.get(day)
        outcome = outcomes.get(decision["decision_id"]) if decision else None
        runs.append(
            {
                "session_date": day,
                "status": final.get("status") or ("COMPLETED" if decision else "STARTED"),
                "failure_kind": final.get("failure_kind"),
                "reason": final.get("reason") or final.get("assessment_error"),
                "time": (decision or {}).get("freeze_time") or final.get("recorded_at"),
                "decision": decision,
                "outcome": outcome,
                "pending": bool(decision) and outcome is None,
            }
        )
    pools = (status.get("comparison") or {}).get("evidence_pools") or {}
    pool_name = config.get("evidence_kind") or next(iter(pools), "prospective")
    pool = pools.get(pool_name) or {}
    arms = ARMS.get(protocol) or list((pool.get("arms") or {}).keys())
    baseline = BASELINE.get(protocol)
    arm_rows = []
    for name in arms:
        stats = (pool.get("arms") or {}).get(name) or {}
        excess_diff = ((pool.get("paired_excess_differences") or {}).get(name) or {}).get(baseline)
        arm_rows.append(
            {
                "arm": name,
                "observed_days": stats.get("observed_days", 0),
                "selected_days": stats.get("selected_days", 0),
                "unavailable_days": stats.get("unavailable_days", 0),
                "mean_net": stats.get("paired_mean_base_net"),
                "mean_stress_net": stats.get("paired_mean_stress_net"),
                "excess": stats.get("paired_excess_over_benchmark"),
                "excess_verdict": verdict(stats.get("paired_excess_over_benchmark")),
                "vs_baseline": excess_diff if name != baseline else None,
                "vs_baseline_verdict": verdict(excess_diff) if name != baseline else None,
            }
        )
    series = []
    for run in runs:
        outcome = run["outcome"]
        if not outcome:
            continue
        values = {name: (outcome["arms"].get(name) or {}).get("excess") for name in arms}
        if all(v is not None for v in values.values()):
            series.append({"session_date": run["session_date"], "excess": values})
    return {
        "protocol": protocol,
        "evidence_kind": pool_name,
        "first_session_date": deployment.get("first_session_date"),
        "config": {
            "decision_window_ny": config.get("decision_window_ny"),
            "holding_seconds": config.get("holding_seconds"),
            "candidates": config.get("candidates"),
            "universe_size": len(config.get("universe") or []),
            "codex_model": config.get("codex_model"),
            "mode": config.get("mode"),
        },
        "last_tick": status.get("tick_completed_at"),
        "frozen_sessions": pool.get("frozen_sessions", len(decisions)),
        "resolved_sessions": pool.get("resolved_sessions", len(outcomes)),
        "valid_paired_days": pool.get("valid_paired_days", len(series)),
        "pending_sessions": len(status.get("pending_sessions") or []),
        "baseline": baseline,
        "arms": arm_rows,
        "series": series,
        "runs": runs,
        "data_gaps": [
            {
                "time": f.get("recorded_at"),
                "failure_kind": f.get("failure_kind"),
                "reason": f.get("reason"),
            }
            for f in failures[-20:]
        ],
        "orders_submitted": 0,
    }


def run_detail(directory, decision_id):
    """Everything a person wants to see about one frozen session decision."""
    state = daily_state(directory)
    run = next(
        (r for r in state["runs"] if r["decision"] and r["decision"]["decision_id"] == decision_id),
        None,
    )
    if run is None:
        return None
    return {"protocol": state["protocol"], "evidence_kind": state["evidence_kind"], **run}


# ---------------------------------------------------------------- execution


def _money(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _order_view(order):
    executions = order.get("executions") or []
    times = sorted(e.get("timestamp") for e in executions if e.get("timestamp"))
    return {
        "side": order.get("side"),
        "symbol": order.get("symbol"),
        "state": order.get("state"),
        "quantity": _money(order.get("cumulative_quantity") or order.get("quantity")),
        "price": _money(order.get("execution_average_price") or order.get("average_price")),
        "notional": _money(order.get("executed_notional")),
        "fees": _money(order.get("fees")),
        "filled_at": times[0] if times else None,
    }


def execution_run(directory):
    """Summary of one execution state directory (a live or paper run)."""
    directory = Path(directory)
    report = read_json(directory / "report.json")
    started = finished = None
    db_status = db_mode = run_id = None
    events = []
    db = connect(directory / "agent" / "state.sqlite3")
    if db is not None:
        try:
            if "runs" in _tables(db):
                row = db.execute(
                    "SELECT id, started, finished, mode, status FROM runs ORDER BY started DESC"
                ).fetchone()
                if row:
                    run_id, started, finished, db_mode, db_status = row
                    events = [
                        (time_, kind, payload)
                        for time_, kind, payload in db.execute(
                            "SELECT time, kind, payload FROM events WHERE run_id=? ORDER BY seq",
                            (run_id,),
                        )
                    ]
        finally:
            db.close()
    paper = (directory / "paper-run.json").is_file() or (report or {}).get("mode") == "PAPER"
    view = {
        "id": source_id(directory),
        "path": str(directory),
        "mode": "PAPER" if paper else "LIVE",
        "synthetic": bool((report or {}).get("synthetic_fixture")),
        "started": started,
        "finished": finished,
        "status": (report or {}).get("status") or db_status or "UNKNOWN",
        "in_progress": report is None and db_status not in (None, "COMPLETED", "HALTED"),
        "orders": [],
        "exit_due": None,
        "exit_reason": None,
        "realized_pnl": None,
        "reconciled": None,
        "flat": None,
        "source": "ai" if ((report or {}).get("decision") or {}).get("provenance") else "manual",
        "timeline": [],
    }
    labels = {
        "proposal": "Order proposed",
        "risk_pass": "Risk checks passed",
        "human_approval": "Order approved",
        "submission_started": "Order sent",
        "fill_reconciliation": "Fill confirmed",
        "exit_due": "Exit scheduled",
    }
    seen_fill = set()
    for time_, kind, payload in events:
        if kind not in labels:
            continue
        try:
            data = json.loads(payload)
        except ValueError:
            data = {}
        if kind == "exit_due":
            view["exit_due"] = data.get("due")
        if kind == "fill_reconciliation":
            mark = json.dumps(data.get("executions"), sort_keys=True)
            if mark in seen_fill:
                continue
            seen_fill.add(mark)
        if kind == "risk_pass" and data.get("phase") != "before_review":
            continue
        view["timeline"].append({"time": time_, "kind": kind})
    if report:
        view["orders"] = [_order_view(o) for o in report.get("orders") or []]
        view["exit_due"] = report.get("exit_due") or view["exit_due"]
        view["exit_reason"] = report.get("exit_reason")
        view["realized_pnl"] = _money(report.get("realized_pnl"))
        view["reconciled"] = report.get("reconciliation_status") == "RECONCILED"
        view["flat"] = report.get("flat_bot_position")
        view["reason"] = report.get("reason")
    view["db_mode"] = db_mode
    return view


def execution_history(directory):
    directory = Path(directory)
    runs = []
    if kind_of(directory) == "execution":
        runs.append(execution_run(directory))
    history = directory.with_name(directory.name + ".history")
    if history.is_dir():
        for item in sorted(history.iterdir()):
            if item.is_dir() and kind_of(item) == "execution":
                view = execution_run(item)
                view["archived"] = True
                runs.append(view)
    runs.sort(key=lambda r: r.get("started") or 0, reverse=True)
    return runs


def live_settings(path):
    """The few TOML fields a person needs at a glance; nothing else is exposed."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"error": "settings file could not be read"}
    live, entry, exit_ = data.get("live", {}), data.get("entry", {}), data.get("exit", {})
    return {
        "symbols": [s for s in live.get("symbols", []) if isinstance(s, str)][:20],
        "max_notional": _money(live.get("max_notional")),
        "dollar_amount": _money(entry.get("dollar_amount")),
        "hold_seconds": exit_.get("hold_seconds")
        if isinstance(exit_.get("hold_seconds"), int)
        else None,
    }


# ---------------------------------------------------------------- strategy lab


def lab_state(directory):
    directory = Path(directory)
    db = connect(directory / "experience.sqlite3")
    if db is None:
        return None
    try:
        tables = _tables(db)

        def count(table, where=""):
            if table not in tables:
                return 0
            return db.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]

        evidence = {}
        if "market_snapshots" in tables:
            evidence = dict(
                db.execute(
                    "SELECT evidence_kind, COUNT(*) FROM market_snapshots GROUP BY evidence_kind"
                ).fetchall()
            )
        weights, latest = [], None
        if "learning_runs" in tables and "strategy_scores" in tables:
            row = db.execute("SELECT run_id, asof FROM learning_runs ORDER BY asof DESC").fetchone()
            if row:
                latest = row[1]
                weights = [
                    {
                        "strategy": strategy,
                        "version": version,
                        "n": n,
                        "weight": weight,
                        "mean_return": mean,
                        "uncertainty": uncertainty,
                    }
                    for strategy, version, n, weight, mean, uncertainty in db.execute(
                        "SELECT strategy_id, version, n, weight, mean_return, uncertainty "
                        "FROM strategy_scores WHERE run_id=? ORDER BY weight DESC",
                        (row[0],),
                    )
                ]
        return {
            "strategies": count("strategies"),
            "versions": count("strategy_versions"),
            "predictions": count("predictions"),
            "outcomes": count("outcomes"),
            "learning_runs": count("learning_runs"),
            "challengers": count("mutations"),
            "promotions": count("promotions"),
            "active_lessons": count("lessons", "WHERE status='active'"),
            "evidence": evidence,
            "last_learning": latest,
            "weights": weights,
            "synthetic": (read_json(directory / "summary.json") or {})
            .get("evidence", "")
            .startswith("SYNTHETIC")
            or set(evidence) == {"synthetic"},
        }
    finally:
        db.close()
