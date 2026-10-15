"""Continuous Quant v5: read-only forward signal decay, independent of LLMs/execution."""

import argparse
import json
import math
import sqlite3
import statistics
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..calendar import session_bounds
from ..research.domain import canonical, identity, iso
from ..research.opportunities import UNIVERSE, capture, scan
from ..research.research_universe import liquid_candidates
from ..research.store import Experience

NY = ZoneInfo("America/New_York")
PROTOCOL = "continuous-quant-v5"
CONFIG = {
    "protocol": PROTOCOL,
    "hypothesis": "rolling-relative-momentum-v1",
    "mode": "SHADOW",
    "universe": UNIVERSE,
    "scan_seconds": 300,
    "window_minutes": 15,
    "minimum_bars": 15,
    "signal": "abs(return)>=max(0.003,1.5*rv), abs(relative)>=0.002, same sign",
    "expected_return": "clip(0.5*relative,-0.03,0.03); UNCALIBRATED",
    "liquidity": "spread<=0.005; completed trailing mean dollar volume>=1000000/min",
    "delays": [60, 180, 300, 600],
    "exit_seconds": 3600,
    "endpoint_window_seconds": 60,
    "quote_max_age_seconds": 15,
    "slippage_per_side": 0.0005,
    "fees_fraction": 0.0,
    "fees_status": "assumed zero; not observed",
    "entry_stale": "direction*mid/detection_mid drift>=abs(frozen expected) or spread>0.005",
    "bearish": "theoretical short underlying; excludes borrow/margin; no executable claim",
    "comparison": "all four delays matched; session mean clusters; no account compounding",
}
VERSION = identity(CONFIG)


def day_of(now):
    return datetime.fromtimestamp(now, NY).date()


def slots(day):
    bounds = session_bounds(day)
    if not bounds:
        return []
    start = bounds[0] + 900
    stop = min(bounds[0] + 19800, bounds[1] - 60)  # 15:00 ET; early close aware
    return list(range(int(start), int(stop) + 1, CONFIG["scan_seconds"]))


def period(now):
    t = datetime.fromtimestamp(now, NY)
    return "morning" if t.hour < 12 else "midday" if t.hour < 14 else "afternoon"


def quote_ok(q, now, target=None):
    try:
        values = [q[k] for k in ("bid", "ask", "asof", "observed_at")]
        return (
            all(math.isfinite(v) for v in values)
            and 0 < q["bid"] <= q["ask"]
            and q["asof"] <= q["observed_at"] <= now
            and all(
                q["asof"] <= q.get(side, q["asof"]) <= q["observed_at"]
                for side in ("bid_asof", "ask_asof")
            )
            and q.get("active", True)
            and q["observed_at"] - q["asof"] <= CONFIG["quote_max_age_seconds"]
            and (target is None or target <= q["asof"] <= q["observed_at"] < target + 60)
        )
    except (KeyError, TypeError, ValueError):
        return False


def mid(q):
    return (q["bid"] + q["ask"]) / 2


def features(data, now):
    report = scan(data, now=now, window_minutes=15, candidate_count=31)
    liquid_candidates(report, data)
    rows = []
    for c in report["candidate_pool"]:
        f, evidence = c["features"], c.get("market_evidence", {})
        valid = (
            c.get("liquidity", {}).get("eligible", False)
            and not c["limitations"]
            and evidence.get("completed_bars") == 15
            and quote_ok(data.get("quotes", {}).get(c["symbol"]), now)
        )
        r, relative = f.get("window_return", 0), f.get("relative_strength", 0)
        active = (
            valid
            and abs(r) >= max(0.003, 1.5 * f["realized_volatility"])
            and abs(relative) >= 0.002
            and r * relative > 0
        )
        rows.append(
            {
                **c,
                "valid": valid,
                "direction": (1 if r > 0 else -1) if active else 0,
                "expected_uncalibrated": max(-0.03, min(0.03, 0.5 * relative)),
            }
        )
    return rows


