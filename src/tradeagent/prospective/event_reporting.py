"""Reproducible event-cohort comparisons, session clusters and missingness."""

from ..research.domain import canonical, iso
from .continuous import cluster
from .event_store import CONFIG, PROTOCOL, VERSION
from .events import ARMS, outcomes, session


def compare(rows):
    results = {}
    index = {(r["event_id"], r["arm"], r["horizon"]): r for r in rows}
    for horizon in (1, 3, 5):
        results[str(horizon)] = {}
        for name, a, b in (
            ("primary_ai_minus_delayed", "quant_codex", "delayed"),
            ("waiting_delayed_minus_fast", "delayed", "fast"),
        ):
            matched = []
            for r in rows:
                other = index.get((r["event_id"], b, horizon))
                if r["arm"] != a or r["horizon"] != horizon or not other:
                    continue
                if not (
                    r["status"] == other["status"] == "COMPLETE"
                    and r["market_eligible"]
                    and other["market_eligible"]
                ):
                    continue
                if a == "quant_codex" and (
                    r["entry_capture_id"] != other["entry_capture_id"]
                    or r["entry_target_at"] != other["entry_target_at"]
                ):
                    raise ValueError("AI and delayed entry cohorts differ")
                matched.append((r["entry_session"], r["net"] - other["net"]))
            results[str(horizon)][name] = cluster(matched)
        results[str(horizon)]["arms"] = {}
        for arm in ARMS:
            selected = [r for r in rows if r["arm"] == arm and r["horizon"] == horizon]
            complete = [r for r in selected if r["status"] == "COMPLETE" and r["market_eligible"]]
            results[str(horizon)]["arms"][arm] = {
                "complete_n": len(complete),
                "pending_n": sum(r["status"] == "PENDING" for r in selected),
                "missing_n": sum(r["status"] == "UNAVAILABLE" for r in selected),
                "corporate_action_flagged_n": sum(
                    r["status"] == "FLAGGED_CORPORATE_ACTION" for r in selected
                ),
                "net_vs_cash": cluster([(r["entry_session"], r["net"]) for r in complete]),
                "excess_spy": cluster(
                    [
                        (r["entry_session"], r["net"] - r["spy_net"])
                        for r in complete
                        if r["spy_net"] is not None
                    ]
                ),
                "excess_event_basket": cluster(
                    [
                        (r["entry_session"], r["net"] - r["basket_net"])
                        for r in complete
                        if r["basket_net"] is not None
                    ]
                ),
                "long_n": sum(r["choice"] == "LONG" for r in selected),
                "cash_n": sum(r["choice"] == "CASH" for r in selected),
                "long_direction_hits": sum(
                    r["choice"] == "LONG" and r["price_details"]["gross_mid_return"] > 0
                    for r in complete
                ),
                "cash_avoided_negative_n": sum(
                    r["choice"] == "CASH" and r["price_details"]["gross_mid_return"] <= 0
                    for r in complete
                ),
                "cash_missed_positive_n": sum(
                    r["choice"] == "CASH" and r["price_details"]["gross_mid_return"] > 0
                    for r in complete
                ),
            }
    return results


