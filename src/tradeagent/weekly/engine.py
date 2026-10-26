"""Forward-only weekly paper state machine. All transactions are virtual journal events."""

import random
import subprocess
import time
from datetime import date, timedelta

from . import ledger, models
from .market import momentum
from .protocol import (
    ARMS,
    BOOKS,
    CONFIG,
    PROTOCOL,
    UNIVERSE,
    day_at,
    dec,
    digest,
    first_session,
    instant,
    next_session,
    quantity,
    session_bounds,
    valid_quote,
    week_key,
)


def guard():
    units = [
        "tradeagent-daily-research.service",
        "tradeagent-daily-news.service",
        "tradeagent-claude-compare.service",
    ]
    result = subprocess.run(
        ["systemctl", "--user", "show", *units, "--property=ActiveState", "--value"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    states = result.stdout.split()
    if len(states) != len(units) or any(state not in {"inactive", "failed"} for state in states):
        raise ValueError("existing shortline service active; weekly task yields priority")


class Engine:
    def __init__(self, store, market, *, clock=time.time, assessor=models.concurrent):
        self.store, self.market, self.clock, self.assessor = store, market, clock, assessor

    def eligible(self, day):
        return str(day) >= self.store.activation["start_date"] and bool(session_bounds(day))

    def finalize_missed(self):
        day = day_at(self.clock())
        candidate = date.fromisoformat(self.store.activation["start_date"])
        from .protocol import next_week

        while candidate <= day:
            week = str(candidate)
            key = "decision:" + week
            if self.clock() > instant(candidate, CONFIG["deadline"]) and not self.store.get(key):
                value = {
                    "week": week,
                    "cutoff": instant(candidate, CONFIG["cutoff"]),
                    "frozen_at": self.clock(),
                    "packet_hash": None,
                    "arms": {
                        arm: {
                            "status": "MISSING",
                            "reason": "missed or interrupted weekly decision; no backfill",
                        }
                        for arm in ARMS
                    },
                    "evidence_kind": self.store.evidence,
                }
                value["decision_hash"] = digest(value)
                self.store.add(key, "decision", value)
            candidate = next_week(candidate)

    def history(self):
        day = day_at(self.clock())
        if not self.eligible(day):
            return
        history = self.market.histories([*UNIVERSE, "SPY"], years=2)
        self.store.artifact(history)
        self.actions(history, day)

    def actions(self, histories, day):
        health = {
            "date": str(day),
            "missing": [s for s, h in histories.items() if h.get("missing")],
            "source_hash": digest({s: h.get("raw_hash") for s, h in histories.items()}),
        }
        health_key = "corporate-health:" + str(day) + ":" + digest(health)
        if not self.store.get(health_key):
            self.store.add(health_key, "corporate_health", health)
        for history in histories.values():
            for action in sorted(
                history.get("actions", []), key=lambda a: (a["date"], a["type"] != "split")
            ):
                if self.store.activation["start_date"] <= action["date"] <= str(day):
                    stable = {
                        k: v for k, v in action.items() if k not in {"observed_at", "source_url"}
                    }
                    for book in BOOKS:
                        ledger.corporate_action(self.store, book, stable)
                    key = "action-source:" + digest(stable)
                    if not self.store.get(key):
                        self.store.add(key, "corporate_source", {"action": action})

    def packet(self, *, prepare=False):
        now, day = self.clock(), day_at(self.clock())
        week = week_key(day)
        key = "packet:" + week
        if not prepare and (
            day != first_session(day) or not self.eligible(day) or self.store.get(key)
        ):
            return self.store.get(key)
        cutoff = now if prepare else instant(day, CONFIG["cutoff"])
        if not prepare and not instant(day, "11:29") <= now < cutoff:
            raise ValueError("packet must be captured before frozen cutoff")
        histories = self.market.histories([*UNIVERSE, "SPY"], years=2)
        self.actions(histories, day)
        captured = self.market.quotes([*UNIVERSE, "SPY"])
        received = self.clock()
        if prepare:
            cutoff = received
        quotes = {
            s: q
            for s, q in captured["quotes"].items()
            if valid_quote(q, received) and q["observed_at"] <= cutoff
        }
        previous = day - timedelta(days=1)
        while not session_bounds(previous):
            previous -= timedelta(days=1)
        features, prices = {}, {}
        for symbol, history in histories.items():
            bars = [b for b in history.get("bars", []) if b["date"] <= str(previous)]
            previous_close = bars[-1]["close"] if bars else None
            q = quotes.get(symbol)
            price = (q["bid"] + q["ask"]) / 2 if q else previous_close
            if price:
                prices[symbol] = price
            features[symbol] = {
                "previous_close": previous_close,
                "momentum_12_1": momentum(history, previous),
                "quote": q,
                "history_missing": history.get("missing"),
                "history_source_hash": history.get("raw_hash"),
            }
        accounts, endpoints = {}, {}
        for book in BOOKS:
            state = ledger.mature(self.store.state(book), day)
            value = ledger.nav(state, prices)
            endpoints[book] = (
                str(value)
                if value is not None and all(s in quotes for s in state["positions"])
                else None
            )
            if book in {"main:codex", "main:claude"}:
                accounts[book.split(":")[1]] = {
                    "nav": endpoints[book],
                    "cash": str(ledger.total_cash(state)),
                    "positions": [
                        {
                            "symbol": s,
                            "shares": str(ledger.shares(state, s)),
                            "weight": float(ledger.shares(state, s) * dec(prices[s]) / value)
                            if value and s in prices
                            else None,
                        }
                        for s in state["positions"]
                    ],
                }
        packet = {
            "protocol": PROTOCOL,
            "week": week,
            "cutoff": cutoff,
            "captured_at": received,
            "price_features": features,
            "accounts": accounts,
            "prices": prices,
            "endpoints": endpoints,
            "history_hash": self.store.artifact(histories),
            "market_hash": self.store.artifact(captured),
            "price_policy": "latest pre-cutoff observed quote; previous completed close if unavailable",
        }
        if received > cutoff and not prepare:
            raise ValueError("packet received after cutoff; no late information admitted")
        if not prepare:
            self.store.add(key, "packet", packet)
        return packet

    def decision(self, *, prepare=False, packet=None):
        now, day = self.clock(), day_at(self.clock())
        if not prepare and (not self.eligible(day) or day != first_session(day)):
            return None
        week = week_key(day)
        if not prepare and self.store.get("decision:" + week):
            return self.store.get("decision:" + week)
        packet = packet or self.store.get("packet:" + week)
        deadline = (
            now + CONFIG["model_timeout_seconds"] if prepare else instant(day, CONFIG["deadline"])
        )
        if not prepare and now < instant(day, CONFIG["cutoff"]):
            raise ValueError("decision cannot start before cutoff")
        answers = {}
        attempt = "assessment-started:" + week
        if (
            packet
            and not prepare
            and (
                packet["cutoff"] != instant(day, CONFIG["cutoff"])
                or packet["captured_at"] > packet["cutoff"]
            )
        ):
            raise ValueError("invalid pre-cutoff packet timing")
        if packet and now < deadline and (prepare or not self.store.get(attempt)):
            if not prepare:
                self.store.add(
                    attempt,
                    "assessment_started",
                    {"week": week, "started_at": now, "packet_hash": digest(packet)},
                )
            answers = self.assessor(
                packet,
                timeout=max(1, min(CONFIG["model_timeout_seconds"], deadline - now - 2)),
                clock=self.clock,
            )
        arms = {}
        for arm in ("codex", "claude"):
            answer = answers.get(
                arm,
                {"status": "MISSING", "reason": "missing pre-cutoff packet or decision deadline"},
            )
            if answer.get("completed_at", deadline) > deadline:
                answer = {
                    **answer,
                    "status": "MISSING",
                    "reason": "model completed after frozen deadline",
                }
            arms[arm] = answer
        seed = digest(PROTOCOL + str(day))
        valid = packet is not None and self.clock() <= deadline
        ranks = sorted(
            (
                (s, f["momentum_12_1"])
                for s, f in (packet or {}).get("price_features", {}).items()
                if s in UNIVERSE and f["momentum_12_1"] is not None
            ),
            key=lambda x: (-x[1], x[0]),
        )
        for arm, picks in (
            ("quant", [x[0] for x in ranks[:3]]),
            ("random", random.Random(int(seed, 16)).sample(list(UNIVERSE), 3)),
            ("spy", ["SPY"] if str(day) == self.store.activation["start_date"] else []),
        ):
            arms[arm] = {
                "status": "VALID" if valid and (arm != "quant" or ranks) else "MISSING",
                "selection": {"picks": [{"symbol": s} for s in picks]},
                "seed": seed if arm == "random" else None,
            }
        value = {
            "week": week,
            "cutoff": packet["cutoff"] if packet else instant(day, CONFIG["cutoff"]),
            "frozen_at": self.clock(),
            "packet_hash": digest(packet),
            "arms": arms,
            "evidence_kind": self.store.evidence,
        }
        if prepare:
            return value
        value["decision_hash"] = digest(value)
        self.store.add("decision:" + week, "decision", value)
        return value

    def plans(self, day):
        week = week_key(day)
        decision = self.store.get("decision:" + week)
        packet = self.store.get("packet:" + week)
        if not decision or not packet:
            return []
        if digest({k: v for k, v in decision.items() if k != "decision_hash"}) != decision[
            "decision_hash"
        ] or decision["packet_hash"] != digest(packet):
            raise ValueError("decision/packet freeze hash mismatch")
        if decision["frozen_at"] > instant(date.fromisoformat(week), CONFIG["deadline"]):
            return []
        plans = []
        for book in BOOKS:
            key = f"plan:{week}:{book}"
            existing = self.store.get(key)
            if existing:
                plans.append(existing)
                continue
            arm = book.split(":")[1]
            answer = decision["arms"][arm]
            if answer["status"] != "VALID":
                continue
            state = ledger.mature(self.store.state(book), day)
            value = packet["endpoints"].get(book)
            if value is None:
                self.missing(
                    "plan:" + week + ":" + book, book, week, "*", "decision NAV unavailable"
                )
                continue
            picks = [x["symbol"] for x in answer["selection"]["picks"]]
            if arm == "spy" and str(day) != self.store.activation["start_date"]:
                continue
            orders = []
            for symbol in sorted(set(picks) | set(state["positions"])):
                reference = packet["prices"].get(symbol)
                if reference is None:
                    self.missing(
                        f"plan:{week}:{book}:{symbol}",
                        book,
                        week,
                        symbol,
                        "decision valuation unavailable",
                    )
                    continue
                target = dec(value) / (1 if arm == "spy" else 3) if symbol in picks else dec(0)
                current = ledger.shares(state, symbol)
                difference = target - current * dec(reference)
                if (
                    symbol in picks
                    and current
                    and abs(difference) <= dec(value) * dec(CONFIG["rebalance_band"])
                ):
                    continue
                if difference < 0:
                    orders.append(
                        {
                            "symbol": symbol,
                            "side": "sell",
                            "qty": str(quantity(-difference / dec(reference))),
                            "target_value": str(target),
                        }
                    )
                elif difference > 0:
                    orders.append(
                        {
                            "symbol": symbol,
                            "side": "buy",
                            "target_value": str(target),
                            "qty": str(
                                quantity(
                                    difference
                                    / (dec(reference) * (1 + dec(CONFIG["slippage_per_side"])))
                                )
                            ),
                        }
                    )
            plan = {
                "book": book,
                "week": week,
                "orders": orders,
                "decision_hash": decision["decision_hash"],
                "nav": value,
            }
            self.store.add(key, "plan", plan)
            plans.append(plan)
        return plans

    def missing(self, key, book, week, symbol, reason):
        key = "missing:" + key
        if not self.store.get(key):
            self.store.add(
                key,
                "missing",
                {
                    "book": book,
                    "week": week,
                    "symbol": symbol,
                    "reason": reason,
                    "date": str(day_at(self.clock())),
                },
            )

    def fill(self):
        now, day = self.clock(), day_at(self.clock())
        if not self.eligible(day):
            return
        week = week_key(day)
        decision_day = date.fromisoformat(week)
        primary = day == decision_day
        sensitivity = day == next_session(decision_day)
        if not (primary or sensitivity):
            return
        plans = (
            self.plans(decision_day)
            if primary
            else [r for r in self.store.rows("plan") if r["week"] == week]
        )
        start, end = instant(day, "12:00"), instant(day, "12:10")
        if now < start:
            return
        delays = list(self.store.rows("buy_delay")) if sensitivity else []
        pending = []
        for plan in plans:
            for order in plan["orders"]:
                key = f"fill:{week}:{plan['book']}:{order['symbol']}:{order['side']}"
                if primary and not self.store.get(key) and not self.store.get("missing:" + key):
                    pending.append((plan, order, key))
        for delay in delays:
            if delay["week"] == week and not self.store.get(delay["fill_key"]):
                pending.append(
                    (
                        delay,
                        {"symbol": delay["symbol"], "side": "buy", "qty": delay["qty"]},
                        delay["fill_key"],
                    )
                )
        if now >= end:
            for plan, order, key in pending:
                self.missing(
                    key, plan["book"], week, order["symbol"], "missed fill window; no backfill"
                )
            return
        if not pending:
            return
        captured = self.market.quotes(list(dict.fromkeys(o["symbol"] for _, o, _ in pending)))
        received = self.clock()
        evidence = self.store.artifact(captured)
        quotes = {
            s: q for s, q in captured["quotes"].items() if valid_quote(q, received, start, end)
        }
        for plan, order, key in sorted(
            pending, key=lambda x: (x[1]["side"] != "sell", x[0]["book"], x[1]["symbol"])
        ):
            book, symbol = plan["book"], order["symbol"]
            q = quotes.get(symbol)
            if not q:
                continue
            stopped = self.store.get(f"stopped:{week}:{book}:{symbol}")
            if stopped and order["side"] == "buy":
                self.missing(key, book, week, symbol, "risk exit; cash until next weekly rebalance")
                continue
            requested = dec(order["qty"])
            if "target_value" in order:
                current_qty = ledger.shares(self.store.state(book), symbol)
                mid = (dec(q["bid"]) + dec(q["ask"])) / 2
                difference = dec(order["target_value"]) - current_qty * mid
                requested = (
                    quantity(
                        max(dec(0), difference)
                        / (dec(q["ask"]) * (1 + dec(CONFIG["slippage_per_side"])))
                    )
                    if order["side"] == "buy"
                    else quantity(max(dec(0), -difference) / mid)
                )
            if order["side"] == "sell":
                ledger.sell(
                    self.store,
                    key,
                    book,
                    symbol,
                    requested,
                    q,
                    day,
                    at=received,
                    reason="weekly",
                )
            else:
                before = ledger.shares(self.store.state(book), symbol)
                filled = ledger.buy(self.store, key, book, symbol, requested, q, day, at=received)
                after = ledger.shares(self.store.state(book), symbol)
                remaining = requested - (after - before)
                if primary and book.startswith("settled:") and remaining > 0:
                    delay_key = "buy-delay:" + key
                    if not self.store.get(delay_key):
                        self.store.add(
                            delay_key,
                            "buy_delay",
                            {
                                "book": book,
                                "week": week,
                                "symbol": symbol,
                                "qty": str(remaining),
                                "fill_key": key + ":settled-next-session",
                                "date": str(day),
                                "earliest": str(next_session(day)),
                                "reason": "settled-only sensitivity",
                                "quote_evidence": evidence,
                            },
                        )
                    if not filled:
                        self.missing(
                            key,
                            book,
                            week,
                            symbol,
                            "settled cash unavailable; next-session sensitivity window",
                        )
                elif not filled:
                    self.missing(key, book, week, symbol, "no affordable cash")

    def risk(self):
        now, day = self.clock(), day_at(self.clock())
        if not self.eligible(day):
            return
        week = week_key(day)
        target = instant(day, "15:45")
        bounds = session_bounds(day)
        if not bounds[0] <= target < bounds[1]:
            self.missing(
                "risk:" + str(day),
                "all-active",
                week,
                "*",
                "early close: no regular-session 15:45 risk window",
            )
            return
        if not target <= now < target + 60:
            if now >= target + 60:
                self.missing(
                    "risk:" + str(day),
                    "all-active",
                    week,
                    "*",
                    "missed risk check; no stale execution",
                )
            return
        holdings = {
            s
            for book in BOOKS
            if not book.endswith(":spy")
            for s in self.store.state(book)["positions"]
        }
        if not holdings:
            return
        health = [r for r in self.store.rows("corporate_health") if r["date"] == str(day)]
        unavailable = set(health[-1]["missing"]) if health else set(holdings)
        for book in BOOKS:
            for symbol in self.store.state(book)["positions"]:
                if symbol in unavailable:
                    self.missing(
                        f"corporate-risk:{day}:{book}:{symbol}",
                        book,
                        week,
                        symbol,
                        "corporate action source pending; no risk fill guessed",
                    )
        holdings -= unavailable
        if not holdings:
            return
        capture = self.market.quotes(sorted(holdings))
        received = self.clock()
        self.store.artifact(capture)
        for book in BOOKS:
            if book.endswith(":spy"):
                continue
            state = self.store.state(book)
            for symbol in state["positions"]:
                if symbol in unavailable:
                    continue
                q = capture["quotes"].get(symbol)
                key = f"risk:{day}:{book}:{symbol}:sell"
                if self.store.get(key):
                    continue
                if not valid_quote(q, received, target, target + 60):
                    self.missing(key, book, week, symbol, "risk quote missing")
                    continue
                deferred = any(
                    r.get("book") == book
                    and r.get("symbol") == symbol
                    and r.get("phase") == "risk"
                    and r["at"] >= min(lot["opened_at"] for lot in state["positions"][symbol])
                    for r in self.store.rows("delay")
                )
                if (
                    dec(q["bid"]) <= ledger.wac(state, symbol) * (1 - dec(CONFIG["risk_loss"]))
                    or deferred
                ):
                    if ledger.sell(
                        self.store,
                        key,
                        book,
                        symbol,
                        ledger.shares(state, symbol),
                        q,
                        day,
                        at=received,
                        reason="risk",
                    ):
                        stop_key = f"stopped:{week}:{book}:{symbol}"
                        if not self.store.get(stop_key):
                            self.store.add(
                                stop_key,
                                "risk_exit",
                                {"book": book, "symbol": symbol, "week": week, "date": str(day)},
                            )

    def valuation(self):
        self.finalize_missed()
        day = day_at(self.clock())
        if self.eligible(day) and self.clock() < instant(day, "17:15"):
            raise ValueError("closing valuation cannot freeze before 17:15 New York")
        if not self.eligible(day):
            return
        histories = self.market.histories([*UNIVERSE, "SPY"], years=2, refresh=True)
        self.actions(histories, day)
        evidence = self.store.artifact(histories)
        prices = {
            s: next(
                (b["close"] for b in reversed(h.get("bars", [])) if b["date"] == str(day)), None
            )
            for s, h in histories.items()
        }
        for book in BOOKS:
            key = f"valuation:{day}:{book}"
            if self.store.get(key):
                continue
            state = ledger.mature(self.store.state(book), day)
            value = ledger.nav(state, prices)
            actions_ok = all(not histories[s].get("missing") for s in state["positions"])
            self.store.add(
                key,
                "valuation",
                {
                    "book": book,
                    "date": str(day),
                    "week": week_key(day),
                    "nav": str(value) if value is not None and actions_ok else None,
                    "cash": str(ledger.total_cash(state)),
                    "settled_cash": str(dec(state["settled"])),
                    "pending_cash": state["pending"],
                    "positions": [
                        {
                            "symbol": s,
                            "shares": str(ledger.shares(state, s)),
                            "wac": str(ledger.wac(state, s)),
                            "price": prices.get(s),
                            "opened_at": min(lot["opened_at"] for lot in state["positions"][s]),
                        }
                        for s in state["positions"]
                    ],
                    "cash_fraction": float(ledger.total_cash(state) / value) if value else None,
                    "costs": state["costs"],
                    "turnover": state["turnover"],
                    "closed_lots": state["closed_lots"],
                    "history_hash": evidence,
                    "corporate_actions_complete": actions_ok,
                },
            )
        self.fill()
