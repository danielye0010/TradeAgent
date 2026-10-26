"""Complete paper-only projection and descriptive paired-week comparisons."""

import json
import math
import statistics
from datetime import date, timedelta
from pathlib import Path

from . import ledger
from .protocol import ARMS, BOOKS, CONFIG_HASH, PROTOCOL, day_at, dec, next_week


def paired(values):
    return {
        "n": len(values),
        "mean": statistics.mean(values) if values else None,
        "standard_error": statistics.stdev(values) / math.sqrt(len(values))
        if len(values) > 1
        else None,
        "interpretation": "descriptive SE; weeks may be serially dependent; no significance claim",
    }


def snapshot(store):
    valuations = list(store.rows("valuation"))
    packets = sorted(store.rows("packet"), key=lambda r: r["week"])
    decisions = {r["week"]: r for r in store.rows("decision")}
    missing = list(store.rows("missing"))
    delays = list(store.rows("delay")) + list(store.rows("buy_delay"))
    trades = [r for r in store.rows("ledger") if r.get("side")]
    books, weeks = {}, []
    for book in BOOKS:
        state = ledger.mature(store.state(book), day_at(store.clock()))
        marks = [v for v in valuations if v["book"] == book]
        last = marks[-1] if marks else None
        series = [
            {
                "date": v["date"],
                "nav": float(v["nav"]) if v["nav"] is not None else None,
                "cash_fraction": v["cash_fraction"],
            }
            for v in marks
        ]
        peak, drawdown = 10000, 0.0
        for mark in series:
            if mark["nav"] is not None:
                peak = max(peak, mark["nav"])
                drawdown = min(drawdown, mark["nav"] / peak - 1)
        closed = state["closed_lots"]
        total_qty = sum((float(x["qty"]) for x in closed), 0.0)
        books[book] = {
            "nav": float(last["nav"])
            if last and last["nav"] is not None
            else (float(ledger.total_cash(state)) if not state["positions"] else None),
            "cumulative_return": float(last["nav"]) / 10000 - 1
            if last and last["nav"] is not None
            else None,
            "cash": float(ledger.total_cash(state)),
            "settled_cash": float(dec(state["settled"])),
            "pending_cash": state["pending"],
            "nav_date": last["date"] if last else None,
            "positions": [
                {
                    "symbol": s,
                    "shares": str(ledger.shares(state, s)),
                    "wac": str(ledger.wac(state, s)),
                    "holding_days": (
                        store.clock() - min(lot["opened_at"] for lot in state["positions"][s])
                    )
                    / 86400,
                }
                for s in state["positions"]
            ],
            "nav_series": series,
            "drawdown": drawdown,
            "turnover": float(state["turnover"]) / 10000,
            "modeled_cost": float(state["costs"]),
            "mean_cash_fraction": statistics.mean(
                v["cash_fraction"] for v in marks if v["cash_fraction"] is not None
            )
            if any(v["cash_fraction"] is not None for v in marks)
            else None,
            "mean_completed_holding_days": sum(
                float(x["qty"]) * x["seconds"] / 86400 for x in closed
            )
            / total_qty
            if total_qty
            else None,
            "missing": sum(r.get("book") in (book, "all-active") for r in missing),
            "delays": sum(r.get("book") == book for r in delays),
        }
    for packet in packets:
        start = packet["week"]
        end = str(next_week(date.fromisoformat(start)))
        following = next((p for p in packets if p["week"] == end), None)
        decision = decisions.get(start)
        row = {
            "week": start,
            "end": end,
            "returns": {},
            "coverage": {},
            "status": "COMPLETE" if following else "PENDING",
        }
        for book in BOOKS:
            arm = book.split(":")[1]
            valid = bool(decision and decision["arms"][arm]["status"] == "VALID")
            if arm == "spy":
                first = decisions.get(store.activation["start_date"])
                valid = bool(
                    first
                    and first["arms"]["spy"]["status"] == "VALID"
                    and any(t["book"] == book and t["symbol"] == "SPY" for t in trades)
                )
            gaps = [
                m
                for m in missing
                if m.get("week") == start
                and m.get("book") in (book, "all-active")
                and not m["reason"].startswith("settled cash unavailable")
            ]
            valid = valid and not gaps
            row["coverage"][book] = "VALID" if valid else "MISSING"
            a = packet["endpoints"].get(book)
            b = following["endpoints"].get(book) if following else None
            row["returns"][book] = (
                float(dec(b) / dec(a) - 1)
                if valid and a is not None and b is not None and dec(a) > 0
                else None
            )
        row["metrics"] = {}
        for book in BOOKS:
            fills = [t for t in trades if t["book"] == book and start <= t["date"] < end]
            marks = [v for v in valuations if v["book"] == book and v["week"] == start]
            row["metrics"][book] = {
                "modeled_cost": sum(float(t["modeled_cost"]) for t in fills),
                "gross_turnover_dollars": sum(float(t["notional"]) for t in fills),
                "mean_cash_fraction": statistics.mean(
                    v["cash_fraction"] for v in marks if v["cash_fraction"] is not None
                )
                if any(v["cash_fraction"] is not None for v in marks)
                else None,
                "missing": sum(
                    m.get("book") in (book, "all-active") and m.get("week") == start
                    for m in missing
                ),
                "delays": sum(
                    d.get("book") == book and start <= d.get("date", "") < end for d in delays
                ),
            }
        weeks.append(row)
    comparisons = {}
    for mode in ("main", "settled"):
        for arm, other in (
            ("codex", "random"),
            ("claude", "random"),
            ("codex", "claude"),
            *((a, b) for a in ARMS for b in ("spy", "quant") if a != b),
        ):
            values = [
                w["returns"][mode + ":" + arm] - w["returns"][mode + ":" + other]
                for w in weeks
                if w["returns"][mode + ":" + arm] is not None
                and w["returns"][mode + ":" + other] is not None
            ]
            comparisons[f"{mode}:{arm}-minus-{other}"] = {
                **paired(values),
                "primary": mode == "main" and other == "random" and arm in ("codex", "claude"),
            }
    # Use the previous observed day's exposure for each next-day SPY return. Gaps break chains.
    exposure = {}
    for mode in ("main", "settled"):
        spy = {v["date"]: v for v in valuations if v["book"] == mode + ":spy"}
        for arm in ARMS[:-1]:
            marks = [v for v in valuations if v["book"] == mode + ":" + arm]
            values = []
            for previous, current in zip(marks, marks[1:], strict=False):
                a, b = spy.get(previous["date"]), spy.get(current["date"])
                from .protocol import next_session

                if str(next_session(date.fromisoformat(previous["date"]))) != current["date"]:
                    continue
                if a and b and a["nav"] and b["nav"] and previous["cash_fraction"] is not None:
                    values.append(
                        (float(b["nav"]) / float(a["nav"]) - 1) * (1 - previous["cash_fraction"])
                    )
            exposure[mode + ":" + arm] = {
                "observed_daily_intervals": len(values),
                "matched_spy_return": math.prod(1 + x for x in values) - 1 if values else None,
                "method": "previous close invested fraction times next observed daily SPY total return; gaps excluded",
            }
    for mode in ("main", "settled"):
        for arm in ARMS[:-1]:
            book = mode + ":" + arm
            weekly = []
            for week in weeks:
                own = week["returns"].get(book)
                spy_return = week["returns"].get(mode + ":spy")
                marks = [v for v in valuations if v["book"] == book and v["week"] == week["week"]]
                from .protocol import sessions

                expected = len(
                    list(
                        sessions(week["week"], date.fromisoformat(week["end"]) - timedelta(days=1))
                    )
                )
                if (
                    own is None
                    or spy_return is None
                    or len(marks) != expected
                    or any(v["cash_fraction"] is None for v in marks)
                ):
                    continue
                exposure_fraction = statistics.mean(1 - v["cash_fraction"] for v in marks)
                matched = spy_return * exposure_fraction
                weekly.append(
                    {
                        "week": week["week"],
                        "mean_observed_invested_fraction": exposure_fraction,
                        "spy_return": spy_return,
                        "matched_spy_return": matched,
                        "net_excess": own - matched,
                    }
                )
            exposure[book]["weekly"] = weekly
            exposure[book]["weekly_method"] = (
                "same-week SPY total return scaled by mean observed daily invested fraction; descriptive exposure control, not beta-adjusted alpha"
            )
            comparisons[book + "-minus-exposure-matched-spy"] = {
                **paired([r["net_excess"] for r in weekly]),
                "primary": False,
            }
    public_decisions = [
        {
            "week": d["week"],
            "decision_hash": d["decision_hash"],
            "arms": {
                arm: {
                    "status": answer["status"],
                    "selection": answer.get("selection"),
                    "reason": answer.get("reason"),
                    "model": (answer.get("provenance") or {}).get("model"),
                    "cli_version": (answer.get("provenance") or {}).get("cli_version"),
                    "web_searches": (answer.get("provenance") or {}).get("web_searches"),
                }
                for arm, answer in d["arms"].items()
            },
        }
        for d in decisions.values()
    ]
    return {
        "protocol": PROTOCOL,
        "schema": 1,
        "mode": "SHADOW",
        "status": "PENDING_START" if not decisions else "UNVALIDATED",
        "evidence_kind": store.evidence,
        "config_hash": CONFIG_HASH,
        "implementation_hash": store.activation["implementation_hash"],
        "start_date": store.activation["start_date"],
        "generated_at": store.clock(),
        "real_orders_submitted": 0,
        "books": books,
        "weeks": weeks,
        "decisions": public_decisions,
        "comparisons": comparisons,
        "exposure_matched_spy": exposure,
        "missing": [{k: v for k, v in m.items() if k not in {"id", "at", "kind"}} for m in missing],
        "delays": [{k: v for k, v in m.items() if k not in {"id", "at", "kind"}} for m in delays],
        "trade_count": len(trades),
        "coverage": {
            a: {
                "scheduled_weeks": len(decisions),
                "valid_decisions": sum(
                    d["arms"][a]["status"] == "VALID" for d in decisions.values()
                ),
            }
            for a in ARMS
        },
        "limitations": [
            "Unvalidated prospective paper policy; modeled fills, not actual executions",
            "Pending dividend payable dates are receivables, not available trading cash",
            "Weeks may be serially dependent; descriptive SE is not proof of alpha",
            "Both primary model comparisons must be retained; no winner selection",
            "Missing performance is excluded from inference and retained in coverage/NAV records",
        ],
    }


