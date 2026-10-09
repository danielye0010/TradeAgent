"""Shared owner-authorized one-shot orchestration over the durable execution lifecycle."""

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from time import sleep as wait_for_poll
from zoneinfo import ZoneInfo

from .broker import utc_time
from .calendar import session_bounds
from .execution_policy import OwnerPolicy, StandingLifecycle, create_policy
from .legacy.standalone import intent_from_row
from .model import Config, Halt, Intent, Risk, dec, digest
from .risk import TERMINAL, check_order, check_state
from .simulator import SCENARIOS, SimClock, SimulatedMCP, funded_snapshot
from .state import State, dumps
from .supervised import OfficialExecutionAdapter

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


def choose_entry(snapshot, config, risk, now, limit, entry=None):
    """Isolated execution canary, not an alpha signal or research selector."""
    check_state(snapshot, risk, now)
    reasons = []
    selection = (
        [entry["symbol"]]
        if entry and "symbol" in entry
        else entry.get("preferred_symbols", [config.allowed_symbols[0]])
        if entry
        else [config.allowed_symbols[0]]
    )
    for symbol in selection:
        if symbol not in config.allowed_symbols:
            raise Halt("explicit entry symbol is outside the configured universe")
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
        if entry:
            intent = Intent(
                symbol,
                "buy",
                dec(entry["quantity"]) if "quantity" in entry else None,
                dec(entry["limit_price"]) if "limit_price" in entry else None,
                order_type=entry["order_type"],
                dollar_amount=dec(entry["dollar_amount"]) if "dollar_amount" in entry else None,
            )
            if intent.risk_notional(snapshot, risk) > budget:
                reasons.append(f"{symbol}: configured entry exceeds available risk/cash budget")
                continue
        else:
            quantity = (budget / ask).to_integral_value(rounding="ROUND_FLOOR")
            if quantity <= 0:
                reasons.append(f"{symbol}: configured whole-share budget is insufficient")
                continue
            intent = Intent(symbol, "buy", quantity, ask)
        if config.mode == "LIVE" and snapshot.countries.get(symbol) != "US":
            reasons.append(f"{symbol}: US equity eligibility unavailable")
            continue
        try:
            check_order(intent, snapshot, config, risk, now, snapshot.nav)
        except Halt as exc:
            reasons.append(f"{symbol}: {exc}")
            continue
        return intent, reasons
    return None, reasons


