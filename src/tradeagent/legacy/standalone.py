"""One-shot deterministic orchestration of the existing policy/order lifecycle."""

import json
import time
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from ..model import Halt, Intent, dec
from ..risk import check_state
from .autonomous import AutonomousCanaryLifecycle, AutonomousPolicyLifecycle
from .strategy import TestTrendStrategy


def intent_from_row(row):
    payload = json.loads(row["payload"])
    if set(payload) != {
        "symbol",
        "side",
        "quantity",
        "limit_price",
        "type",
        "time_in_force",
        "market_hours",
    } or (
        payload["type"] != "limit"
        or payload["time_in_force"] != "gfd"
        or payload["market_hours"] != "regular_hours"
    ):
        raise Halt("recovery requires an exact ordinary equity intent")
    return Intent(
        payload["symbol"], payload["side"], dec(payload["quantity"]), dec(payload["limit_price"])
    )


def run_policy_once(
    action,
    state,
    adapter,
    config,
    risk,
    guard,
    facts,
    selector,
    histories_reader,
    clock=time.time,
    fresh_facts_reader=None,
):
    """No alternate order engine, timer, retries, cancellation or automatic exit."""
    if action not in {"canary-buy", "canary-exit", "run-once"}:
        raise Halt("unknown standalone action")
    phase = guard.validate()["phase"]
    if (action.startswith("canary-") and phase != "CANARY") or (
        action == "run-once" and phase != "LIMITED_EQUITY"
    ):
        raise Halt("runner/deployment phase mismatch")
    if guard.simulation != adapter.is_simulation:
        raise Halt("runner simulation/production mismatch")
    result = {
        "status": "halted",
        "action": action,
        "simulation": adapter.is_simulation,
        "orders": [],
        "reconciliation": "not_started",
    }
    with state.lock(config.lease_seconds) as fence:
        run = state.start(config, risk)
        result["run_id"] = run
        try:
            factory = AutonomousCanaryLifecycle if phase == "CANARY" else AutonomousPolicyLifecycle
            common = (state, adapter, config, risk, run, fence, guard)
            lifecycle = (
                factory(
                    *common,
                    facts,
                    selector,
                    clock,
                    histories_reader,
                    selection_facts_reader=fresh_facts_reader,
                )
                if phase == "CANARY"
                else factory(*common, clock, facts, selector)
            )
            original_risk = lifecycle._risk

            def checked_risk(intent, snapshot, baseline, fees=0):
                if fresh_facts_reader is not None:
                    fresh = fresh_facts_reader()
                    anchor, observed = facts.get(intent.symbol), fresh.get(intent.symbol)
                    if anchor is None or observed is None:
                        raise Halt("current instrument classification unavailable")
                    observed.validate(clock(), canary=phase == "CANARY")
                    fields = (
                        "symbol",
                        "instrument_id",
                        "exchange",
                        "ordinary_common",
                        "adr",
                        "etf",
                        "leveraged_inverse",
                        "corporate_action_clear",
                        "evidence_source",
                    )
                    if any(getattr(anchor, k) != getattr(observed, k) for k in fields):
                        raise Halt(
                            "fresh instrument identity/classification differs from signed anchor"
                        )
                    state.event(
                        run,
                        "fresh_universe_evidence",
                        {"symbol": intent.symbol, "facts": asdict(observed)},
                    )
                return original_risk(intent, snapshot, baseline, fees)

            lifecycle._risk = checked_risk
            initial = adapter.snapshot()
            state.event(run, "portfolio_snapshot", asdict(initial))
            # Reconcile existing submissions with the normal fee/cash/position
            # lifecycle BEFORE recover() marks terminal rows. Never replay.
            rows = state.db.execute(
                "SELECT * FROM intents WHERE status IN ('submitting','unknown','pending')"
            ).fetchall()
            if rows:
                for row in rows:
                    result["orders"].append(lifecycle.reconcile(row["key"], intent_from_row(row)))
                result["status"] = "reconciled_only"
                result["reconciliation"] = "authoritative"
                return result
            check_state(initial, risk, clock())
            state.recover(run, initial)
            result["reconciliation"] = "startup_matched"
            grant_id = guard.artifact["policy"]["grant_id"]
            side = "buy" if action == "canary-buy" else "sell"
            if (
                phase == "CANARY"
                and state.db.execute(
                    "SELECT 1 FROM policy_decisions WHERE grant_id=? AND side=?", (grant_id, side)
                ).fetchone()
            ):
                raise Halt("canary side already consumed; no replay")
            histories = histories_reader()
            state.event(run, "historical_snapshot", histories)
            day = datetime.fromtimestamp(clock(), ZoneInfo("America/New_York")).date().isoformat()
            if action == "canary-buy":
                if fresh_facts_reader is not None:
                    current = fresh_facts_reader()
                    selected, decisions = selector.select(
                        initial, config, risk, current, histories, clock(), initial.nav
                    )
                    if selected is None:
                        result.update(status="NO_TRADE", selection=decisions)
                        return result
                prepared = lifecycle.prepare_buy(day)
                if isinstance(prepared, dict):
                    result.update(status="NO_TRADE", selection=prepared["decisions"])
                    return result
                plan, intent = prepared
            elif action == "canary-exit":
                plan, intent = lifecycle.prepare_sell(day)
            else:
                strategy = TestTrendStrategy()
                signals = strategy.signals(histories, config)
                state.event(run, "signal_snapshot", {k: asdict(v) for k, v in signals.items()})
                # One invocation, one deterministic decision at most. Repeated
                # signed in-envelope invocations do not require per-order approval.
                choices = [
                    (s, strategy.proposal(signals[s], initial, config))
                    for s in sorted(strategy.universe(config))
                ]
                choices = [(s, i) for s, i in choices if i is not None]
                if not choices:
                    result["status"] = "NO_TRADE"
                    return result
                symbol, intent = choices[0]
                plan = lifecycle.prepare(intent, signals[symbol].bar_time, asdict(signals[symbol]))
            if plan["status"] != "approval_required":
                result.update(status=plan["status"])
                return result
            try:
                outcome = lifecycle.execute(plan["key"], intent)
            except Halt:
                row = state.db.execute(
                    "SELECT status FROM intents WHERE key=?", (plan["key"],)
                ).fetchone()
                if not row or row["status"] != "unknown":
                    raise
                # A lost ACK may still have an authoritative broker reference.
                # Read reconciliation ONLY; never invoke execute again.
                outcome = lifecycle.reconcile(plan["key"], intent)
            result["orders"].append(outcome)
            result["status"] = result["orders"][-1]["status"]
            result["reconciliation"] = "authoritative"
            final = adapter.snapshot()
            state.event(
                run,
                "final_reconciliation",
                {"decision": "authoritative", "snapshot": asdict(final)},
            )
            result["account_after"] = {
                "cash": str(final.cash),
                "nav": str(final.nav),
                "positions": final.positions,
                "orders": final.orders,
            }
            return result
        except Exception as exc:
            state.event(run, "standalone_halt", {"class": type(exc).__name__})
            result.update(
                status="halted",
                reason=str(exc)
                if isinstance(exc, Halt)
                else "unexpected error; operator investigation required",
            )
            return result
        finally:
            state.event(run, "standalone_result", result)
            state.finish(run, result["status"])
            state.export_log()
