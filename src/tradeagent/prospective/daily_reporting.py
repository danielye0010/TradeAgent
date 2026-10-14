"""Derived SHADOW selection diagnostics; never collect data or revise frozen records."""

import json
import math
import sqlite3
import statistics
import time
from datetime import datetime
from itertools import combinations
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
CAPITAL = 1000
LABELS = {
    "quant_only": "Quant Only",
    "codex_only": "Codex Only",
    "codex_price": "Codex Price",
    "codex_news": "Codex News",
    "quant_codex": "Quant + Codex",
    "quant_codex_news": "Quant + Codex News",
    "seeded_random": "Random",
}


def local(value):
    return datetime.fromtimestamp(value, NY).isoformat() if value is not None else "UNAVAILABLE"


def valid_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def daily_mean(values):
    return {
        "days": len(values),
        "mean": statistics.fmean(values) if values else None,
        "standard_error": statistics.stdev(values) / math.sqrt(len(values))
        if len(values) > 1
        else None,
    }


def ranking_quality(assessments, symbols, outcomes, assessment_status="VALID"):
    """Spearman with average realized ties; ordering accuracy excludes realized ties."""
    result = {
        "status": "UNAVAILABLE",
        "spearman": None,
        "pairwise_accuracy": None,
        "scored_pairs": 0,
        "tied_pairs": 0,
    }
    if (
        assessment_status != "VALID"
        or set(assessments) != set(symbols)
        or len({a["rank"] for a in assessments.values()}) != len(symbols)
    ):
        return {**result, "reason": "complete valid frozen ranking unavailable"}
    order = sorted(symbols, key=lambda s: assessments[s]["rank"])
    result["order"] = order
    if len(order) < 2:
        return {**result, "reason": "at least two candidates required"}
    if any(s not in outcomes for s in order):
        return {**result, "status": "PENDING", "reason": "complete candidate outcomes pending"}
    values = [outcomes[s]["base_net"] for s in order]
    ranks = [sum(v < x for v in values) + (sum(v == x for v in values) + 1) / 2 for x in values]
    preferred = list(range(len(order), 0, -1))
    xmean, ymean = statistics.fmean(preferred), statistics.fmean(ranks)
    denominator = math.sqrt(
        sum((x - xmean) ** 2 for x in preferred) * sum((y - ymean) ** 2 for y in ranks)
    )
    pairs = [(a, b) for a, b in combinations(values, 2) if a != b]
    return {
        **result,
        "status": "AVAILABLE",
        "reason": None if denominator else "constant realized returns",
        "spearman": sum((x - xmean) * (y - ymean) for x, y in zip(preferred, ranks, strict=True))
        / denominator
        if denominator
        else None,
        "pairwise_accuracy": sum(a > b for a, b in pairs) / len(pairs) if pairs else None,
        "scored_pairs": len(pairs),
        "tied_pairs": len(order) * (len(order) - 1) // 2 - len(pairs),
    }


class SavedCaptures:
    """Look up the final decision quote, not v2's earlier scanner quote."""

    def __init__(self, directory, now):
        self.directory, self.now, self.loaded = Path(directory), now, None

    def records(self):
        if self.loaded is None:
            self.loaded = []
            for path in sorted((self.directory / "captures").glob("*.json")):
                data = json.loads(path.read_text())
                if data["observed_at"] <= self.now:
                    self.loaded.append(data)
        return self.loaded

    def quote(self, decision, candidate):
        for data in self.records():
            if (
                data["observed_at"] == decision["market_observed_at"]
                and data["source"] == decision["source"]
                and data["evidence_kind"] == decision["evidence_kind"]
            ):
                return data.get("quotes", {}).get(candidate["symbol"])
        # The unrefreshed v1 scan may already contain the identical frozen quote.
        scanner = candidate.get("scanner", {})
        if scanner.get("observation_time") == decision["market_observed_at"]:
            return scanner.get("market_evidence", {}).get("quote")
        return None

    def missing_path(self, decision, candidate):
        benchmark = candidate["predictions"][0]["context"].get("benchmark", "SPY")
        data = max(
            (
                d
                for d in self.records()
                if d["source"] == decision["source"]
                and d["session_open"] == decision["session_open"]
                and d["evidence_kind"] in {decision["evidence_kind"], "historical_market"}
            ),
            key=lambda d: d["observed_at"],
            default=None,
        )
        if data is None:
            return {
                "symbol": candidate["symbol"],
                "status": "PENDING",
                "reason": "no saved outcome capture",
            }
        gaps = {}
        for symbol in (candidate["symbol"], benchmark):
            starts = {
                b["start"]
                for b in data["bars"]
                if b["symbol"] == symbol
                and b["available_at"] <= self.now
                and b["end"] - b["start"] == 60
            }
            gaps[symbol] = [
                local(t)
                for t in range(
                    int(decision["outcome_entry_time"]), int(decision["outcome_exit_time"]), 60
                )
                if t not in starts
            ]
        return {
            "symbol": candidate["symbol"],
            "status": "PENDING",
            "capture_observed_at": data["observed_at"],
            "missing_minutes": gaps,
            "reason": "requires complete synchronized symbol/benchmark paths; no interpolation",
        }