class Pipeline:
    def __init__(self, directory, *, clock=time.time, collector=capture, oauth_helper=None):
        self.directory, self.clock, self.collector = Path(directory), clock, collector
        self.oauth_helper = oauth_helper
        existing_db = self.directory / "experience.sqlite3"
        if existing_db.exists():
            with sqlite3.connect(f"file:{existing_db}?mode=ro", uri=True) as check:
                if not check.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='continuous_events'"
                ).fetchone():
                    raise ValueError(
                        "v5 requires independent state; existing research state refused"
                    )
        self.store = Experience(self.directory)
        self.db = self.store.db
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS continuous_events "
            "(kind TEXT, key TEXT, at REAL, payload TEXT, PRIMARY KEY(kind,key))"
        )
        for op in ("UPDATE", "DELETE"):
            self.db.execute(
                f"CREATE TRIGGER IF NOT EXISTS continuous_immutable_{op} "
                f"BEFORE {op} ON continuous_events BEGIN "
                "SELECT RAISE(ABORT,'immutable continuous evidence'); END"
            )
        self.db.commit()
        existing = self.records("config")
        if existing and existing[0]["version"] != VERSION:
            self.store.close()
            raise ValueError("protocol configuration changed: use a separate state directory")
        self.add(
            "config", PROTOCOL, {"version": VERSION, "config": CONFIG, "activated_at": self.clock()}
        )

    def add(self, kind, key, payload):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO continuous_events VALUES(?,?,?,?)",
                (kind, str(key), self.clock(), canonical(payload)),
            )

    def records(self, kind):
        return [
            json.loads(r[0])
            for r in self.db.execute(
                "SELECT payload FROM continuous_events WHERE kind=? ORDER BY at,key", (kind,)
            )
        ]

    def has(self, kind, key):
        return (
            self.db.execute(
                "SELECT 1 FROM continuous_events WHERE kind=? AND key=?", (kind, str(key))
            ).fetchone()
            is not None
        )

    def collect(self, symbols, *, quotes_only=False):
        if self.collector is capture:
            busy = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "is-active",
                    "--quiet",
                    "tradeagent-daily-research.service",
                    "tradeagent-daily-news.service",
                ],
                check=False,
                capture_output=True,
                timeout=5,
            )
            if busy.returncode == 0:
                raise ValueError("deferred: existing v1/v2 capture has priority")
        started = self.clock()
        data = self.collector(
            symbols,
            oauth_helper=self.oauth_helper,
            deadline=started + 40,
            lookback_minutes=30,
            quotes_only=quotes_only,
        )
        received = self.clock()
        if (
            data["evidence_kind"] != "prospective"
            or not started <= data["requested_at"] <= data["observed_at"] <= received
        ):
            raise ValueError("forward capture identity/clock required")
        key = identity(data)
        captures = self.directory / "captures"
        captures.mkdir(exist_ok=True)
        path = captures / (key + ".json")
        if not path.exists():
            with path.open("x") as handle:
                handle.write(canonical(data))
        self.add(
            "capture",
            key,
            {
                "capture_id": key,
                "requested_at": started,
                "received_at": received,
                "seconds": received - started,
                "bytes": path.stat().st_size,
                "bars": len(data["bars"]),
                "quotes": len(data["quotes"]),
                "quotes_only": quotes_only,
            },
        )
        return data, key

    def scan_slot(self, slot):
        if self.has("scan_started", slot):
            return
        self.add("scan_started", slot, {"slot": slot, "started_at": self.clock()})
        try:
            data, capture_id = self.collect(UNIVERSE)
            now = self.clock()
            if now >= slot + 60:
                raise ValueError("scan missed its frozen 60-second slot budget")
            rows = features(data, now)
            basket = [r["symbol"] for r in rows if r["valid"]]
            previous = {}
            for s in self.records("scan"):
                if day_of(s["detected_at"]) == day_of(now):
                    for r in s["rows"]:
                        if r["valid"]:
                            previous[r["symbol"]] = r["direction"]
            opportunities = []
            for row in rows:
                old, direction = previous.get(row["symbol"], 0), row["direction"]
                row["transition"] = (
                    "UNKNOWN"
                    if not row["valid"]
                    else "NEW"
                    if direction and direction != old
                    else "CONTINUING"
                    if direction
                    else "EXPIRED"
                    if old
                    else "ABSTAIN"
                )
                if row["transition"] != "NEW":
                    continue
                q = data["quotes"][row["symbol"]]
                opportunity = {
                    "id": identity([VERSION, slot, row["symbol"], direction]),
                    "version": VERSION,
                    "session": day_of(now).isoformat(),
                    "detected_at": now,
                    "slot": slot,
                    "symbol": row["symbol"],
                    "family": CONFIG["hypothesis"],
                    "direction": direction,
                    "period": period(now),
                    "features": row["features"],
                    "magnitude": abs(row["features"]["relative_strength"]),
                    "expected_uncalibrated": row["expected_uncalibrated"],
                    "liquidity": row["liquidity"],
                    "quote": q,
                    "spread_fraction": (q["ask"] - q["bid"]) / mid(q),
                    "basket": basket,
                    "capture_id": capture_id,
                    "costs": {
                        "slippage_per_side": CONFIG["slippage_per_side"],
                        "fees_fraction": CONFIG["fees_fraction"],
                        "spread": "observed entry/exit sides; do not subtract twice",
                    },
                    "exit_at": now + 3600,
                    "eligible": now + 3660 <= data["session_close"],
                }
                opportunities.append(opportunity)
            # A complete scan plus its new opportunities commits atomically.
            with self.db:
                self.db.execute(
                    "INSERT INTO continuous_events VALUES(?,?,?,?)",
                    (
                        "scan",
                        str(slot),
                        now,
                        canonical(
                            {
                                "slot": slot,
                                "detected_at": now,
                                "capture_id": capture_id,
                                "rows": rows,
                                "coverage": len(basket),
                                "observed_symbols": len(
                                    {b["symbol"] for b in data["bars"]} & set(UNIVERSE)
                                ),
                            }
                        ),
                    ),
                )
                for o in opportunities:
                    self.db.execute(
                        "INSERT INTO continuous_events VALUES(?,?,?,?)",
                        ("opportunity", o["id"], now, canonical(o)),
                    )
        except Exception as exc:
            self.add(
                "scan_failure",
                slot,
                {"slot": slot, "reason": str(exc), "recorded_at": self.clock()},
            )

    def endpoints(self):
        now = self.clock()
        due = []
        for o in self.records("opportunity"):
            if not o["eligible"]:
                continue
            for delay in [*CONFIG["delays"], 3600]:
                key, target = o["id"] + ":" + str(delay), o["detected_at"] + delay
                if not self.has("endpoint", key) and now >= target:
                    due.append((o, delay, key, target))
        live = [r for r in due if now < r[3] + 60]
        data, capture_id, error = None, None, None
        if live:
            symbols = sorted({s for o, _, _, _ in live for s in [o["symbol"], *o["basket"], "SPY"]})
            try:
                data, capture_id = self.collect(symbols, quotes_only=True)
            except Exception as exc:
                error = str(exc)
        for o, delay, key, target in due:
            quotes = data["quotes"] if data and (o, delay, key, target) in live else {}
            accepted = {s: q for s, q in quotes.items() if quote_ok(q, self.clock(), target)}
            self.add(
                "endpoint",
                key,
                {
                    "opportunity_id": o["id"],
                    "delay": delay,
                    "target_at": target,
                    "observed_at": self.clock(),
                    "quotes": accepted,
                    "capture_id": capture_id,
                    "status": "OBSERVED" if o["symbol"] in accepted else "MISSING",
                    "reason": error
                    or (None if o["symbol"] in accepted else "missed/stale/absent quote"),
                },
            )

    def tick(self):
        with self.store.lock():
            changes = self.db.total_changes
            now = self.clock()
            self.endpoints()  # forward endpoints have priority over discovery
            activated = self.records("config")[0]["activated_at"]
            for slot in slots(day_of(now)):
                if slot >= activated and slot <= now < slot + 20:
                    self.scan_slot(slot)
            self.endpoints()
            for slot in slots(day_of(now)):
                if activated <= slot and slot + 60 <= now and not self.has("scan", slot):
                    self.add("scan_missed", slot, {"slot": slot, "recorded_at": now})
            # No report parsing/rewriting on idle 15-second scheduler ticks.
            if self.db.total_changes == changes and (self.directory / "daily-status.json").exists():
                return {
                    "protocol": PROTOCOL,
                    "version": VERSION,
                    "generated_at": now,
                    "scan_count": self.db.execute(
                        "SELECT count(*) FROM continuous_events WHERE kind='scan'"
                    ).fetchone()[0],
                    "unique_opportunities": self.db.execute(
                        "SELECT count(*) FROM continuous_events WHERE kind='opportunity'"
                    ).fetchone()[0],
                }
            return self.report()

    def report(self):
        now = self.clock()
        result = summarize(self, now)
        today = summarize(self, now, session=day_of(now).isoformat())
        reports = self.directory / "reports"
        reports.mkdir(exist_ok=True)
        for suffix, content in (("json", canonical(today)), ("md", render(today))):
            path = reports / f"{day_of(now).isoformat()}.{suffix}"
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(content)
            temporary.replace(path)
        for name, content in (
            ("daily-status.json", canonical(result)),
            ("daily-report.md", render(today)),
            ("cumulative-report.md", render(result)),
        ):
            path = self.directory / name
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(content)
            temporary.replace(path)
        return result