def report(store, v5_directory=None):
    now = store.clock()
    events = store.rows("event")
    contents = store.rows("content")
    analysis = store.rows("analysis")
    entries = store.rows("entry")
    values = outcomes(store)
    assessment_map = {r["event_id"]: r for r in analysis}
    started = {r["event_id"] for r in store.rows("analysis_started")}
    stages = []
    for e in events:
        a = assessment_map.get(e["id"], {})
        stages.append(
            {
                "event_id": e["id"],
                "symbol": e["symbol"],
                "prospective": e["prospective"],
                "category": e["category"],
                "material_detected": e["material"],
                "rejection": e["rejection"],
                "content_retrieved": any(c["event_id"] == e["id"] for c in contents),
                "analysis_candidate": bool(
                    e["prospective"] and e["material"] and not e["rejection"]
                ),
                "accepted_for_analysis": e["id"] in started,
                "codex_completed": a.get("status") == "COMPLETED",
                "entry_eligibility": {
                    r["kind"]: r["frame"]["eligible"] for r in entries if r["event_id"] == e["id"]
                },
                "choices": {r["kind"]: r["choices"] for r in entries if r["event_id"] == e["id"]},
                "assessment_status": a.get(
                    "status", "PENDING" if e["prospective"] and e["material"] else "NOT_ELIGIBLE"
                ),
                "assessment_reason": a.get("reason"),
                "market_available": any(
                    r["event_id"] == e["id"] and r["status"] == "FROZEN" for r in entries
                ),
            }
        )
    result = {
        "protocol": PROTOCOL,
        "version": VERSION,
        "generated_at": now,
        "mode": "SHADOW",
        "activated_at": store.activated_at,
        "prospective_events": sum(e["prospective"] for e in events),
        "nonprospective_events": sum(not e["prospective"] for e in events),
        "rejected_events": sum(e["rejection"] is not None for e in events),
        "content_count": len(contents),
        "polls": store.rows("poll"),
        "stages": stages,
        "codex_started": len(store.rows("analysis_started")),
        "codex_completed": sum(r["status"] == "COMPLETED" for r in analysis),
        "codex_unavailable": sum(r["status"] == "UNAVAILABLE" for r in analysis),
        "daily_codex_budget": CONFIG["daily_codex_budget"],
        "categories": {
            k: sum(e["category"] == k for e in events)
            for k in sorted({e["category"] for e in events})
        },
        "interpretations": {
            k: sum(r.get("assessment", {}).get("interpretation") == k for r in analysis)
            for k in ("bullish", "bearish", "neutral")
        },
        "analysis_latencies_seconds": [
            r["completed_at"] - r["started_at"] for r in analysis if "started_at" in r
        ],
        "discovery_latencies_seconds": [
            e["first_discovered_at"] - e["accepted_at"]
            for e in events
            if e["prospective"] and e["accepted_at"] is not None
        ],
        "decisions": entries,
        "outcomes": values,
        "comparison": compare(values),
        "broker_writes": 0,
        "api_billed_model_calls": 0,
        "corporate_action_scope": CONFIG["corporate_actions"],
    }
    # Direction diagnostics never masquerade as a short or option return.
    result["bearish_direction_diagnostics"] = [
        {
            "event_id": r["event_id"],
            "horizon": r["horizon"],
            "underlying_gross_return": r["price_details"]["gross_mid_return"],
            "direction_correct": r["price_details"]["gross_mid_return"] < 0,
        }
        for r in values
        if r["arm"] == "delayed"
        and r["price_details"]
        and assessment_map.get(r["event_id"], {}).get("assessment", {}).get("interpretation")
        == "bearish"
    ]
    days = {session(e["first_discovered_at"]).isoformat() for e in events if e["prospective"]}
    result["prospective_events_by_discovery_date"] = {
        d: sum(
            e["prospective"] and session(e["first_discovered_at"]).isoformat() == d for e in events
        )
        for d in sorted(days)
    }
    lines = [
        f"# {PROTOCOL} — SHADOW",
        f"Generated: {iso(now)}; frozen version: {VERSION}",
        f"Prospective events: {result['prospective_events']}; historical/nonprospective: {result['nonprospective_events']}; rejected: {result['rejected_events']}",
        f"Content retrieved: {len(contents)}; Codex started/completed/unavailable: {result['codex_started']}/{result['codex_completed']}/{result['codex_unavailable']}; budget 3/day, one process.",
        "Primary: AI minus Delayed on identical eligible event/entry quotes; cluster by entry trading session.",
        "Fast versus Delayed separates waiting cost. All choices LONG/CASH; missing AI/quotes remain UNAVAILABLE.",
        "Exits are 1/3/5 trading sessions after entry, in the final regular-session minute. Early closes/holidays use XNYS.",
        "Costs: observed ask-to-bid plus 5bp/side, assumed zero fees. Price returns exclude cash distributions.",
        "Daily adjusted-vs-raw previous-close evidence is required across the holding period; missing or changed adjustment metadata flags outcomes and excludes qualified comparisons.",
        "No broker writes, paid API calls, account compounding or established profitability.",
        "",
        "|Horizon sessions|Comparison|Matched events|Sessions|Mean net difference|Session SE|",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for h, comparison in result["comparison"].items():
        for name in ("primary_ai_minus_delayed", "waiting_delayed_minus_fast"):
            s = comparison[name]
            lines.append(
                f"|{h}|{name}|{s['opportunities']}|{s['sessions']}|{s['session_mean']}|{s['session_standard_error']}|"
            )
    lines += [
        "",
        "|Horizon|Arm|Complete|Pending|Missing|Action flagged|LONG|CASH|Mean net vs CASH|",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for h, c in result["comparison"].items():
        for arm, s in c["arms"].items():
            lines.append(
                f"|{h}|{arm}|{s['complete_n']}|{s['pending_n']}|{s['missing_n']}|{s['corporate_action_flagged_n']}|{s['long_n']}|{s['cash_n']}|{s['net_vs_cash']['session_mean']}|"
            )
    lines += [
        "",
        f"Latest SEC poll: {result['polls'][-1] if result['polls'] else 'NOT_RUN'}",
        f"Event categories: {result['categories']}",
        f"AI direction categories: {result['interpretations']}",
        f"Actual discovery latencies (s): {result['discovery_latencies_seconds']}",
        f"Actual Codex latencies (s): {result['analysis_latencies_seconds']}",
        "Detailed stages, rejected/missing events, choices, matched benchmark samples, forecasts and quote timestamps: daily-status.json.",
        "Raw evidence: immutable event_records in experience.sqlite3 and content-addressed evidence/. Historical connectivity is not prospective evidence.",
    ]
    directory = store.directory
    (directory / "reports").mkdir(exist_ok=True)
    text = "\n".join(lines) + "\n"
    for relative, body in (
        ("daily-status.json", canonical(result)),
        ("daily-report.md", text),
        (f"reports/{session(now).isoformat()}.md", text),
    ):
        path = directory / relative
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(body)
        temp.replace(path)
    if v5_directory:
        from .v5_supplement import supplement, write_report

        write_report(directory, supplement(v5_directory, now))
    return {
        k: result[k]
        for k in (
            "protocol",
            "version",
            "generated_at",
            "prospective_events",
            "codex_started",
            "codex_completed",
        )
    }