def cost_details(decision, candidate, outcome, captures):
    costs = candidate.get("outcome_costs", {})
    quote = captures.quote(decision, candidate) if captures else None
    spread = costs.get("observed_spread_bps")
    side = costs.get("base_side_cost")
    check = None
    if quote and all(valid_number(quote.get(k)) for k in ("bid", "ask")):
        measured = (quote["ask"] - quote["bid"]) / ((quote["ask"] + quote["bid"]) / 2) * 10000
        check = (
            valid_number(side)
            and valid_number(spread)
            and math.isclose(spread, measured, abs_tol=1e-7)
            and math.isclose(side, (max(1, measured / 2) + 1 + 0.1) / 10000, abs_tol=1e-12)
        )
    net_check = None
    if outcome and valid_number(side):
        net_check = math.isclose(
            outcome["base_net"], (1 + outcome["gross"]) * (1 - side) / (1 + side) - 1, abs_tol=1e-12
        )
    return {
        "quote_status": "AVAILABLE" if quote else "UNAVAILABLE",
        "quote": quote,
        "frozen_spread_bps": spread,
        "base_side_cost": side,
        "cost_arithmetic_valid": check,
        "net_arithmetic_valid": net_check,
    }


def evaluate(store, protocol, now, captures=None):
    """New exploratory statistics, grouped by evidence pool and one frozen session/day."""
    decisions = [
        d
        for d in store.records("decisions")
        if d.get("research_protocol") == protocol and d["decision_time"] <= now
    ]
    outcomes = {o["decision_id"]: o for o in store.records("outcomes") if o["resolved_at"] <= now}
    symbol_rows = store.records("symbol_outcomes")
    assessments = store.records("assessments")
    scans = {s["scan_id"]: s for s in store.records("scans")}
    terminal = {r["decision_id"] for r in store.records("resolution_failures")}
    sessions = [
        s
        for s in store.records("sessions")
        if s.get("protocol") == protocol and s.get("phase") == "FINAL"
    ]
    rows = []
    for d in decisions:
        did = d["decision_id"]
        o = outcomes.get(did) if did not in terminal else None
        candidates = {c["symbol"]: c for c in d["quantitative_observations"]}
        eligible = d["comparison_universe"]
        shortlist = [
            c["symbol"]
            for c in scans.get(d["scan_id"], {}).get("candidates", d["ranked_candidates"])
        ]
        observed = dict((o or {}).get("candidate_outcomes", {}))
        observed.update(
            {
                r["symbol"]: r["modeled_outcome"]
                for r in symbol_rows
                if r["decision_id"] == did
                and r["evidence_kind"] == d["evidence_kind"]
                and r["resolved_at"] <= now
            }
        )
        observed = {
            s: p
            for s, p in observed.items()
            if valid_number(p.get("base_net"))
            and p.get("entry_time") == d["outcome_entry_time"]
            and p.get("exit_time") == d["outcome_exit_time"]
        }
        if did in terminal:
            observed = {}
        complete = all(s in observed for s in eligible)
        basket = (
            statistics.fmean(observed[s]["base_net"] for s in eligible)
            if eligible and complete
            else None
        )
        tagged = {
            tag: {
                a["symbol"]: {"rank": a["rank"], "stance": a["stance"]}
                for a in assessments
                if a["scan_id"] == d["scan_id"] and a.get("assessor", "price") == tag
            }
            for tag in ("price", "news")
        }
        ranks = {
            tag: ranking_quality(
                tagged[tag],
                shortlist,
                observed,
                d.get(
                    "assessment_status" if tag == "price" else "news_assessment_status",
                    "UNAVAILABLE",
                ),
            )
            for tag in tagged
        }
        arms = {}
        for name, arm in d["comparisons"].items():
            p = (o or {}).get("comparisons", {}).get(name)
            unavailable = (
                arm["status"] != "VALID" or did in terminal or (o is not None and p is None)
            )
            resolved = not unavailable and p is not None and valid_number(p.get("base_net"))
            net = p["base_net"] if resolved else None
            arms[name] = {
                "symbol": arm["symbol"],
                "decision": "UNAVAILABLE"
                if unavailable
                else "LONG"
                if arm["shadow_direction"]
                else "CASH",
                "status": "UNAVAILABLE" if unavailable else "RESOLVED" if resolved else "PENDING",
                "entry_price": p.get("entry_price") if resolved else None,
                "exit_price": p.get("exit_price") if resolved else None,
                "gross": p.get("gross") if resolved else None,
                "net_return": net,
                "pnl_dollars": CAPITAL * net if net is not None else None,
                "excess_over_candidate_basket": net - basket
                if net is not None and basket is not None
                else None,
                "reason": arm.get("reason") if unavailable else None,
            }

        def selection(name, arms=arms):
            a = arms.get(name)
            return (a["symbol"], a["decision"]) if a and a["decision"] != "UNAVAILABLE" else None

        price_name = "codex_price" if "codex_price" in arms else "codex_only"
        changes = {}
        for tag, name in (("price", price_name), ("news", "codex_news")):
            q, a = selection("quant_only"), selection(name)
            changes["quant_vs_" + tag + "_selection"] = (
                q != a if q is not None and a is not None else None
            )
        both_ranked = all("order" in ranks[t] for t in tagged)
        changes["price_vs_news_top_rank"] = (
            ranks["price"]["order"][0] != ranks["news"]["order"][0]
            if both_ranked and shortlist
            else None
        )
        changes["price_vs_news_complete_ranking"] = (
            ranks["price"]["order"] != ranks["news"]["order"] if both_ranked else None
        )
        changes["price_vs_news_stance"] = (
            any(tagged["price"][s]["stance"] != tagged["news"][s]["stance"] for s in shortlist)
            if both_ranked
            else None
        )
        ps, ns = selection(price_name), selection("codex_news")
        changes["price_vs_news_selection"] = ps != ns if ps is not None and ns is not None else None
        session = next((s for s in sessions if s.get("decision_id") == did), {})
        searches = session.get("codex_news", {}).get("web_searches")
        consulted = (
            valid_number(searches) and searches > 0 and d.get("news_assessment_status") == "VALID"
        )
        price_net = arms.get(price_name, {}).get("net_return")
        news_net = arms.get("codex_news", {}).get("net_return")
        news = {
            "status": "CONSULTED"
            if consulted
            else "UNAVAILABLE"
            if d.get("news_assessment_status") != "VALID"
            else "SEARCH_NOT_OBSERVED",
            "observed_searches": searches,
            "changed_selection": changes["price_vs_news_selection"],
            "matched_net_difference": news_net - price_net
            if consulted and news_net is not None and price_net is not None
            else None,
        }
        missing = []
        for s, c in candidates.items():
            if c["predictions"] and s not in observed:
                missing.append(
                    captures.missing_path(d, c)
                    if captures
                    and did not in terminal
                    and d.get("outcome_exit_time") is not None
                    and now >= d["outcome_exit_time"]
                    else {
                        "symbol": s,
                        "status": "UNAVAILABLE" if did in terminal else "PENDING",
                        "reason": "complete saved outcome unavailable",
                    }
                )
        rows.append(
            {
                "session_date": datetime.fromtimestamp(d["decision_time"], NY).date().isoformat(),
                "decision_id": did,
                "evidence_kind": d["evidence_kind"],
                "decision_time": d["decision_time"],
                "market_observed_at": d["market_observed_at"],
                "entry_time": d["outcome_entry_time"],
                "exit_time": d["outcome_exit_time"],
                "capture": session,
                "resolved_at": (o or {}).get("resolved_at"),
                "resolution_status": "UNAVAILABLE"
                if did in terminal
                else "RESOLVED"
                if o
                else "PENDING",
                "coverage": {
                    "scanned": len(d["research_universe"]),
                    "eligible": len(eligible),
                    "complete_eligible": sum(s in observed for s in eligible),
                    "complete_scanned": sum(s in observed for s in candidates),
                    **{
                        kind: sum(a["decision"] == kind for a in arms.values())
                        for kind in ("LONG", "CASH", "UNAVAILABLE")
                    },
                },
                "basket": {
                    "status": "AVAILABLE"
                    if basket is not None
                    else "UNAVAILABLE"
                    if not eligible or did in terminal
                    else "PENDING",
                    "net_return": basket,
                    "symbols": eligible,
                    "missing_symbols": [s for s in eligible if s not in observed],
                },
                "candidate_returns": {s: observed.get(s) for s in eligible},
                "assessments": tagged,
                "costs": {
                    s: cost_details(d, candidates[s], observed.get(s), captures) for s in eligible
                },
                "ranking_quality": ranks,
                "disagreements": changes,
                "news": news,
                "arms": arms,
                "missing_outcomes": missing,
            }
        )
    pools = {}
    for pool in sorted({r["evidence_kind"] for r in rows}):
        days = [r for r in rows if r["evidence_kind"] == pool]
        if len({r["session_date"] for r in days}) != len(days):
            raise ValueError("duplicate frozen session in exploratory selection reporting")
        disagreements = {}
        for metric in days[0]["disagreements"]:
            valid = [
                r["disagreements"][metric] for r in days if r["disagreements"][metric] is not None
            ]
            disagreements[metric] = {
                "disagreement_days": sum(valid),
                "valid_days": len(valid),
                "percent": 100 * sum(valid) / len(valid) if valid else None,
                "unavailable_dates": [
                    r["session_date"] for r in days if r["disagreements"][metric] is None
                ],
            }
        pools[pool] = {
            "frozen_days": len(days),
            "disagreements": disagreements,
            "news_minus_price": daily_mean(
                [
                    r["news"]["matched_net_difference"]
                    for r in days
                    if r["news"]["matched_net_difference"] is not None
                ]
            ),
            "basket_excess": {
                name: daily_mean(
                    [
                        r["arms"][name]["excess_over_candidate_basket"]
                        for r in days
                        if r["arms"][name]["excess_over_candidate_basket"] is not None
                    ]
                )
                for name in days[0]["arms"]
            },
        }
    day = datetime.fromtimestamp(now, NY).date().isoformat()
    today = next((r for r in rows if r["session_date"] == day), None)
    return {
        "kind": "exploratory_saved_data_only",
        "protocol": protocol,
        "capital_dollars": CAPITAL,
        "generated_at": now,
        "session_date": day,
        "today": today,
        "sessions": rows,
        "evidence_pools": pools,
        "capture_failures": [s for s in sessions if s.get("status") != "COMPLETED"],
        "interpretation": "daily sessions are the inference unit; candidate pairs are descriptive, not independent days; incomplete cases remain visible",
    }


