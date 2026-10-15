"""Restart-safe SEC -> independent Fast / Delayed / AI SHADOW research workers."""

import argparse
import json
import math
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from ..calendar import session_bounds
from ..research.domain import canonical
from ..research.opportunities import capture, grouped
from .continuous import NY, mid, quote_ok
from .event_assessor import assess_event
from .event_store import CONFIG, PROTOCOL, VERSION, Store
from .sec_events import Client, poll

ARMS = ("fast", "delayed", "quant_codex")


def session(now):
    return datetime.fromtimestamp(now, NY).date()


def entry_slot(available):
    day = session(available)
    for _ in range(370):
        bounds = session_bounds(day)
        if bounds:
            earliest = max(available, bounds[0] + 900)
            target = (math.floor((earliest - 45) / 60) + 1) * 60 + 45
            if target + 60 <= bounds[1]:
                return target
        day += timedelta(days=1)
        available = datetime(day.year, day.month, day.day, tzinfo=NY).timestamp()
    raise ValueError("no eligible entry session")


def horizon_targets(entry_time):
    day = session(entry_time)
    targets = {}
    for count in range(1, 6):
        day += timedelta(days=1)
        while not session_bounds(day):
            day += timedelta(days=1)
        if count in (1, 3, 5):
            targets[str(count)] = session_bounds(day)[1] - 60
    return targets


def prior_session(day):
    day -= timedelta(days=1)
    while not session_bounds(day):
        day -= timedelta(days=1)
    return day


def worker_permitted(kind, now):
    """Observe oneshot activating as busy. Market work also yields to v5."""
    names = ["tradeagent-daily-research.service", "tradeagent-daily-news.service"]
    if kind == "market":
        names.append("tradeagent-continuous-v5.service")
        local = datetime.fromtimestamp(now, NY)
        # Reserve v5 discovery minutes and its usual endpoint seconds.
        if local.minute % 5 == 0 or local.second < 40:
            return False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", *names, "--property=ActiveState", "--value"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        states = result.stdout.split()
        return len(states) == len(names) and all(s in {"inactive", "failed"} for s in states)
    except (OSError, subprocess.SubprocessError):
        return False


def eligible_slot(available):
    target = entry_slot(available)
    # v5 discovery has a fully reserved minute, so entries use the next slot.
    if datetime.fromtimestamp(target, NY).minute % 5 == 0:
        target = entry_slot(target + 0.001)
    return target


def evidence(data, event, now):
    bars = grouped(data, now)
    symbol = event["symbol"]
    own, spy = bars.get(symbol, [])[-30:], bars.get("SPY", [])[-30:]
    q = data.get("quotes", {}).get(symbol)
    sq = data.get("quotes", {}).get("SPY")
    result = {
        "available": False,
        "eligible": False,
        "symbol": symbol,
        "quote": q,
        "observed_at": now,
        "reaction_reference": "first_actual_discovery",
        "expected_return_kind": "UNCALIBRATED",
        "reason": "MISSING_MARKET_EVIDENCE",
    }
    if not quote_ok(q, now) or not quote_ok(sq, now) or len(own) < 5 or len(spy) < 5:
        return result
    if now - own[-1].end > 120 or now - spy[-1].end > 120:
        return result
    if any(a.end != b.start for path in (own, spy) for a, b in zip(path, path[1:], strict=False)):
        return result
    info = event["first_discovered_at"]
    anchors = []
    for ticker, path in ((symbol, own), ("SPY", spy)):
        before = [b for b in path if b.end <= info]
        if before:
            anchors.append((before[-1].close, before[-1].end))
        else:
            ref = data.get("references", {}).get(ticker, {})
            if not 0 < ref.get("previous_close", 0) or ref.get("previous_close_time", now) > info:
                return result
            anchors.append((ref["previous_close"], ref["previous_close_time"]))
    if anchors[0][1] != anchors[1][1]:
        return result
    move = mid(q) / anchors[0][0] - 1
    market = mid(sq) / anchors[1][0] - 1
    relative = move - market
    rv = math.sqrt(sum(math.log(b.close / b.open) ** 2 for b in own))
    volume = sum(b.close * b.volume for b in own) / len(own)
    spread = (q["ask"] - q["bid"]) / mid(q)
    expected = max(-0.03, min(0.03, relative * 0.5))
    cost = spread + 2 * CONFIG["costs"]["slippage_per_side"] + CONFIG["costs"]["fees_fraction"]
    liquid = volume >= 1_000_000 and spread <= 0.005
    long = liquid and move > 0 and relative > 0 and expected > cost + 0.001
    result.update(
        available=True,
        eligible=liquid,
        reason="QUANT_LONG" if long else "QUANT_CASH" if liquid else "LIQUIDITY_REJECTED",
        choice="LONG" if long else "CASH",
        reaction=move,
        spy_reaction=market,
        relative_reaction=relative,
        realized_volatility=rv,
        mean_dollar_volume=volume,
        spread=spread,
        expected_uncalibrated=expected,
        estimated_roundtrip_cost=cost,
        anchor_at=anchors[0][1],
        quote=q,
        spy_quote=sq,
        completed_bar_end=own[-1].end,
        session=session(now).isoformat(),
        period="morning" if datetime.fromtimestamp(now, NY).hour < 12 else "afternoon",
    )
    return result