def modeled(o, entry, exit_q, symbol, direction=1):
    eq, xq = entry.get(symbol), exit_q.get(symbol)
    if not eq or not xq:
        return None
    ep, xp = (eq["ask"], xq["bid"]) if direction == 1 else (eq["bid"], xq["ask"])
    gross = direction * (mid(xq) / mid(eq) - 1)
    side_return = direction * (xp / ep - 1)
    slip = 2 * o["costs"]["slippage_per_side"] + o["costs"]["fees_fraction"]
    return {
        "entry_price": ep,
        "entry_quote_at": eq["asof"],
        "entry_observed_at": eq["observed_at"],
        "exit_price": xp,
        "exit_quote_at": xq["asof"],
        "exit_observed_at": xq["observed_at"],
        "actual_holding_seconds": xq["asof"] - eq["asof"],
        "gross_mid_return": gross,
        "observed_spread_cost": gross - side_return,
        "slippage_fees": slip,
        "net_modeled_return": side_return - slip,
    }


def cluster(values):
    sessions = {}
    for session, value in values:
        sessions.setdefault(session, []).append(value)
    means = [statistics.fmean(v) for v in sessions.values()]
    return {
        "opportunities": len(values),
        "sessions": len(means),
        "session_mean": statistics.fmean(means) if means else None,
        "session_standard_error": statistics.stdev(means) / len(means) ** 0.5
        if len(means) > 1
        else None,
    }


