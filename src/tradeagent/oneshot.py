"""One-shot paper orchestration over the existing durable execution lifecycle."""

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .broker import utc_time
from .calendar import session_bounds
from .demo import simulated_human
from .legacy.standalone import intent_from_row
from .model import Config, Halt, Intent, Risk, dec, digest
from .risk import TERMINAL, check_order, check_state
from .simulator import SCENARIOS, SimClock, SimulatedMCP, funded_snapshot
from .state import State, dumps
from .supervised import OfficialExecutionAdapter, SupervisedLifecycle

IDENTITY = "one-shot-paper-execution-canary-v1"


def paper_snapshot(clock):
    """Invented prices for software testing; explicitly NOT market observations."""
    s = funded_snapshot(clock)
    s.nav = s.cash = s.buying_power = dec(1000)
    s.prices = {"IWM": dec("10.00"), "QQQ": dec("12.00")}
    s.bids = {k: v - dec(".01") for k, v in s.prices.items()}
    s.asks = {k: v + dec(".01") for k, v in s.prices.items()}
    s.quote_times = {k: clock() for k in s.prices}
    s.bid_times, s.ask_times = dict(s.quote_times), dict(s.quote_times)
    s.tradable = {k: True for k in s.prices}
    s.liquidity = {k: {"asof": clock(), "bid_size": 10000, "ask_size": 10000} for k in s.prices}
    s.option_quotes = {}
    s.option_level = ""
    return s