class Pipeline:
    def __init__(
        self,
        directory,
        *,
        clock=time.time,
        collector=capture,
        assessor=assess_event,
        guard=worker_permitted,
        oauth_helper=None,
        codex="codex",
        v5_directory=None,
    ):
        self.store = Store(directory, clock)
        self.directory = self.store.directory
        self.clock, self.collector, self.assessor, self.guard = clock, collector, assessor, guard
        self.oauth_helper, self.codex = oauth_helper, codex
        self.v5_directory = Path(v5_directory) if v5_directory else None

    def close(self):
        self.store.close()

    def ready(self):
        content = {r["event_id"]: r for r in self.store.rows("content")}
        return [
            (e, content[e["id"]])
            for e in self.store.rows("event")
            if e["prospective"] and e["material"] and not e["rejection"] and e["id"] in content
        ]

    def plan(self):
        for event, content in self.ready():
            self.store.add(
                "plan",
                event["id"],
                {
                    "event_id": event["id"],
                    "available_at": content["available_at"],
                    "fast_target": eligible_slot(content["available_at"]),
                    "cohort": int(event["first_discovered_at"] // 900),
                    "version": VERSION,
                },
            )

    def assess_tick(self):
        with self.store.lock("assessor"):
            self.plan()
            now = self.clock()
            if not self.guard("assessor", now):
                return {"status": "DEFERRED_LEGACY_PRIORITY"}
            # A claim interrupted by process death remains terminal, never auto-replayed.
            for claim in self.store.rows("analysis_started"):
                if not self.store.get("analysis", claim["event_id"]):
                    self.store.add(
                        "analysis",
                        claim["event_id"],
                        {
                            "event_id": claim["event_id"],
                            "status": "UNAVAILABLE",
                            "reason": "INTERRUPTED_NO_RETRY",
                            "completed_at": now,
                            "started_at": claim["started_at"],
                        },
                    )
            used = [
                r
                for r in self.store.rows("analysis_started")
                if session(r["started_at"]) == session(now)
            ]
            events = sorted(
                self.ready(),
                key=lambda p: (p[0]["priority"], p[0]["first_discovered_at"], p[0]["id"]),
            )
            for event, content in events:
                key = event["id"]
                if self.store.get("analysis", key):
                    continue
                if len(used) >= CONFIG["daily_codex_budget"]:
                    self.store.add(
                        "analysis",
                        key,
                        {
                            "event_id": key,
                            "status": "UNAVAILABLE",
                            "reason": "DAILY_BUDGET",
                            "completed_at": now,
                        },
                    )
                    continue
                previous = [
                    r for r in self.store.rows("analysis_started") if r["symbol"] == event["symbol"]
                ]
                if (
                    previous
                    and now - previous[-1]["started_at"] < CONFIG["symbol_cooldown_seconds"]
                ):
                    self.store.add(
                        "analysis",
                        key,
                        {
                            "event_id": key,
                            "status": "UNAVAILABLE",
                            "reason": "SYMBOL_COOLDOWN",
                            "completed_at": now,
                        },
                    )
                    continue
                bounds = session_bounds(session(now))
                fast = self.store.get("entry", key + ":fast")
                if bounds and bounds[0] <= now < bounds[1] and not fast:
                    continue  # independent Fast capture gets the first regular-session opportunity
                claim = {"event_id": key, "symbol": event["symbol"], "started_at": self.clock()}
                if not self.store.add("analysis_started", key, claim):
                    continue
                try:
                    assessment, provenance = self.assessor(
                        event,
                        content,
                        {"fast_market": fast, "observed_at": now, "consensus": "UNAVAILABLE"},
                        executable=self.codex,
                        timeout=CONFIG["codex_timeout_seconds"],
                    )
                    result = {
                        **claim,
                        "status": "COMPLETED",
                        "assessment": assessment,
                        "provenance": provenance,
                        "completed_at": self.clock(),
                    }
                except Exception as exc:
                    result = {
                        **claim,
                        "status": "UNAVAILABLE",
                        "reason": str(exc),
                        "completed_at": self.clock(),
                    }
                self.store.add("analysis", key, result)
                return {"status": result["status"], "event_id": key}
            return {"status": "NO_NEW_ELIGIBLE_EVENT"}

    def collect(self, symbols, quotes_only):
        start = self.clock()
        local = datetime.fromtimestamp(start, NY)
        next_scan = (int(start // 300) + 1) * 300
        deadline = min(start + 35, next_scan - 3) if 9 <= local.hour < 15 else start + 35
        if self.v5_directory:
            path = self.v5_directory / "experience.sqlite3"
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
                pending = [
                    json.loads(r[0])
                    for r in db.execute(
                        "SELECT payload FROM continuous_events WHERE kind='opportunity'"
                    )
                ]
                complete = {
                    r[0]
                    for r in db.execute("SELECT key FROM continuous_events WHERE kind='endpoint'")
                }
            ticks = []
            for opportunity in pending:
                if not opportunity["eligible"]:
                    continue
                for delay in (60, 180, 300, 600, 3600):
                    target = opportunity["detected_at"] + delay
                    if opportunity["id"] + ":" + str(delay) not in complete and start < target + 60:
                        ticks.append(math.ceil(max(start, target) / 15) * 15)
            if ticks:
                deadline = min(deadline, min(ticks) - 3)
        if deadline - start < 3:
            raise ValueError("deferred: imminent v5 discovery")
        data = self.collector(
            sorted(set(symbols) | {"SPY"}),
            oauth_helper=self.oauth_helper,
            lookback_minutes=30,
            quotes_only=quotes_only,
            reference_details=True,
            deadline=deadline,
        )
        if (
            data["evidence_kind"] != "prospective"
            or not start <= data["requested_at"] <= data["observed_at"] <= self.clock()
        ):
            raise ValueError("market capture is not genuine current forward evidence")
        digest = self.store.blob(canonical(data).encode(), ".json")
        self.store.add(
            "capture",
            digest,
            {
                "capture_id": digest,
                "requested_at": start,
                "observed_at": data["observed_at"],
                "seconds": self.clock() - start,
                "symbols": data["symbols"],
                "quotes_only": quotes_only,
            },
        )
        return data, digest

    def record_references(self, data, capture_id):
        day = session(data["observed_at"]).isoformat()
        for symbol, ref in data.get("references", {}).items():
            self.store.add(
                "reference",
                day + ":" + symbol,
                {
                    "session": day,
                    "symbol": symbol,
                    "reference": ref,
                    "observed_at": data["observed_at"],
                    "capture_id": capture_id,
                },
            )

    def pending_entries(self):
        now = self.clock()
        pending = []
        for event, content in self.ready():
            plan = self.store.get("plan", event["id"])
            if not plan:
                continue
            for kind in ("fast", "delayed"):
                key = event["id"] + ":" + kind
                if self.store.get("entry", key):
                    continue
                analysis = self.store.get("analysis", event["id"])
                if kind == "delayed" and not analysis:
                    continue
                target = (
                    plan["fast_target"]
                    if kind == "fast"
                    else eligible_slot(analysis["completed_at"])
                )
                if target <= now:
                    pending.append((event, content, kind, key, target))
        return pending

    def entries(self):
        now = self.clock()
        due = self.pending_entries()
        live = [r for r in due if now < r[4] + 60]
        data, capture_id, error = None, None, None
        symbols = set()
        for event, _, kind, _, _ in live:
            cohort = self.store.get("plan", event["id"])["cohort"]
            peers = [
                e["symbol"]
                for e, c in self.ready()
                if int(e["first_discovered_at"] // 900) == cohort and c["available_at"] <= now
            ]
            symbols.update(peers)
            fast = self.store.get("entry", event["id"] + ":fast")
            if kind == "delayed" and fast:
                symbols.update(fast.get("basket", []))
        if live:
            try:
                data, capture_id = self.collect(symbols, False)
                self.record_references(data, capture_id)
            except Exception as exc:
                error = str(exc)
        for event, content, kind, key, target in due:
            frame = None
            if data and (event, content, kind, key, target) in live:
                frame = evidence(data, event, self.clock())
                if not quote_ok(frame.get("quote"), self.clock(), target):
                    frame["available"] = frame["eligible"] = False
                    frame["reason"] = "ENTRY_QUOTE_OUTSIDE_FROZEN_WINDOW"
            if not frame:
                frame = {
                    "available": False,
                    "eligible": False,
                    "reason": error or "MISSED_ENTRY_WINDOW",
                }
            basket = []
            basket_quotes = {}
            fast = self.store.get("entry", event["id"] + ":fast")
            if data:
                candidates = (
                    fast.get("basket", []) if kind == "delayed" and fast else sorted(symbols)
                )
                for symbol in candidates:
                    peer = {**event, "symbol": symbol}
                    f = evidence(data, peer, self.clock())
                    if (kind == "delayed" and fast) or f["eligible"]:
                        basket.append(symbol)
                        q = data["quotes"].get(symbol)
                        if quote_ok(q, self.clock(), target):
                            basket_quotes[symbol] = q
            analysis = self.store.get("analysis", event["id"])
            quant_choice = frame.get("choice") if frame["available"] else None
            choices = {kind: quant_choice}
            if kind == "delayed":
                choices["quant_codex"] = (
                    (
                        "LONG"
                        if quant_choice == "LONG"
                        and analysis.get("assessment", {}).get("recommendation") == "LONG"
                        else "CASH"
                    )
                    if quant_choice and analysis["status"] == "COMPLETED"
                    else None
                )
            now = self.clock()
            self.store.add(
                "entry",
                key,
                {
                    "event_id": event["id"],
                    "kind": kind,
                    "symbol": event["symbol"],
                    "target_at": target,
                    "frozen_at": now,
                    "status": "FROZEN" if frame["available"] else "UNAVAILABLE",
                    "reason": frame["reason"],
                    "frame": frame,
                    "choices": choices,
                    "quote": frame.get("quote") if frame["available"] else None,
                    "spy_quote": frame.get("spy_quote")
                    if frame["available"] and quote_ok(frame.get("spy_quote"), self.clock(), target)
                    else None,
                    "basket": basket,
                    "basket_quotes": basket_quotes,
                    "capture_id": capture_id,
                    "session": session(now).isoformat(),
                    "horizons": horizon_targets(now) if frame["available"] else {},
                    "costs": CONFIG["costs"],
                    "evidence_kind": "prospective",
                },
            )

    def exits_and_references(self):
        now = self.clock()
        entries = [r for r in self.store.rows("entry") if r["status"] == "FROZEN"]
        due = []
        for entry in entries:
            for horizon, target in entry["horizons"].items():
                key = f"{entry['event_id']}:{entry['kind']}:{horizon}"
                if target <= now and not self.store.get("exit", key):
                    due.append((entry, horizon, key, target))
        live = [r for r in due if now < r[3] + 60]
        bounds = session_bounds(session(now))
        monitor = set()
        if bounds and bounds[0] + 900 <= now < bounds[1]:
            for e in entries:
                if now <= max(e["horizons"].values()) + 60:
                    monitor.update([e["symbol"], *e["basket"], "SPY"])
            monitor = {
                s
                for s in monitor
                if not self.store.get("reference", session(now).isoformat() + ":" + s)
            }
        symbols = {s for e, _, _, _ in live for s in [e["symbol"], *e["basket"], "SPY"]} | monitor
        data, capture_id, error = None, None, None
        if symbols:
            try:
                data, capture_id = self.collect(symbols, True)
                self.record_references(data, capture_id)
            except Exception as exc:
                error = str(exc)
        for entry, horizon, key, target in due:
            quotes = (
                {s: q for s, q in data["quotes"].items() if quote_ok(q, self.clock(), target)}
                if data and (entry, horizon, key, target) in live
                else {}
            )
            self.store.add(
                "exit",
                key,
                {
                    "event_id": entry["event_id"],
                    "kind": entry["kind"],
                    "horizon": horizon,
                    "target_at": target,
                    "observed_at": self.clock(),
                    "quotes": quotes,
                    "capture_id": capture_id,
                    "status": "OBSERVED" if entry["symbol"] in quotes else "UNAVAILABLE",
                    "reason": error
                    or (None if entry["symbol"] in quotes else "MISSED_OR_INVALID_EXIT"),
                },
            )

    def market_tick(self):
        with self.store.lock("market"):
            self.plan()
            if not self.guard("market", self.clock()):
                return {"status": "DEFERRED_CAPTURE_PRIORITY"}
            self.exits_and_references()
            self.entries()
            return {
                "status": "COMPLETED",
                "entries": len(self.store.rows("entry")),
                "exits": len(self.store.rows("exit")),
            }


def adjustment_status(store, entry, symbol, exit_time):
    import re

    event_map = {e["id"]: e for e in store.rows("event") if e["symbol"] == symbol}
    for c in store.rows("content"):
        event = event_map.get(c["event_id"])
        if (
            event
            and entry["frozen_at"] <= event["first_discovered_at"] <= exit_time
            and c["available_at"] <= exit_time
        ):
            if re.search(
                r"stock split|reverse split|stock dividend|spin-off|spinoff|exchange ratio|symbol change",
                c["extract"],
                re.I,
            ):
                return "CORPORATE_ACTION_DISCLOSURE"
    day = session(
        datetime.fromisoformat(entry["session"]).replace(tzinfo=NY).timestamp()
    ) + timedelta(days=1)
    expected = []
    while day <= session(exit_time):
        if session_bounds(day):
            expected.append(day)
        day += timedelta(days=1)
    if not expected:
        return "UNVERIFIED"
    for day in expected:
        r = store.get("reference", day.isoformat() + ":" + symbol)
        ref = r.get("reference", {}) if r else {}
        if (
            not r
            or r["observed_at"] > exit_time
            or ref.get("previous_close_date") != prior_session(day).isoformat()
        ):
            return "UNVERIFIED"
        raw, adjusted = ref.get("previous_close"), ref.get("adjusted_previous_close")
        if not raw or not adjusted:
            return "UNVERIFIED"
        if abs(adjusted / raw - 1) > 1e-6:
            return "ADJUSTMENT_DETECTED"
    return "NO_PROVIDER_ADJUSTMENT_OBSERVED"


def price_return(entry_q, exit_q, costs):
    if not entry_q or not exit_q:
        return None
    gross = mid(exit_q) / mid(entry_q) - 1
    side = exit_q["bid"] / entry_q["ask"] - 1
    slip = 2 * costs["slippage_per_side"] + costs["fees_fraction"]
    return {
        "entry_price": entry_q["ask"],
        "entry_quote_at": entry_q["asof"],
        "entry_received_at": entry_q["observed_at"],
        "exit_price": exit_q["bid"],
        "exit_quote_at": exit_q["asof"],
        "exit_received_at": exit_q["observed_at"],
        "gross_mid_return": gross,
        "observed_spread_cost": gross - side,
        "slippage_fees": slip,
        "net": side - slip,
        "holding_seconds": exit_q["asof"] - entry_q["asof"],
    }


def outcomes(store):
    rows = []
    for entry in store.rows("entry"):
        if entry["status"] != "FROZEN":
            continue
        for horizon, target in entry["horizons"].items():
            x = store.get("exit", f"{entry['event_id']}:{entry['kind']}:{horizon}")
            quotes = x.get("quotes", {}) if x else {}
            own = price_return(entry["quote"], quotes.get(entry["symbol"]), entry["costs"])
            spy = price_return(entry["spy_quote"], quotes.get("SPY"), entry["costs"])
            action = (
                adjustment_status(store, entry, entry["symbol"], x["observed_at"])
                if x
                else "PENDING"
            )
            basket_rows = []
            for symbol in entry["basket"]:
                v = price_return(
                    entry["basket_quotes"].get(symbol), quotes.get(symbol), entry["costs"]
                )
                if (
                    not v
                    or not x
                    or adjustment_status(store, entry, symbol, x["observed_at"])
                    != "NO_PROVIDER_ADJUSTMENT_OBSERVED"
                ):
                    basket_rows = []
                    break
                basket_rows.append(v["net"])
            for arm, choice in entry["choices"].items():
                available = choice is not None and own is not None
                net = (own["net"] if choice == "LONG" else 0.0) if available else None
                status = (
                    "COMPLETE"
                    if available and action == "NO_PROVIDER_ADJUSTMENT_OBSERVED"
                    else "FLAGGED_CORPORATE_ACTION"
                    if available
                    else "UNAVAILABLE"
                    if x
                    else "PENDING"
                )
                rows.append(
                    {
                        "event_id": entry["event_id"],
                        "symbol": entry["symbol"],
                        "arm": arm,
                        "entry_session": entry["session"],
                        "entry_target_at": entry["target_at"],
                        "entry_capture_id": entry["capture_id"],
                        "horizon": int(horizon),
                        "exit_target_at": target,
                        "choice": choice,
                        "market_eligible": entry["frame"]["eligible"],
                        "status": "UNAVAILABLE" if choice is None else status,
                        "price_details": own,
                        "net": net,
                        "corporate_action_status": action,
                        "spy_net": spy["net"]
                        if spy
                        and x
                        and adjustment_status(store, entry, "SPY", x["observed_at"])
                        == "NO_PROVIDER_ADJUSTMENT_OBSERVED"
                        else None,
                        "basket_net": sum(basket_rows) / len(basket_rows) if basket_rows else None,
                        "cash_net": 0.0,
                    }
                )
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("worker", choices=("poll", "assess", "market", "report"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--sec-user-agent-file", type=Path)
    parser.add_argument("--oauth-helper", type=Path)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--v5-state-dir", type=Path)
    parser.add_argument("--verify-historical", action="store_true")
    args = parser.parse_args(argv)
    p = Pipeline(
        args.state_dir,
        oauth_helper=args.oauth_helper,
        codex=args.codex,
        v5_directory=args.v5_state_dir,
    )
    try:
        if args.worker == "poll":
            if not args.sec_user_agent_file:
                raise ValueError("SEC User-Agent file required")
            with p.store.lock("poll"):
                result = poll(
                    p.store, Client(p.store, args.sec_user_agent_file.read_text().strip())
                )
                p.plan()
                if args.verify_historical and result["status"] == "COMPLETE":
                    from .sec_events import content

                    old = p.store.rows("historical_connectivity")
                    if not old:
                        historical = sorted(
                            [
                                e
                                for e in p.store.rows("event")
                                if not e["prospective"] and e["category"] == "earnings"
                            ],
                            key=lambda e: e["accepted_at"] or 0,
                            reverse=True,
                        )
                        if historical:
                            event = historical[0]
                            retrieved = content(
                                Client(p.store, args.sec_user_agent_file.read_text().strip()), event
                            )
                            p.store.add(
                                "historical_connectivity",
                                event["id"],
                                {**retrieved, "prospective": False},
                            )
                    original = bool(
                        p.store.rows("historical_connectivity") or p.store.rows("content")
                    )
                    result = {
                        **result,
                        "protocol": PROTOCOL,
                        "version": VERSION,
                        "poll_status": result["status"],
                        "issuer_coverage": len(result["covered"]),
                        "original_document_read": original,
                    }
        elif args.worker == "assess":
            result = p.assess_tick()
        elif args.worker == "market":
            result = p.market_tick()
        else:
            from .event_reporting import report

            with p.store.lock("report"):
                result = report(p.store, args.v5_state_dir)
        print(canonical(result))
    finally:
        p.close()


if __name__ == "__main__":
    main()