def summarize(pipeline, now, session=None):
    scans, opportunities = pipeline.records("scan"), pipeline.records("opportunity")
    if session:
        scans = [s for s in scans if day_of(s["detected_at"]).isoformat() == session]
        opportunities = [o for o in opportunities if o["session"] == session]
    endpoints = {(r["opportunity_id"], r["delay"]): r for r in pipeline.records("endpoint")}
    outcomes = []
    for o in opportunities:
        for delay in CONFIG["delays"]:
            e, x = endpoints.get((o["id"], delay)), endpoints.get((o["id"], 3600))
            eq, xq = (e or {}).get("quotes", {}), (x or {}).get("quotes", {})
            q = eq.get(o["symbol"])
            drift = mid(q) / mid(o["quote"]) - 1 if q else None
            stale = q is not None and (
                o["direction"] * drift >= abs(o["expected_uncalibrated"])
                or (q["ask"] - q["bid"]) / mid(q) > 0.005
            )
            selection = modeled(o, eq, xq, o["symbol"], o["direction"])
            basket_rows = [modeled(o, eq, xq, s) for s in o["basket"]]
            basket = (
                statistics.fmean(r["net_modeled_return"] for r in basket_rows)
                if basket_rows and all(basket_rows)
                else None
            )
            spy = modeled(o, eq, xq, "SPY")
            outcomes.append(
                {
                    "id": o["id"],
                    "symbol": o["symbol"],
                    "session": o["session"],
                    "direction": o["direction"],
                    "period": o["period"],
                    "delay_minutes": delay // 60,
                    "nominal_holding_minutes": 60 - delay // 60,
                    "exit_target_at": o["exit_at"],
                    "horizon_eligible": o["eligible"],
                    "entry_observed": q is not None,
                    "stale_before_entry": stale,
                    "entry_eligible": bool(q and not stale and o["eligible"]),
                    "waiting_price_drift": drift,
                    "selection": selection,
                    "trade_or_cash_net": (
                        selection["net_modeled_return"] if q and not stale else 0.0
                    )
                    if selection
                    else None,
                    "basket_net": basket,
                    "spy_net": spy["net_modeled_return"] if spy else None,
                    "cash_net": 0.0,
                    "status": "INELIGIBLE_HORIZON"
                    if not o["eligible"]
                    else "COMPLETE"
                    if selection and basket is not None and spy
                    else "MISSING"
                    if (e and e["status"] == "MISSING")
                    or (x and x["status"] == "MISSING")
                    or now >= o["exit_at"] + 60
                    else "PENDING",
                    "pricing": "observed_bid_ask_model"
                    if o["direction"] == 1
                    else "theoretical_short_bid_ask_diagnostic",
                }
            )
    # Fixed full four-delay complete cases, including losses and stale signals.
    matched = {
        o["id"]
        for o in opportunities
        if all(r["status"] == "COMPLETE" for r in outcomes if r["id"] == o["id"])
    }
    stats = {}
    for direction, label in ((1, "long"), (-1, "theoretical_bearish")):
        stats[label] = {}
        for delay in (1, 3, 5, 10):
            rows = [
                r
                for r in outcomes
                if r["id"] in matched
                and r["delay_minutes"] == delay
                and r["direction"] == direction
            ]
            stats[label][str(delay)] = {
                "trade_or_cash_net": cluster(
                    [(r["session"], r["trade_or_cash_net"]) for r in rows]
                ),
                "net": cluster(
                    [(r["session"], r["selection"]["net_modeled_return"]) for r in rows]
                ),
                "excess_basket": cluster(
                    [
                        (r["session"], r["selection"]["net_modeled_return"] - r["basket_net"])
                        for r in rows
                    ]
                ),
                "excess_spy": cluster(
                    [
                        (r["session"], r["selection"]["net_modeled_return"] - r["spy_net"])
                        for r in rows
                    ]
                ),
                "difference_vs_1min": cluster(
                    [
                        (
                            r["session"],
                            r["selection"]["net_modeled_return"]
                            - next(
                                v["selection"]["net_modeled_return"]
                                for v in outcomes
                                if v["id"] == r["id"] and v["delay_minutes"] == 1
                            ),
                        )
                        for r in rows
                    ]
                ),
            }
    activation = pipeline.records("config")[0]["activated_at"]
    scheduled, day = [], day_of(activation)
    while day <= day_of(now):
        scheduled += [s for s in slots(day) if activation <= s and s + 60 <= now]
        day += timedelta(days=1)
    if session:
        scheduled = [s for s in scheduled if day_of(s).isoformat() == session]
    observed_slots = {s["slot"] for s in scans}
    intervals = [
        b["detected_at"] - a["detected_at"]
        for a, b in zip(scans, scans[1:], strict=False)
        if day_of(a["detected_at"]) == day_of(b["detected_at"])
    ]
    complete = [r for r in outcomes if r["selection"]]
    return {
        "protocol": PROTOCOL,
        "session": session,
        "version": VERSION,
        "generated_at": now,
        "activated_at": activation,
        "mode": "SHADOW",
        "broker_writes": 0,
        "llm_calls": 0,
        "scan_count": len(scans),
        "scan_coverage": [
            {
                "slot": s["slot"],
                "eligible": s["coverage"],
                "observed_symbols": s.get("observed_symbols"),
            }
            for s in scans
        ],
        "actual_intervals_seconds": intervals,
        "missed_slots": [iso(s) for s in scheduled if s not in observed_slots],
        "failures": pipeline.records("scan_failure"),
        "capture_measurements": pipeline.records("capture"),
        "unique_opportunities": len(opportunities),
        "matched_opportunities": len(matched),
        "abstentions": sum(r["transition"] == "ABSTAIN" for s in scans for r in s["rows"]),
        "transitions": {
            v: sum(r["transition"] == v for s in scans for r in s["rows"])
            for v in ("NEW", "CONTINUING", "EXPIRED", "UNKNOWN")
        },
        "delay_coverage": {
            str(d): {
                "observed": sum(r["entry_observed"] for r in outcomes if r["delay_minutes"] == d),
                "eligible": sum(r["entry_eligible"] for r in outcomes if r["delay_minutes"] == d),
                "stale": sum(r["stale_before_entry"] for r in outcomes if r["delay_minutes"] == d),
            }
            for d in (1, 3, 5, 10)
        },
        "by_period": {
            p: {
                "opportunities": sum(o["period"] == p for o in opportunities),
                "long_net": cluster(
                    [
                        (r["session"], r["selection"]["net_modeled_return"])
                        for r in outcomes
                        if r["id"] in matched
                        and r["period"] == p
                        and r["direction"] == 1
                        and r["delay_minutes"] == 1
                    ]
                ),
            }
            for p in ("morning", "midday", "afternoon")
        },
        "comparison": stats,
        "outcomes": outcomes,
        "best": max(complete, key=lambda r: r["selection"]["net_modeled_return"], default=None),
        "worst": min(complete, key=lambda r: r["selection"]["net_modeled_return"], default=None),
    }