def validated_plan_entry(plan, prediction, snapshot, config, risk, now, limit, entry):
    """Explicit owner handoff; SHADOW does not call this execution boundary."""
    from .model import timestamp_fresh
    from .research.expressions import TradePlan

    economic = None
    economic_payload = None
    if type(plan) is not TradePlan:
        from .research.tradeplan import EconomicPlan, plan_dict, validate_execution_plan

        if type(plan) is not EconomicPlan:
            raise Halt("execution requires a validated TradePlan")
        economic = plan
        economic_payload = plan_dict(economic)
        validate_execution_plan(economic, now)
        if (
            economic.forecast.value["strategy"] != prediction.strategy_id
            or economic.forecast.value["version"] != prediction.strategy_version
            or economic.forecast.value["decision_time"] != prediction.decision_time
            or economic.forecast.value["horizon"] != prediction.horizon
            or economic.forecast.value["symbol"] != prediction.symbol
        ):
            raise Halt("economic plan and prediction provenance differ")
        plan = economic.decision
    strategy_version = getattr(prediction, "strategy_version", getattr(prediction, "version", None))

    if (
        type(plan) is not TradePlan
        or plan.kind != "UNDERLYING"
        or plan.prediction_id != prediction.prediction_id
        or plan.instrument != prediction.symbol
    ):
        raise Halt("execution requires a matching validated underlying TradePlan and prediction")
    if (
        economic is None
        and not timestamp_fresh(prediction.decision_time, now, risk.max_data_age_seconds)
    ) or prediction.direction != 1:
        raise Halt("strategy plan is stale or not a long equity decision")
    if not prediction.strategy_id or not strategy_version or not prediction.snapshot_id:
        raise Halt("strategy decision provenance is incomplete")
    if economic is not None and snapshot.asks.get(plan.instrument, dec(0)) > dec(plan.entry_limit):
        raise Halt("entry price condition is no longer met")
    requested = dict(entry or {})
    if not ("quantity" in requested or "dollar_amount" in requested):
        raise Halt("validated plan requires explicit owner-configured entry sizing")
    requested.pop("preferred_symbols", None)
    requested["symbol"] = plan.instrument
    if requested.get("order_type", "limit") == "limit" and plan.entry_limit is not None:
        requested["limit_price"] = str(plan.entry_limit)
    intent, reasons = choose_entry(snapshot, config, risk, now, limit, requested)
    if intent is None:
        raise Halt("validated plan rejected: " + "; ".join(reasons))
    return intent, {
        "prediction_id": prediction.prediction_id,
        "strategy_id": prediction.strategy_id,
        "strategy_version": strategy_version,
        "snapshot_id": prediction.snapshot_id,
        "decision_time": prediction.decision_time,
        "plan": asdict(plan),
        "economic_plan": economic_payload,
    }


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        stream.write(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class OneShotRun:
    """Shared production/paper controller over the existing durable order engine."""

    def __init__(
        self, state, broker, clock, config, risk, fence, run_id, options, kill_switch, engine
    ):
        if (
            type(engine) is not StandingLifecycle
            or engine.guard.simulation != engine.broker.is_simulation
        ):
            raise Halt("one-shot controller requires owner-authorized existing lifecycle")
        self.state, self.broker, self.clock = state, broker, clock
        self.options, self.kill_switch = options, Path(kill_switch)
        self.config, self.risk, self.run_id = config, risk, run_id
        self.engine = engine
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
        if self.engine.broker.is_simulation:
            self.clock.advance(seconds)
        else:
            deadline = self.clock() + seconds
            while self.clock() < deadline and not self.kill_switch.exists():
                self.engine.fence()
                self.engine.guard.validate()
                if all(row["status"] in TERMINAL for row in self.rows()):
                    self.audit()  # authoritative ownership/cash monitoring while held
                wait_for_poll(min(5, max(0, deadline - self.clock())))
        self.put("clock", self.clock())

    def rows(self):
        return self.state.db.execute("SELECT * FROM intents ORDER BY rowid").fetchall()

    def submissions(self):
        records = []
        for row in self.rows():
            consumed = self.state.db.execute(
                "SELECT consumed FROM approvals WHERE key=?", (row["key"],)
            ).fetchone()

            def event(kind, key=row["key"]):
                return self.state.db.execute(
                    "SELECT time FROM events WHERE kind=? AND json_extract(payload, '$.key')=? ORDER BY seq LIMIT 1",
                    (kind, key),
                ).fetchone()

            sent = event("placement_send_started")
            not_sent = event("submission_not_sent")
            pre_submission = (
                row["status"] in {"prepared", "reviewed", "abandoned", "risk_rejected"}
                and not row["broker_id"]
                and not sent
                and (not consumed or (row["status"] == "abandoned" and not_sent))
            )
            status = (
                "NOT_SUBMITTED"
                if pre_submission
                else "BROKER_CONFIRMED"
                if row["broker_id"]
                else "SUBMISSION_UNKNOWN"
            )
            records.append(
                {
                    "key": row["key"],
                    "ref_id": row["ref_id"],
                    "side": intent_from_row(row).side,
                    "submission_status": status,
                    "sent_at": sent[0] if sent else None,
                }
            )
        return records

    def submission_status(self):
        statuses = {r["submission_status"] for r in self.submissions()}
        return (
            "SUBMISSION_UNKNOWN"
            if "SUBMISSION_UNKNOWN" in statuses
            else "BROKER_CONFIRMED"
            if "BROKER_CONFIRMED" in statuses
            else "NOT_SUBMITTED"
        )

    def submit(self, intent):
        self.put(intent.side + "_attempt_time", self.clock())
        plan = self.engine.prepare(
            intent,
            f"{self.config.strategy_version}:{intent.side}"
            + (
                f":{sum(intent_from_row(r).side == 'sell' for r in self.rows())}"
                if intent.side == "sell"
                and any(intent_from_row(r).side == "sell" for r in self.rows())
                else ""
            ),
            {
                "execution_canary": not bool((self.get("decision") or {}).get("provenance")),
                "decision_provenance": (self.get("decision") or {}).get("provenance"),
                "synthetic_fixture": self.engine.broker.is_simulation,
                "excluded_from_strategy_performance": not bool(
                    (self.get("decision") or {}).get("provenance")
                ),
            },
        )
        if plan["status"] != "approval_required":
            raise Halt("one-shot side is already reserved; never replay")
        try:
            return self.engine.execute(plan["key"], intent)
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
            self.advance(self.options.get("poll_interval_seconds", 1))
            result = self.engine.reconcile(row["key"], intent)
        row = self.state.db.execute("SELECT * FROM intents WHERE key=?", (row["key"],)).fetchone()
        if row["status"] == "pending" and row["broker_id"]:
            cancellation = "cancel_reserved:" + row["key"]
            if self.get(cancellation):
                raise Halt("cancellation was already attempted; reconcile only, never replay")
            self.put(cancellation, {"order_id": row["broker_id"], "at": self.clock()})
            try:
                result = self.engine.cancel(row["key"], intent)
            except Halt:
                result = self.engine.reconcile(row["key"], intent)
        if result["status"] not in TERMINAL:
            raise Halt("order remains open after bounded cancellation; no repeat order")
        return result

    def audit(self, snapshot=None, *, allow_open=False):
        """Whole-round-trip cash/holdings proof from the core's normalized fills."""
        initial = self.get("initial")
        snapshot = snapshot if snapshot is not None else self.broker.snapshot()
        if initial is None:
            raise Halt(
                "startup has no recorded account baseline; run recover to finalize before new-run"
            )
        orders, seen, submissions = [], set(), []
        expected_cash = dec(initial["cash"])
        positions = {s: dec(q) for s, q in initial["positions"].items()}
        spent, proceeds, fees = dec(0), dec(0), dec(0)
        bought, sold = dec(0), dec(0)
        for row in self.rows():
            intent = intent_from_row(row)
            matches = [
                o
                for o in snapshot.orders
                if o.get("ref_id") == row["ref_id"]
                or (row["broker_id"] and o["id"] == row["broker_id"])
            ]
            record = next(item for item in self.submissions() if item["key"] == row["key"])
            submissions.append(record)
            pre_submission = record["submission_status"] == "NOT_SUBMITTED"
            if pre_submission:
                if matches:
                    raise Halt("broker order contradicts pre-submission intent")
                continue
            if len(matches) != 1:
                raise Halt("unknown order identity; cannot prove final reconciliation")
            order = matches[0]
            if (
                order["id"] in seen
                or (row["broker_id"] and row["broker_id"] != order["id"])
                or order.get("ref_id") != row["ref_id"]
                or not intent.matches_order(order)
                or (
                    row["status"] != order["state"]
                    and not (allow_open and row["status"] in {"submitting", "unknown", "pending"})
                )
                or (order["state"] not in TERMINAL and not allow_open)
            ):
                raise Halt("order/intent identity or terminal status mismatch")
            record["submission_status"] = "BROKER_CONFIRMED"
            seen.add(order["id"])
            quantity = sum((dec(f["quantity"]) for f in order["executions"]), dec(0))
            if (
                quantity != dec(order["cumulative_quantity"])
                or quantity < 0
                or (intent.quantity is not None and quantity > intent.quantity)
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
        from dataclasses import replace

        check_state(
            replace(snapshot, orders=[o for o in snapshot.orders if o["state"] in TERMINAL])
            if allow_open
            else snapshot,
            self.risk,
            self.clock(),
        )
        return {
            "orders": orders,
            "broker_order_count": len(orders),
            "submissions": submissions,
            "submission_status": "SUBMISSION_UNKNOWN"
            if any(s["submission_status"] == "SUBMISSION_UNKNOWN" for s in submissions)
            else "BROKER_CONFIRMED"
            if orders
            else "NOT_SUBMITTED",
            "reconciliation_status": "OPEN_ORDERS"
            if any(o["state"] not in TERMINAL for o in orders)
            else "RECONCILED",
            "initial_cash": initial["cash"],
            "final_cash": str(snapshot.cash),
            "expected_final_cash": str(expected_cash),
            "final_positions": snapshot.positions,
            "bought": str(bought),
            "sold": str(sold),
            "known_fees": str(fees),
            "entry_executed_notional": str(spent),
            "exit_executed_notional": str(proceeds),
            "bot_owned_residual": str(bought - sold),
            "unknown_orders": [],
            "realized_pnl": str(proceeds - spent - fees) if bought == sold and bought > 0 else None,
            "flat_bot_position": bought == sold,
            "unrelated_holdings_unchanged": True,
            "cash_reconciled": True,
        }

    def execute(self, *, allow_entry=True, recover_exit=False, plan=None, prediction=None):
        saved_clock = self.get("clock")
        if saved_clock is not None and self.engine.broker.is_simulation:
            self.clock.advance(max(0, saved_clock - self.clock()))
        if self.get("initial") is None:
            self.put("initial", asdict(self.broker.snapshot()))
        rows = self.rows()
        entries = [r for r in rows if intent_from_row(r).side == "buy"]
        exits = [r for r in rows if intent_from_row(r).side == "sell"]
        if len(entries) > 1:
            raise Halt("lifecycle entry budget violated; inspect status before recovery")
        if self.get("finished"):
            audit = self.audit()
            return {"status": self.get("finished"), "duplicate_suppressed": True, **audit}
        if not entries:
            if not allow_entry:
                return self.finish(
                    "HALTED",
                    reason="no submitted entry; use new-run after reconciliation",
                    outstanding_incident=False,
                )
            if self.kill_switch.exists():
                return self.finish("NO_TRADE", reason="kill switch prevents new exposure")
            initial = self.broker.snapshot()
            bounds = session_bounds(
                datetime.fromtimestamp(self.clock(), ZoneInfo("America/New_York")).date()
            )
            minimum_window = max(
                60, self.options["polls"] * self.options.get("poll_interval_seconds", 1) * 2
            )
            if (
                not bounds
                or self.clock()
                >= bounds[1] - self.options.get("session_buffer_seconds", 600) - minimum_window
            ):
                return self.finish("NO_TRADE", reason="insufficient regular-session exit window")
            if (
                plan is not None
                and hasattr(plan, "exit_at")
                and (plan.exit_at > bounds[1] - self.options.get("session_buffer_seconds", 600))
            ):
                return self.finish(
                    "NO_TRADE", reason="planned horizon exceeds regular-session exit window"
                )
            check_state(initial, self.risk, self.clock())
            self.state.recover(self.run_id, initial)
            provenance = None
            if plan is not None:
                intent, provenance = validated_plan_entry(
                    plan,
                    prediction,
                    initial,
                    self.config,
                    self.risk,
                    self.clock(),
                    self.options["max_notional"],
                    self.options.get("entry"),
                )
                reasons = []
            else:
                intent, reasons = choose_entry(
                    initial,
                    self.config,
                    self.risk,
                    self.clock(),
                    self.options["max_notional"],
                    self.options.get("entry"),
                )
            if intent is None:
                return self.finish("NO_TRADE", reason="; ".join(reasons))
            self.put(
                "decision",
                {
                    "time": self.clock(),
                    "price": str(initial.prices[intent.symbol]),
                    "symbol": intent.symbol,
                    "quantity": str(intent.quantity) if intent.quantity is not None else None,
                    "dollar_amount": str(intent.dollar_amount)
                    if intent.dollar_amount is not None
                    else None,
                    "order_type": intent.order_type,
                    "selection_reasons": reasons,
                    "source": "VALIDATED_TRADE_PLAN"
                    if provenance
                    else "SYNTHETIC_EXECUTION_CANARY"
                    if self.engine.broker.is_simulation
                    else "OWNER_CONFIGURED_EXECUTION",
                    "provenance": provenance,
                },
            )
            self.submit(intent)
            entries = [r for r in self.rows() if intent_from_row(r).side == "buy"]
        entry = entries[0]
        if entry["status"] in {"prepared", "reviewed", "abandoned", "risk_rejected"}:
            audit = self.audit()
            if audit["submission_status"] != "NOT_SUBMITTED":
                raise Halt("entry submission is ambiguous; use recover, never a new entry")
            return self.finish(
                "HALTED",
                reason="entry was not submitted; use new-run after reconciliation",
                outstanding_incident=False,
            )
        if not exits:
            self.settle(entry)
        elif exits[-1]["status"] not in {"prepared", "reviewed", "abandoned", "risk_rejected"}:
            self.settle(exits[-1])
        audit = self.audit()
        if exits and audit["flat_bot_position"]:
            return self._closed(audit)
        if exits and not recover_exit:
            raise Halt(
                "confirmed residual bot exposure; run tradeagent recover --config to manage the remaining exit"
            )
        snapshot = self.broker.snapshot()
        entry_order = next(o for o in snapshot.orders if o.get("ref_id") == entry["ref_id"])
        filled = dec(entry_order["cumulative_quantity"])
        if not filled:
            return self.finish("NO_TRADE", reason="entry terminal without a confirmed fill")
        symbol = intent_from_row(entry).symbol
        original = dec(self.get("initial")["positions"].get(symbol, 0))
        residual = dec(audit["bot_owned_residual"])
        if snapshot.positions.get(symbol, dec(0)) != original + residual:
            raise Halt(
                "bot position differs from confirmed fills; inspect status and reconcile ownership"
            )
        if not exits:
            fill_time = max(utc_time(f["timestamp"]) for f in entry_order["executions"])
            bounds = session_bounds(
                datetime.fromtimestamp(fill_time, ZoneInfo("America/New_York")).date()
            )
            if not bounds:
                raise Halt("holding window has no regular session")
            decision = self.get("decision") or {}
            proposed = (decision.get("provenance") or {}).get("economic_plan")
            requested_due = (
                proposed["exit_at"] if proposed else fill_time + self.options["hold_seconds"]
            )
            deadline = min(
                requested_due, bounds[1] - self.options.get("session_buffer_seconds", 600)
            )
            self.put("exit_due", deadline)
            self.state.event(
                self.run_id,
                "exit_due",
                {"due": deadline, "symbol": symbol, "quantity": str(residual)},
            )
            if not self.kill_switch.exists():
                self.advance(max(0, deadline - self.clock()))
            self.put(
                "exit_reason",
                "kill_switch_risk_reduction"
                if self.kill_switch.exists()
                else "holding_period_elapsed"
                if deadline == requested_due
                else "regular_session_exit_deadline",
            )
        else:
            self.put("exit_reason", "owner_requested_residual_recovery")
        maximum = self.options.get("max_exit_attempts")
        if maximum is not None and len(exits) >= maximum:
            raise Halt(
                "configured exit attempt limit reached; remaining exposure requires owner action"
            )
        if self.engine.broker.is_simulation:
            self.broker.scenario = self.options["exit_scenario"]
        snapshot = self.broker.snapshot()
        sellable = max(dec(0), snapshot.available.get(symbol, dec(0)) - original)
        quantity = min(residual, sellable)
        if quantity <= 0:
            raise Halt(
                f"remaining bot exposure {residual} {symbol} is not sellable; resolve reserved broker shares then recover"
            )
        exit_type = self.options.get("exit_order_type", "limit")
        self.submit(
            Intent(
                symbol,
                "sell",
                quantity,
                snapshot.bids[symbol] if exit_type == "limit" else None,
                order_type=exit_type,
            )
        )
        exits = [r for r in self.rows() if intent_from_row(r).side == "sell"]
        self.settle(exits[-1])
        audit = self.audit()
        if not audit["flat_bot_position"]:
            raise Halt(
                "confirmed residual bot exposure; run tradeagent recover --config to manage the remaining exit"
            )
        return self._closed(audit)

    def _closed(self, audit):
        if dec(audit["entry_executed_notional"]) > dec(self.options["max_notional"]):
            raise Halt(
                "actual entry exceeded configured order value; exposure closed, review required"
            )
        status = (
            "COMPLETED"
            if all(o["state"] == "filled" for o in audit["orders"])
            else "CLOSED_PARTIAL"
        )
        return self.finish(status, **audit)

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
    if not 0 < dec(max_notional):
        raise Halt("entry notional must be positive")
    if type(hold_seconds) is not int or hold_seconds < 0 or type(polls) is not int or polls < 1:
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
        allowed_symbols=["IWM", "QQQ"],
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
            adapter = OfficialExecutionAdapter(sim, sim, clock)
            grant_path = directory / "paper-owner-grant.json"
            if not grant_path.exists():
                atomic_json(
                    grant_path,
                    {
                        "policy": create_policy(
                            config, risk, adapter.account_digest, options, clock, True
                        ),
                        "actor": "SIMULATED_POLICY_OWNER",
                    },
                )
            guard = OwnerPolicy(
                json.loads(grant_path.read_text()),
                state,
                config,
                risk,
                adapter.account_digest,
                options,
                clock,
                simulation=True,
            )
            engine = StandingLifecycle(state, adapter, config, risk, run_id, fence, clock, guard)
            controller = OneShotRun(
                state,
                sim,
                clock,
                config,
                risk,
                fence,
                run_id,
                options,
                kill_switch or directory / "KILL",
                engine,
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


def run_live(settings, *, recover=False, observer=None, plan=None, prediction=None):
    from .execution_policy import owner_run_lock

    with owner_run_lock(settings):
        return _run_live(
            settings, recover=recover, observer=observer, plan=plan, prediction=prediction
        )


def _run_live(settings, *, recover=False, observer=None, plan=None, prediction=None):
    """Explicit owner-launched LIVE command; not called by read-only live-check."""
    import time

    from .broker import Broker
    from .execution_policy import active_settings, check_run_state
    from .standalone_mcp import ExternalOAuthToken, StandaloneExecutionTransport, StandaloneMCP

    settings.validate()
    previous_artifact = check_run_state(settings)
    if recover and previous_artifact is None:
        raise Halt(
            "no existing lifecycle; recover cannot initiate an entry, use run-once for a new lifecycle"
        )
    settings = active_settings(settings)
    selected, config, risk, options = (
        settings.directory,
        settings.config,
        settings.risk,
        settings.options,
    )
    result = {
        "mode": "LIVE",
        "status": "HALTED",
        "execution_canary": plan is None,
        "excluded_from_strategy_performance": plan is None,
        "outstanding_incident": True,
    }
    state, run_id = None, None
    os.umask(0o077)
    try:
        with StandaloneMCP(
            ExternalOAuthToken(settings.oauth_helper, Path(__file__).parent), settings.timeout
        ) as bridge:
            broker = Broker(bridge, config, risk)
            broker.snapshot()
            account = digest(broker.account["account_number"])
            if settings.account_sha256 and account != settings.account_sha256:
                raise Halt("authenticated account differs from configured account pin")
            if observer:
                observer("broker_connected", {"account_key": account[:16]})
            artifact = previous_artifact or {
                "policy": create_policy(
                    config, risk, account, options, owner_config_hash=settings.file_hash
                ),
                "actor": "LOCAL_OWNER",
            }
            state = State(selected / "agent")
            with state.lock(config.lease_seconds, local_owner=True) as fence:
                marker = selected / "live-run.json"
                if previous_artifact is None:
                    with marker.open("x") as stream:
                        stream.write(dumps(artifact))
                        stream.flush()
                        os.fsync(stream.fileno())
                    descriptor = os.open(selected, os.O_DIRECTORY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                state.observer = observer

                def approval_setting():
                    setting = bridge.read(
                        "get_trade_approval_setting",
                        {"account_number": broker.account["account_number"]},
                    )["data"]["setting"]
                    if setting["account_number"] != broker.account["account_number"]:
                        raise Halt("broker approval setting account mismatch")
                    return setting["human_must_approve_trades"]

                guard = OwnerPolicy(
                    artifact,
                    state,
                    config,
                    risk,
                    account,
                    options,
                    time.time,
                    settings=settings,
                    permission_reader=approval_setting,
                )
                guard.validate()
                # Check permission before constructing the write-capable adapter.
                if approval_setting() is not False:
                    raise Halt(
                        "broker trade approvals enabled or uncertain; owner broker setup required"
                    )
                adapter = OfficialExecutionAdapter(
                    broker, StandaloneExecutionTransport(bridge, state, broker, guard), time.time
                )
                run_id = state.start(config, risk)
                engine = StandingLifecycle(
                    state, adapter, config, risk, run_id, fence, time.time, guard
                )
                controller = OneShotRun(
                    state,
                    broker,
                    time.time,
                    config,
                    risk,
                    fence,
                    run_id,
                    options,
                    selected / "KILL",
                    engine,
                )
                try:
                    result.update(
                        controller.execute(
                            allow_entry=not recover,
                            recover_exit=recover,
                            plan=plan,
                            prediction=prediction,
                        )
                    )
                    result["outstanding_incident"] = not result.get(
                        "cash_reconciled", False
                    ) or not result.get("flat_bot_position", False)
                except Halt as exc:
                    result.update(status="HALTED", reason=str(exc), outstanding_incident=True)
                    try:
                        result.update(controller.audit())
                        if result["submission_status"] == "NOT_SUBMITTED":
                            result["outstanding_incident"] = False
                    except Halt as audit_error:
                        result["reconciliation_blocker"] = str(audit_error)
                finally:
                    result["submissions"] = controller.submissions()
                    result["submission_status"] = controller.submission_status()
                    result["submission_times"] = {
                        side: next(
                            (r["sent_at"] for r in result["submissions"] if r["side"] == side), None
                        )
                        for side in ("buy", "sell")
                    }
                    result["attempt_times"] = {
                        side: controller.get(side + "_attempt_time") for side in ("buy", "sell")
                    }
                    result["decision"] = controller.get("decision")
                    if result["decision"]:
                        result["execution_canary"] = not bool(result["decision"].get("provenance"))
                        result["excluded_from_strategy_performance"] = result["execution_canary"]
                    result["exit_due"] = controller.get("exit_due")
                    result["exit_reason"] = controller.get("exit_reason")
                    result["broker_calls"] = bridge.calls
    except (Halt, OSError, ValueError, KeyError, TypeError) as exc:
        result["reason"] = (
            str(exc) if isinstance(exc, Halt) else "local runtime prerequisite failed"
        )
        raise
    finally:
        if state:
            if run_id:
                state.event(run_id, "one_shot_final", result)
                state.finish(run_id, result["status"])
                state.export_log()
            atomic_json(selected / "report.json", result)
            state.close()
    return result


def reconcile_live(settings, *, persist=True):
    """Read-only diagnosis across a code update; preserve the original report and DB."""
    import hashlib
    import sqlite3
    import time
    from types import SimpleNamespace

    from .broker import Broker
    from .execution_policy import active_settings, check_run_state, owner_file, owner_run_lock
    from .standalone_mcp import ExternalOAuthToken, ReadOnlyMCP

    with owner_run_lock(settings):
        if check_run_state(settings, reconciliation_only=True) is None:
            with ReadOnlyMCP(
                ExternalOAuthToken(settings.oauth_helper, Path(__file__).parent), settings.timeout
            ) as bridge:
                broker = Broker(bridge, settings.config, settings.risk)
                observed = broker.snapshot()
                return {
                    "status": "NEW_RUN_READY",
                    "submission_status": "NOT_SUBMITTED",
                    "reconciliation_status": "NO_LIFECYCLE",
                    "real_review_place_cancel_calls": 0,
                    "execution_state_modified": False,
                    "orders": [],
                    "observed_orders": observed.orders,
                    "final_positions": observed.positions,
                    "final_cash": str(observed.cash),
                    "buying_power": str(observed.buying_power),
                    "other_asset_values": observed.other_asset_values,
                    "account_key": observed.account_key,
                    "broker_calls": bridge.calls,
                    "recovery_instruction": "run live-check, then owner-operated run-once for a new entry",
                }
        settings = active_settings(settings)
        marker = json.loads(owner_file(settings.directory / "live-run.json").read_text())
        db = sqlite3.connect(f"file:{settings.directory / 'agent/state.sqlite3'}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            audit = OneShotRun.__new__(OneShotRun)
            audit.state, audit.risk, audit.clock = SimpleNamespace(db=db), settings.risk, time.time
            original = settings.directory / "report.json"
            previous = (
                json.loads(original.read_text()) if original.exists() else {"status": "INTERRUPTED"}
            )
            result = {
                "status": "HALTED",
                "original_status": previous["status"],
                "submission_status": audit.submission_status(),
                "reconciliation_status": "BLOCKED",
                "outstanding_incident": True,
                "original_report_sha256": hashlib.sha256(original.read_bytes()).hexdigest()
                if original.exists()
                else None,
                "reason": previous.get("reason"),
                "real_review_place_cancel_calls": 0,
                "execution_state_modified": False,
            }
            with ReadOnlyMCP(
                ExternalOAuthToken(settings.oauth_helper, Path(__file__).parent), settings.timeout
            ) as bridge:
                broker = Broker(bridge, settings.config, settings.risk)
                observed = broker.snapshot()
                result.update(
                    observed_orders=observed.orders,
                    final_positions=observed.positions,
                    final_cash=str(observed.cash),
                    buying_power=str(observed.buying_power),
                    other_asset_values=observed.other_asset_values,
                    account_key=observed.account_key,
                )
                if digest(broker.account["account_number"]) != marker["policy"]["account_digest"]:
                    raise Halt("reconciliation account differs from existing owner run")
                audit.broker = broker
                try:
                    result.update(audit.audit(observed, allow_open=True))
                    if result["submission_status"] == "NOT_SUBMITTED":
                        result["outstanding_incident"] = False
                    elif result["reconciliation_status"] == "OPEN_ORDERS":
                        result["status"] = "ORDER_OPEN"
                    elif audit.get("finished"):
                        result["status"] = audit.get("finished")
                        result["outstanding_incident"] = not result["flat_bot_position"]
                    else:
                        result["status"] = (
                            "CLOSED" if result["flat_bot_position"] else "POSITION_OPEN"
                        )
                        result["outstanding_incident"] = not result["flat_bot_position"]
                except Halt as exc:
                    result["reconciliation_blocker"] = str(exc)
                    if "contradicts" in str(exc):
                        result["submission_status"] = "SUBMISSION_UNKNOWN"
                result["broker_calls"] = bridge.calls
            result["recovery_instruction"] = (
                "tradeagent new-run --config " + str(settings.path)
                if result.get("flat_bot_position")
                and result["reconciliation_status"] == "RECONCILED"
                and (
                    audit.get("finished") in {"COMPLETED", "CLOSED_PARTIAL", "NO_TRADE"}
                    or result["submission_status"] == "NOT_SUBMITTED"
                )
                else "tradeagent recover --config "
                + str(settings.path)
                + "; reconcile existing broker identity before any new exposure"
            )
            if not persist:
                return result
            target = settings.directory / f"reconciliation-{time.time_ns()}.json"
            result["report_path"] = str(target)
            atomic_json(target, result)
            return result
        finally:
            db.close()


def new_live_run(settings):
    """Explicit owner reset only after fresh read-only proof that the old run is closed."""
    import sqlite3
    import time
    from uuid import uuid4

    from .broker import Broker
    from .execution_policy import active_settings, check_run_state, owner_file, owner_run_lock
    from .standalone_mcp import ExternalOAuthToken, ReadOnlyMCP

    with owner_run_lock(settings):
        if check_run_state(settings, reconciliation_only=True) is None:
            return {
                "status": "NEW_RUN_READY",
                "already_ready": True,
                "real_review_place_cancel_calls": 0,
            }
        settings = active_settings(settings)
        marker = json.loads(owner_file(settings.directory / "live-run.json").read_text())
        # Archiving verifies closure without migrating historical SQLite bytes.
        # The existing durable-completion guard below remains mandatory.
        state = State.__new__(State)
        state.directory = settings.directory / "agent"
        state.db = sqlite3.connect(f"file:{state.directory / 'state.sqlite3'}?mode=ro", uri=True)
        state.db.row_factory = sqlite3.Row
        try:
            with state.lock(settings.config.lease_seconds, local_owner=True):
                with ReadOnlyMCP(
                    ExternalOAuthToken(settings.oauth_helper, Path(__file__).parent),
                    settings.timeout,
                ) as bridge:
                    broker = Broker(bridge, settings.config, settings.risk)
                    snapshot = broker.snapshot()
                    if (
                        digest(broker.account["account_number"])
                        != marker["policy"]["account_digest"]
                    ):
                        raise Halt("archive account differs from owner run")
                    # Reuse the existing accounting audit without creating any write adapter.
                    audit = OneShotRun.__new__(OneShotRun)
                    audit.state, audit.broker, audit.risk, audit.clock = (
                        state,
                        broker,
                        settings.risk,
                        time.time,
                    )
                    evidence = audit.audit()
                    if (
                        audit.get("finished") not in {"COMPLETED", "CLOSED_PARTIAL", "NO_TRADE"}
                        and evidence["submission_status"] != "NOT_SUBMITTED"
                    ):
                        raise Halt("unfinished/incident run cannot be replaced")
                    if not evidence["flat_bot_position"]:
                        raise Halt("residual bot-owned position prevents a new run")
                    check_state(snapshot, settings.risk, time.time())
                    atomic_json(settings.directory / "archive-reconciliation.json", evidence)
        finally:
            state.close()
        archive_root = settings.directory.with_name(settings.directory.name + ".history")
        archive_root.mkdir(mode=0o700, exist_ok=True)
        archive = archive_root / str(uuid4())
        # A crash between these operations leaves the receipt in place and halts safely.
        settings.directory.rename(archive)
        if settings.receipt.exists():
            settings.receipt.rename(archive / "owner-config.run.json")
        for parent in {settings.directory.parent, settings.receipt.parent, archive}:
            descriptor = os.open(parent, os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return {
            "status": "NEW_RUN_READY",
            "archived_run": str(archive),
            "real_review_place_cancel_calls": 0,
            "next_run_requires_explicit_launch": True,
        }