def render(evaluation, status):
    """Readable derivative, keeping original P&L separate from exploratory diagnostics."""

    def num(value, scale=1, digits=4):
        return "—" if value is None else f"{value * scale:.{digits}f}"

    def flag(value):
        return "UNAVAILABLE" if value is None else "YES" if value else "NO"

    e, row = evaluation, evaluation["today"]
    lines = [
        f"# TradeAgent SHADOW — {e['session_date']}",
        "",
        f"Protocol: {e['protocol']}; generated: {local(e['generated_at'])} (America/New_York).",
        "Saved evidence only. $1,000 per arm; modeled prices/costs, no broker fills.",
        "",
    ]
    run = status.get("run", {})
    if row is None:
        current = run if run.get("session_date") == e["session_date"] else {}
        lines += [
            f"Today: {current.get('status', 'PENDING')}; reason: {current.get('failure_kind') or 'no frozen decision for this date'}. No prior-day substitution.",
            "",
        ]
    else:
        capture = row["capture"]
        lines += [
            f"Evidence pool: {row['evidence_kind']}. Capture: {capture.get('status', 'UNAVAILABLE')} at {local(capture.get('recorded_at'))}; failure: {capture.get('failure_kind') or 'none'}.",
            f"Decision: {local(row['decision_time'])}; market receipt: {local(row['market_observed_at'])}.",
            f"Entry: {local(row['entry_time'])}; exit: {local(row['exit_time'])}; resolution: {row['resolution_status']} at {local(row['resolved_at'])}.",
            f"Latest saved pipeline tick: {local(status.get('tick_completed_at'))}; saved resolution errors: {sum(bool(r.get('failure_kind')) for r in status.get('resolution', []))}. Systemd exit is independently recorded in its journal.",
            "",
            "## Original frozen trading metrics",
            "",
            "| Strategy | Selection | Status | Entry | Exit | Gross % | Net % | P&L $ |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for name, arm in row["arms"].items():
            selected = arm["symbol"] or arm["decision"]
            lines.append(
                f"| {LABELS.get(name, name)} | {selected} | {arm['status']} | {num(arm['entry_price'])} | {num(arm['exit_price'])} | {num(arm['gross'], 100)} | {num(arm['net_return'], 100)} | {num(arm['pnl_dollars'], digits=2)} |"
            )
        lines += [
            "",
            "Combined v1/v2 rule: positive Quant signal AND Codex support for the selected symbol; rank the supported positive-Quant candidates by Codex rank, then Quant signal. First choices need not match.",
            "",
            "## Exploratory selection diagnostics",
            "",
            "Coverage: " + "; ".join(f"{k}={v}" for k, v in row["coverage"].items()) + ".",
            f"Candidate equal-weight basket: {row['basket']['status']}; net={num(row['basket']['net_return'], 100)}%; missing={row['basket']['missing_symbols']}.",
            "",
            "| Candidate | Price rank / stance | News rank / stance | Gross % | Net % |",
            "| --- | --- | --- | ---: | ---: |",
        ]
        for symbol, outcome in row["candidate_returns"].items():
            a = [row["assessments"][tag].get(symbol, {}) for tag in ("price", "news")]
            p = outcome or {}
            lines.append(
                f"| {symbol} | {a[0].get('rank', '—')} / {a[0].get('stance', 'UNAVAILABLE')} | {a[1].get('rank', '—')} / {a[1].get('stance', 'UNAVAILABLE')} | {num(p.get('gross'), 100)} | {num(p.get('base_net'), 100)} |"
            )
        lines += ["", "Selected net minus the same complete candidate basket (percentage points):"]
        lines += [
            f"- {LABELS.get(n, n)}: {num(a['excess_over_candidate_basket'], 100)}"
            for n, a in row["arms"].items()
        ]
        lines += [
            "",
            "| Ranking | Status | Spearman | Pairwise accuracy % | Scored pairs | Realized ties |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
        for tag, quality in row["ranking_quality"].items():
            lines.append(
                f"| {tag} | {quality['status']} | {num(quality['spearman'])} | {num(quality['pairwise_accuracy'], 100)} | {quality['scored_pairs']} | {quality['tied_pairs']} |"
            )
        lines += [
            "",
            "Rank association uses average ranks for realized ties. Pairwise accuracy excludes realized ties; pairs are not independent trading days.",
            "",
        ]
        lines += [f"- {name}: {flag(changed)}" for name, changed in row["disagreements"].items()]
        news = row["news"]
        lines += [
            f"- News: {news['status']}; observed searches={news['observed_searches']}; changed selected decision={flag(news['changed_selection'])}; matched News minus Price={num(news['matched_net_difference'], 100)} percentage points.",
            "",
            "## Frozen costs and data timestamps",
            "",
            "| Candidate | Bid | Ask | Spread bps | Per-side cost bps | Quote / cost check | Net check |",
            "| --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
        quote_times = []
        for s, c in row["costs"].items():
            q = c["quote"] or {}
            lines.append(
                f"| {s} | {num(q.get('bid'))} | {num(q.get('ask'))} | {num(c['frozen_spread_bps'])} | {num(c['base_side_cost'], 10000)} | {c['quote_status']} / {flag(c['cost_arithmetic_valid'])} | {flag(c['net_arithmetic_valid'])} |"
            )
            quote_times.append(
                f"- {s} quote asof={local(q.get('asof'))}; receipt={local(q.get('observed_at'))}."
            )
        lines += ["", *quote_times]
        lines += [
            "",
            "Base per-side cost = (max(1 bp, observed spread / 2) + 1 bp slippage + 0.1 bp fee) / 10,000. Net = (1 + gross) × (1 − side cost) / (1 + side cost) − 1. Costs use the final decision quote; v2 refresh can differ from its earlier scanner quote. Historical cost values are preserved.",
            "",
        ]
    lines += ["## Missing data", ""]
    missing = [(r["session_date"], m) for r in e["sessions"] for m in r["missing_outcomes"]]
    lines += [f"- {day}: {json.dumps(m, sort_keys=True)}" for day, m in missing] or [
        "No pending candidate outcomes in saved decisions."
    ]
    lines += [
        f"- Capture failure: {s['session_date']} / {s.get('failure_kind', s.get('status'))}"
        for s in e["capture_failures"]
    ]
    lines += ["", "## Cumulative original metrics (separate from today's result)", ""]
    for pool, summary in status.get("comparison", {}).get("evidence_pools", {}).items():
        lines += [
            f"{pool}: frozen={summary.get('frozen_sessions')}; resolved={summary.get('resolved_sessions')}; valid paired days={summary.get('valid_paired_days')}. Arithmetic daily means, not compounded account P&L."
        ]
        lines += [
            f"- {LABELS.get(n, n)}: mean net={num(a.get('paired_mean_base_net'), 100)}%."
            for n, a in summary.get("arms", {}).items()
        ]
    lines += ["", "## Cumulative exploratory diagnostics", ""]
    for pool, summary in e["evidence_pools"].items():
        lines += [f"{pool}: {summary['frozen_days']} frozen days; exclusions remain explicit."]
        for metric, m in summary["disagreements"].items():
            lines.append(
                f"- {metric}: {m['disagreement_days']}/{m['valid_days']} valid days ({num(m['percent'], digits=2)}%); unavailable dates={m['unavailable_dates']}."
            )
        for name, m in summary["basket_excess"].items():
            lines.append(
                f"- {LABELS.get(name, name)} minus basket: {m['days']} daily pairs, mean={num(m['mean'], 100)} pp; daily SE={num(m['standard_error'], 100)} pp."
            )
        m = summary["news_minus_price"]
        lines.append(
            f"- Consulted News minus Price: {m['days']} matched days, mean={num(m['mean'], 100)} pp; daily SE={num(m['standard_error'], 100)} pp."
        )
    lines += [
        "",
        "Exploratory descriptions are not evidence of a repeatable edge. Absolute P&L and relative candidate selection performance are different quantities.",
        "",
    ]
    return "\n".join(lines)


def write_report(directory, text):
    path = Path(directory) / "daily-report.md"
    temporary = path.with_suffix(".md.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    return path


def regenerate(directory, protocol, now=None):
    """Read-only SQLite snapshot; write only the derived Markdown report."""
    directory, now = Path(directory), time.time() if now is None else now
    db = sqlite3.connect(
        (directory / "experience.sqlite3").resolve().as_uri() + "?mode=ro", uri=True
    )
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        records = {
            kind: [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM opportunity_" + kind + " ORDER BY recorded_at"
                )
            ]
            if "opportunity_" + kind in tables
            else []
            for kind in (
                "decisions",
                "outcomes",
                "symbol_outcomes",
                "scans",
                "assessments",
                "sessions",
                "resolution_failures",
            )
        }
    finally:
        db.close()

    class Snapshot:
        def records(self, kind):
            return records[kind]

    status = json.loads((directory / "daily-status.json").read_text())
    if status["protocol"] != protocol:
        raise ValueError("report protocol does not match saved state")
    if protocol == "daily-bidirectional-v3":
        from ..research.bidirectional import pnl_report

        text = pnl_report(Snapshot())
        result = {"protocol": protocol, "status": "SAVED_DATA_ONLY"}
    else:
        result = evaluate(Snapshot(), protocol, now, SavedCaptures(directory, now))
        text = render(result, status)
    path = write_report(directory, text)
    return {
        "protocol": protocol,
        "report": str(path),
        "generated_at": now,
        "today": result.get("today"),
        "orders_submitted": 0,
    }