def choose_entry(snapshot, config, risk, now, limit):
    """Isolated execution canary, not an alpha signal or research selector."""
    check_state(snapshot, risk, now)
    reasons = []
    for symbol in sorted(
        config.allowed_symbols, key=lambda s: (snapshot.asks.get(s, dec("1e100")), s)
    ):
        if snapshot.positions.get(symbol, 0):
            reasons.append(f"{symbol}: existing position is not bot-owned")
            continue
        ask = snapshot.asks.get(symbol)
        if ask is None or ask <= 0:
            reasons.append(f"{symbol}: quote unavailable")
            continue
        budget = min(
            dec(limit),
            snapshot.cash,
            snapshot.buying_power,
            snapshot.nav * dec(risk.max_new_exposure_fraction),
            snapshot.nav * dec(risk.max_position_fraction),
            max(dec(0), snapshot.cash - snapshot.nav * dec(risk.min_cash_fraction)),
        )
        quantity = (budget / ask).to_integral_value(rounding="ROUND_FLOOR")
        if quantity <= 0:
            reasons.append(
                f"{symbol}: capital limit cannot buy one whole share; no fractional fallback"
            )
            continue
        intent = Intent(symbol, "buy", quantity, ask)
        try:
            check_order(intent, snapshot, config, risk, now, snapshot.nav)
        except Halt as exc:
            reasons.append(f"{symbol}: {exc}")
            continue
        return intent, reasons
    return None, reasons


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        stream.write(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class PaperRun:
    """Paper-only controller; broker review/submit/cancel/reconcile stay in core."""

    def __init__(self, state, broker, clock, config, risk, fence, run_id, options, kill_switch):
        if type(broker) is not SimulatedMCP or broker.is_simulation is not True:
            raise Halt("paper controller requires the local simulator; no live transport")
        self.state, self.sim, self.clock = state, broker, clock
        self.options, self.kill_switch = options, Path(kill_switch)
        self.config, self.risk, self.run_id = config, risk, run_id
        self.engine = SupervisedLifecycle(
            state,
            OfficialExecutionAdapter(broker, broker, clock),
            config,
            risk,
            run_id,
            fence,
            clock,
        )
        state.db.execute(
            "CREATE TABLE IF NOT EXISTS one_shot_meta(key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        state.db.commit()

    def get(self, key):
        row = self.state.db.execute(
            "SELECT payload FROM one_shot_meta WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        with self.state.db:
            self.state.db.execute(
                "INSERT OR REPLACE INTO one_shot_meta VALUES(?,?)", (key, dumps(value))
            )

    def advance(self, seconds):
        self.clock.advance(seconds)
        self.put("clock", self.clock())

    def rows(self):
        return self.state.db.execute("SELECT * FROM intents ORDER BY rowid").fetchall()

    def submit(self, intent):
        self.put(intent.side + "_submission_time", self.clock())
        plan = self.engine.prepare(
            intent,
            f"{IDENTITY}:{intent.side}",
            {
                "execution_canary": True,
                "synthetic_fixture": True,
                "excluded_from_strategy_performance": True,
            },
        )
        if plan["status"] != "approval_required":
            raise Halt("one-shot side is already reserved; never replay")
        try:
            return self.engine.execute(plan["key"], intent, simulated_human(plan))
        except Halt:
            row = self.state.db.execute(
                "SELECT * FROM intents WHERE key=?", (plan["key"],)
            ).fetchone()
            if row["status"] not in {"unknown", "submitting", "pending"}:
                raise
            # A timeout is a READ/reconcile path only, never another submit.
            return self.engine.reconcile(row["key"], intent)

    def settle(self, row):
        intent = intent_from_row(row)
        if row["status"] in {"prepared", "reviewed", "abandoned", "risk_rejected"}:
            raise Halt("interrupted unsubmitted intent; reserved side cannot be replayed")
        result = self.engine.reconcile(row["key"], intent)
        for _ in range(self.options["polls"]):
            if result["status"] in TERMINAL:
                return result
            self.advance(1)
            result = self.engine.reconcile(row["key"], intent)
        row = self.state.db.execute("SELECT * FROM intents WHERE key=?", (row["key"],)).fetchone()
        if row["status"] == "pending" and row["broker_id"]:
            binding = {
                "action": "cancel",
                "key": row["key"],
                "order_id": row["broker_id"],
                "account_digest": self.engine.broker.account_digest,
                "simulation": True,
                "expires": self.clock() + 30,
            }
            result = self.engine.cancel(row["key"], intent, simulated_human({"binding": binding}))
        if result["status"] not in TERMINAL:
            raise Halt("order remains open after bounded cancellation; no repeat order")
        return result

    def audit(self):
        """Whole-round-trip cash/holdings proof from the core's normalized fills."""
        initial = self.get("initial")
        snapshot = self.sim.snapshot()
        orders, seen = [], set()
        expected_cash = dec(initial["cash"])
        positions = {s: dec(q) for s, q in initial["positions"].items()}
        spent, proceeds, fees = dec(0), dec(0), dec(0)
        bought, sold = dec(0), dec(0)
        for row in self.rows():
            intent = intent_from_row(row)
            matches = [o for o in snapshot.orders if o.get("ref_id") == row["ref_id"]]
            if len(matches) != 1:
                raise Halt("unknown order identity; cannot prove final reconciliation")
            order = matches[0]
            if (
                order["id"] in seen
                or (row["broker_id"] and row["broker_id"] != order["id"])
                or order["symbol"] != intent.symbol
                or order["side"] != intent.side
                or dec(order["quantity"]) != intent.quantity
                or dec(order["price"]) != intent.limit_price
                or row["status"] != order["state"]
                or order["state"] not in TERMINAL
            ):
                raise Halt("order/intent identity or terminal status mismatch")
            seen.add(order["id"])
            quantity = sum((dec(f["quantity"]) for f in order["executions"]), dec(0))
            if (
                quantity != dec(order["cumulative_quantity"])
                or not 0 <= quantity <= intent.quantity
            ):
                raise Halt("fill detail does not match confirmed cumulative quantity")
            notional = sum(
                (dec(f["quantity"]) * dec(f["price"]) for f in order["executions"]), dec(0)
            )
            actual_fees = dec(order.get("fees"))
            if actual_fees < 0:
                raise Halt("invalid actual fees")
            sign = 1 if intent.side == "buy" else -1
            positions[intent.symbol] = positions.get(intent.symbol, dec(0)) + quantity * sign
            if positions[intent.symbol] == 0:
                positions.pop(intent.symbol)
            expected_cash -= notional * sign + actual_fees
            fees += actual_fees
            if intent.side == "buy":
                spent += notional
                bought += quantity
            else:
                proceeds += notional
                sold += quantity
            orders.append(order)
        initial_orders = {o["id"] for o in initial["orders"]}
        if {o["id"] for o in snapshot.orders} != seen | initial_orders:
            raise Halt("unowned broker order appeared during the one-shot run")
        if snapshot.positions != positions or snapshot.options != {
            k: dec(v) for k, v in initial["options"].items()
        }:
            raise Halt("unexplained position movement; unrelated holdings must be unchanged")
        if abs(snapshot.cash - expected_cash) > dec(".01"):
            raise Halt("confirmed fills and fees do not reconcile cash")
        check_state(snapshot, self.risk, self.clock())
        return {
            "orders": orders,
            "initial_cash": initial["cash"],
            "final_cash": str(snapshot.cash),
            "expected_final_cash": str(expected_cash),
            "final_positions": snapshot.positions,
            "bought": str(bought),
            "sold": str(sold),
            "known_fees": str(fees),
            "realized_pnl": str(proceeds - spent - fees) if bought == sold and bought > 0 else None,
            "flat_bot_position": bought == sold,
            "unrelated_holdings_unchanged": True,
            "cash_reconciled": True,
        }

    def execute(self):
        saved_clock = self.get("clock")
        if saved_clock is not None:
            self.clock.advance(max(0, saved_clock - self.clock()))
        if self.get("initial") is None:
            self.put("initial", asdict(self.sim.snapshot()))
        rows = self.rows()
        if (
            len(rows) > 2
            or sum(intent_from_row(r).side == "buy" for r in rows) > 1
            or sum(intent_from_row(r).side == "sell" for r in rows) > 1
        ):
            raise Halt("one-shot side budget violated")
        if self.get("finished"):
            # Never interpret a stale report as proof of current broker state.
            audit = self.audit()
            return {"status": self.get("finished"), "duplicate_suppressed": True, **audit}
        entries = [r for r in rows if intent_from_row(r).side == "buy"]
        exits = [r for r in rows if intent_from_row(r).side == "sell"]
        if not entries:
            if self.kill_switch.exists():
                return self.finish("NO_TRADE", reason="kill switch prevents new exposure")
            initial = self.sim.snapshot()
            check_state(initial, self.risk, self.clock())
            self.state.recover(self.run_id, initial)
            intent, reasons = choose_entry(
                initial, self.config, self.risk, self.clock(), self.options["max_notional"]
            )
            if intent is None:
                return self.finish("NO_TRADE", reason="; ".join(reasons))
            self.put(
                "decision",
                {
                    "time": self.clock(),
                    "price": str(initial.prices[intent.symbol]),
                    "symbol": intent.symbol,
                    "quantity": str(intent.quantity),
                    "source": "SYNTHETIC_EXECUTION_CANARY",
                },
            )
            self.submit(intent)
            entries = [r for r in self.rows() if intent_from_row(r).side == "buy"]
        entry = entries[0]
        if not exits:
            self.settle(entry)
            snapshot = self.sim.snapshot()
            order = next(o for o in snapshot.orders if o.get("ref_id") == entry["ref_id"])
            filled = dec(order["cumulative_quantity"])
            if not filled:
                return self.finish("NO_TRADE", reason="entry terminal without a confirmed fill")
            if filled != filled.to_integral_value():
                raise Halt("fractional residual unsupported by whole-share exit risk gate")
            symbol = intent_from_row(entry).symbol
            if (
                dec(self.get("initial")["positions"].get(symbol, 0)) != 0
                or snapshot.positions.get(symbol) != filled
            ):
                raise Halt("position ownership cannot be distinguished safely")
            fill_time = max(utc_time(f["timestamp"]) for f in order["executions"])
            bounds = session_bounds(
                datetime.fromtimestamp(fill_time, ZoneInfo("America/New_York")).date()
            )
            if not bounds:
                raise Halt("holding window has no regular session")
            requested_due = fill_time + self.options["hold_seconds"]
            deadline = min(requested_due, bounds[1] - 600)
            self.put("exit_due", deadline)
            if self.kill_switch.exists():
                reason = "kill_switch_risk_reduction"
            else:
                self.advance(max(0, deadline - self.clock()))
                reason = (
                    "holding_period_elapsed"
                    if deadline == requested_due
                    else "regular_session_exit_deadline"
                )
            # Simulated clock advances rather than sleeping an hour. No order/fill
            # is manufactured by this advance; the normal simulator submits/fills.
            self.put("exit_reason", reason)
            self.sim.scenario = self.options["exit_scenario"]
            snapshot = self.sim.snapshot()
            self.submit(Intent(symbol, "sell", filled, snapshot.bids[symbol]))
            exits = [r for r in self.rows() if intent_from_row(r).side == "sell"]
        self.settle(exits[0])
        audit = self.audit()
        if not audit["flat_bot_position"]:
            raise Halt("exit terminal but bot-owned exposure remains; no second exit authorized")
        return self.finish("COMPLETED", **audit)

    def finish(self, status, **extra):
        audit = self.audit()
        self.put("finished", status)
        return {"status": status, **audit, **extra}


def run_paper(
    directory,
    max_notional="25",
    hold_seconds=3600,
    polls=3,
    entry_scenario="full_fill",
    exit_scenario="full_fill",
    kill_switch=None,
    initial=None,
):
    if not 0 < dec(max_notional) <= 25:
        raise Halt("one-shot entry notional must be positive and at most $25")
    if (
        type(hold_seconds) is not int
        or not 0 <= hold_seconds <= 21600
        or type(polls) is not int
        or not 1 <= polls <= 30
    ):
        raise Halt("invalid bounded holding/poll policy")
    if entry_scenario not in SCENARIOS or exit_scenario not in SCENARIOS:
        raise Halt("unknown simulation scenario")
    directory = Path(directory).resolve()
    options = {
        "max_notional": str(dec(max_notional)),
        "hold_seconds": hold_seconds,
        "polls": polls,
        "entry_scenario": entry_scenario,
        "exit_scenario": exit_scenario,
    }
    marker = directory / "paper-run.json"
    options["fixture_digest"] = (
        digest(asdict(initial)) if initial else "builtin-synthetic-prices-v1"
    )
    if directory.exists() and not marker.exists() and any(directory.iterdir()):
        raise Halt("paper run requires a fresh directory or its own exact existing marker")
    os.umask(0o077)
    directory.mkdir(parents=True, exist_ok=True)
    expected = {"identity": IDENTITY, "options": options, "simulation": True}
    try:
        with marker.open("x") as stream:
            stream.write(dumps(expected) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if json.loads(marker.read_text()) != expected:
            raise Halt("one-shot configuration changed; use a new paper directory") from None
    clock = SimClock()
    config = Config(
        mode="SUPERVISED",
        supervised_enabled=True,
        strategy_version=IDENTITY,
        state_dir=str(directory / "agent"),
        allowed_symbols=["QQQ", "IWM"],
    )
    risk = Risk()
    state = State(directory / "agent")
    sim = None
    result = {
        "mode": "PAPER",
        "synthetic_fixture": True,
        "execution_canary": True,
        "excluded_from_strategy_performance": True,
        "real_broker_calls": 0,
        "clock": "virtual; no real-time waiting",
        "status": "HALTED",
    }
    try:
        with state.lock(config.lease_seconds) as fence:
            sim = SimulatedMCP(
                directory / "broker", clock, entry_scenario, initial or paper_snapshot(clock)
            )
            run_id = state.start(config, risk)
            controller = PaperRun(
                state,
                sim,
                clock,
                config,
                risk,
                fence,
                run_id,
                options,
                kill_switch or directory / "KILL",
            )
            try:
                result.update(controller.execute())
            except Halt as exc:
                result.update(
                    status="HALTED",
                    reason=str(exc),
                    outstanding_incident=True,
                    residual_positions=sim.snapshot().positions,
                )
                try:
                    result.update(controller.audit())
                except Halt as audit_error:
                    result["reconciliation_blocker"] = str(audit_error)
                state.event(run_id, "one_shot_incident", {"priority": "HIGH", **result})
            except SystemExit:
                result.update(
                    status="HALTED",
                    reason="simulated process interruption; reconcile without replay",
                    outstanding_incident=True,
                    residual_positions=sim.snapshot().positions,
                )
                state.event(
                    run_id, "one_shot_fault", {"status": "interrupted", "retry_submission": False}
                )
                raise
            finally:
                observed = sim.snapshot()
                result["observed_account"] = {
                    "cash": str(observed.cash),
                    "buying_power": str(observed.buying_power),
                    "nav": str(observed.nav),
                    "positions": observed.positions,
                    "orders": observed.orders,
                }
                result["decision"] = controller.get("decision")
                result["submission_times"] = {
                    side: controller.get(side + "_submission_time") for side in ("buy", "sell")
                }
                result["exit_due"] = controller.get("exit_due")
                result["exit_reason"] = controller.get("exit_reason")
                result["simulated_calls_this_process"] = sim.calls
                state.finish(run_id, result["status"])
                state.export_log()
                atomic_json(directory / "report.json", result)
    finally:
        if sim:
            sim.close()
        state.close()
    return result