def render(r):
    def number(v):
        return "UNAVAILABLE" if v is None else f"{v:.4%}"

    lines = [
        f"# {PROTOCOL} — SHADOW",
        f"Version: {r['version']}",
        f"Generated: {iso(r['generated_at'])}; activated: {iso(r['activated_at'])}",
        f"Scans: {r['scan_count']}; unique opportunities: {r['unique_opportunities']}; "
        f"matched across all delays: {r['matched_opportunities']}; abstentions: {r['abstentions']}",
        f"Missed scheduled scans: {len(r['missed_slots'])}; transitions: {r['transitions']}",
        f"Target interval: 300s; observed interval range: {min(r['actual_intervals_seconds'], default=None)}–{max(r['actual_intervals_seconds'], default=None)}s.",
        f"Scan coverage (observed / eligible liquid): {[(s.get('observed_symbols'), s['eligible']) for s in r['scan_coverage']]}",
        f"Report session: {r.get('session') or 'cumulative'}",
        "Entry windows: [signal+delay, +60s). Common exit: signal+60min, same 60s tolerance.",
        "Actual holding periods/times, drift, costs and completeness are retained per outcome.",
        "Costs: observed spread sides plus 5bp slippage per side, assumed zero fees.",
        "All four delays use matched opportunities, including stale and losing signals; uncertainty uses session means.",
        "Stale eligibility is reported separately; it never deletes a frozen opportunity.",
        "Returns are independent hypothetical observations, never a combined account balance.",
        "Bearish outcomes are theoretical short diagnostics excluding borrow/margin, not options or executable short P&L.",
        "CASH=0. No profitability or live-readiness claim. No LLM or broker writes.",
        "",
        "|Direction|Delay min|Matched n|Sessions|Frozen net vs CASH|Trade/CASH net|Session SE|Excess basket|Excess SPY|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for direction, delays in r["comparison"].items():
        for delay, s in delays.items():
            lines.append(
                f"|{direction}|{delay}|{s['net']['opportunities']}|{s['net']['sessions']}|"
                f"{number(s['net']['session_mean'])}|{number(s['trade_or_cash_net']['session_mean'])}|{number(s['net']['session_standard_error'])}|"
                f"{number(s['excess_basket']['session_mean'])}|{number(s['excess_spy']['session_mean'])}|"
            )
    lines += [
        "",
        f"Delay observed/eligible/stale: {r['delay_coverage']}",
        f"Morning/midday/afternoon: {r['by_period']}",
    ]
    for label in ("best", "worst"):
        v = r[label]
        lines.append(
            f"{label}: "
            + (
                f"{v['symbol']} delay={v['delay_minutes']}m "
                f"{v['pricing']} net={number(v['selection']['net_modeled_return'])}"
                if v
                else "UNAVAILABLE"
            )
        )
    lines += [
        "",
        "Raw sources: immutable continuous_events in experience.sqlite3 and content-addressed captures/*.json.",
        "Full reproducible per-opportunity results: daily-status.json. Missing observations are never interpolated.",
        f"Failures: {r['failures']}",
        f"Missed slots: {r['missed_slots']}",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--oauth-helper", type=Path)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args(argv)
    p = Pipeline(args.state_dir, oauth_helper=args.oauth_helper)
    try:
        if args.report_only:
            with p.store.lock():
                r = p.report()
        else:
            r = p.tick()
        print(
            canonical(
                {
                    k: r[k]
                    for k in (
                        "protocol",
                        "version",
                        "scan_count",
                        "unique_opportunities",
                        "generated_at",
                    )
                }
            )
        )
    finally:
        p.store.close()


if __name__ == "__main__":
    main()