def write_reports(store):
    data = snapshot(store)
    directory = store.directory
    text = json.dumps(data, indent=2, allow_nan=False)
    temporary = directory / "status.tmp"
    temporary.write_text(text)
    temporary.replace(directory / "status.json")
    lines = [
        "# weekly-hold-v1 — SHADOW / UNVALIDATED",
        "",
        f"Prospective start: {data['start_date']}; CONFIG: {CONFIG_HASH}",
        "",
        "All five groups and both settlement models are shown. No live orders.",
        "",
        "|Book|NAV|Cash|Costs|Drawdown|Turnover|Missing|Delays|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, b in data["books"].items():
        value = f"{b['nav']:.4f}" if b["nav"] is not None else "UNAVAILABLE"
        lines.append(
            f"|{name}|{value}|{b['cash']:.4f}|{b['modeled_cost']:.4f}|{b['drawdown']:.4%}|{b['turnover']:.4f}|{b['missing']}|{b['delays']}|"
        )
    lines += [
        "",
        "## Weekly paired comparisons",
        "",
        "|Comparison|n|Mean net difference|Descriptive SE|",
        "|---|---:|---:|---:|",
    ]
    for name, c in data["comparisons"].items():
        mean = f"{c['mean']:.6%}" if c["mean"] is not None else "UNAVAILABLE"
        se = f"{c['standard_error']:.6%}" if c["standard_error"] is not None else "UNAVAILABLE"
        lines.append(f"|{name}|{c['n']}|{mean}|{se}|")
    lines += [
        "",
        "## Limitations",
        "",
        *["- " + s for s in data["limitations"]],
        "",
        "## Missing and delayed observations",
        "",
        "```json",
        json.dumps({"missing": data["missing"], "delays": data["delays"]}, indent=2),
        "```",
        "",
        "Full holdings, daily NAV, weekly returns, exposure, coverage, sources and advice: status.json.",
    ]
    for name in ("daily-report.md", "weekly-report.md"):
        (directory / name).write_text("\n".join(lines) + "\n")
    archive = directory / "reports"
    archive.mkdir(exist_ok=True)
    key = store.artifact(data)
    path = archive / (key + ".json")
    if not path.exists():
        path.write_text(text)
    return data


def read_public(directory):
    path = Path(directory) / "status.json"
    if not path.exists():
        return {
            "protocol": PROTOCOL,
            "status": "NOT_DEPLOYED",
            "books": {},
            "weeks": [],
            "comparisons": {},
            "missing": [],
            "delays": [],
        }
    data = json.loads(path.read_text())
    if data.get("protocol") != PROTOCOL or data.get("evidence_kind") != "prospective":
        raise ValueError("public weekly view accepts prospective weekly-hold-v1 only")
    return data
