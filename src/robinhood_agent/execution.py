"""Risk and mode gates shared by shadow and the fake-tested supervised contract.

The installed production Broker intentionally has no write capability. Implementing
its supervised capability is a separate, explicitly authorized release.
"""

import time

from .model import Halt, dec, digest
from .risk import TERMINAL, check_order


class Execution:
    def __init__(self, state, broker, config, risk, run_id, fence):
        self.state, self.broker, self.config, self.risk = state, broker, config, risk
        self.run_id, self.fence = run_id, fence

    def process(
        self,
        intent,
        snapshot,
        baseline,
        bar_time,
        approval=None,
        cycle_exposure=0,
        cycle_turnover=0,
    ):
        c, r = self.config, self.risk
        c.validate()
        check_order(intent, snapshot, c, r, time.time(), baseline, cycle_exposure, cycle_turnover)
        key = digest(
            [
                snapshot.account_key,
                c.strategy_version,
                # Config/risk revisions cannot duplicate this account/signal decision.
                intent.symbol,
                intent.side,
                bar_time,
            ]
        )
        self.fence()
        ref_id = self.state.prepare(key, self.run_id, intent)
        if ref_id is None:
            self.state.event(self.run_id, "duplicate_intent_suppressed", {"key": key})
            return {"key": key, "status": "duplicate_suppressed"}
        proposal = {"key": key, "ref_id": ref_id, "payload": intent.payload(), "mode": c.mode}
        self.state.event(self.run_id, "proposed_order", proposal)
        if c.mode == "SHADOW":
            self.state.update(key, "shadow_recorded")
            self.state.event(
                self.run_id,
                "mode_gate",
                {
                    "key": key,
                    "decision": "shadow_only",
                    "broker_review": "not_called",
                    "submission": "not_called",
                    "cancellation": "not_called",
                },
            )
            return {**proposal, "status": "shadow_recorded"}
        if c.mode != "SUPERVISED" or not c.supervised_enabled:
            raise Halt("execution mode is disabled")
        # This contract is exercised with fake brokers only in this release.
        review = self.broker.review(intent)
        if not isinstance(review, dict) or review.get("checks_passed") is not True:
            raise Halt("review failed or unrecognized review checks")
        if review.get("payload") != intent.payload():
            raise Halt("review does not match exact intent")
        if not 0 <= time.time() - review.get("asof", 0) <= 30:
            raise Halt("review is stale")
        self.state.event(self.run_id, "reviewed_order", {"key": key, "review": review})
        self.state.update(key, "reviewed")
        approval_hash = digest([proposal, review])
        if not isinstance(approval, str) or approval != approval_hash:
            self.state.event(
                self.run_id, "approval_required", {"key": key, "approval_hash": approval_hash}
            )
            raise Halt("exact reviewed order needs explicit user approval")
        self.fence()
        fresh = self.broker.snapshot()
        self.broker.reconcile(snapshot)
        check_order(intent, fresh, c, r, time.time(), baseline, cycle_exposure, cycle_turnover)
        self.state.event(self.run_id, "user_approval", {"key": key, "approval_hash": approval_hash})
        self.fence()
        if not 0 <= time.time() - review.get("asof", 0) <= 30:
            raise Halt("review expired before submission")
        self.state.update(key, "submitting")  # Durable before I/O; never blindly replay.
        try:
            receipt = self.broker.submit(intent, ref_id)
        except Exception as exc:
            self.state.update(key, "unknown")
            self.state.event(self.run_id, "submission_uncertain", {"key": key})
            raise Halt("submission uncertain; reconcile before any further action") from exc
        broker_id = receipt.get("id") if isinstance(receipt, dict) else None
        self.state.update(key, "pending", broker_id)
        self.state.event(self.run_id, "submitted_order", {"key": key, "broker_id": broker_id})
        final = self.broker.snapshot()
        matches = [
            o
            for o in final.orders
            if (broker_id and o["id"] == broker_id) or o.get("ref_id") == ref_id
        ]
        if len(matches) != 1 or matches[0]["state"] not in TERMINAL:
            raise Halt("submission not terminal/reconciled; stop for operator")
        self.state.recover(self.run_id, final)
        return {**proposal, "status": matches[0]["state"], "broker_id": matches[0]["id"]}


def apply_shadow(snapshot, intent):
    """Reserve worst-case cash/shares locally for subsequent decisions; no broker fill simulation."""
    notional = intent.quantity * intent.limit_price
    held = snapshot.positions.get(intent.symbol, dec(0))
    if intent.side == "buy":
        snapshot.cash -= notional
        snapshot.buying_power -= notional
        snapshot.positions[intent.symbol] = held + intent.quantity
        snapshot.available[intent.symbol] = dec(0)
    else:
        snapshot.positions[intent.symbol] = held - intent.quantity
        snapshot.available[intent.symbol] -= intent.quantity
        # Do not spend hypothetical sell proceeds before real settlement.
        snapshot.cash += notional
