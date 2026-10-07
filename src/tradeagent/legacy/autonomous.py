"""Dormant policy-authorized canary uses the unchanged supervised order lifecycle."""

import json
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from ..model import Halt, digest
from ..supervised import SupervisedLifecycle
from .policy import require_autonomous_release


class GrantDecisionVerifier:
    def __init__(self, guard, adapter):
        self.guard, self.adapter = guard, adapter

    def verify(self, artifact, binding, simulation=False):
        if artifact != self.guard.artifact or simulation != self.guard.simulation:
            raise Halt("autonomous artifact/mode mismatch")
        self.guard.validate()
        ctx = self.guard.context
        if (
            self.adapter.account_digest != ctx.account_digest
            or binding.get("account_digest") != ctx.account_digest
            or binding.get("schema_hash") != ctx.schema_hash
            or digest(binding.get("config")) != ctx.config_hash
            or digest(binding.get("risk")) != ctx.risk_hash
        ):
            raise Halt("decision outside active policy context")
        if "payload" not in binding:
            raise Halt("policy grant does not authorize cancellation")


class PolicyExecutionAdapter:
    """Recheck grant expiry/context at the actual review/place boundary."""

    def __init__(self, adapter, guard):
        self.adapter, self.guard = adapter, guard

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    def review(self, intent, ref_id):
        self.guard.validate()
        return self.adapter.review(intent, ref_id)

    def submit(self, intent, ref_id):
        self.guard.validate()
        return self.adapter.submit(intent, ref_id)

    def cancel(self, *args, **kwargs):
        raise Halt("policy does not authorize automatic cancellation")


class AutonomousPolicyLifecycle(SupervisedLifecycle):
    """Dormant whole-equity policy authorization with all ordinary lifecycle gates."""

    def __init__(self, state, adapter, config, risk, run, fence, guard, clock, facts, selector):
        if not adapter.is_simulation:
            require_autonomous_release()
        if guard.simulation != adapter.is_simulation:
            raise Halt("grant/adapter simulation mismatch")
        if (
            type(self) is AutonomousPolicyLifecycle
            and guard.validate()["phase"] != "LIMITED_EQUITY"
        ):
            raise Halt("ordinary policy lifecycle requires LIMITED_EQUITY grant")
        self.guard = guard
        self.facts, self.selector = facts, selector
        super().__init__(
            state, PolicyExecutionAdapter(adapter, guard), config, risk, run, fence, clock
        )

    def _risk(self, intent, snapshot, baseline, fees=0):
        context = self.guard.context
        if (
            self.broker.account_digest != context.account_digest
            or self.broker.contracts.hash != context.schema_hash
            or digest(asdict(self.config)) != context.config_hash
            or digest(asdict(self.risk)) != context.risk_hash
        ):
            raise Halt("active engine outside policy context")
        self.guard.check(intent, snapshot, baseline)
        f = self.facts.get(intent.symbol)
        if f is None:
            raise Halt("instrument classification unavailable")
        f.validate(self.clock(), canary=self.guard.validate()["phase"] == "CANARY")
        if (
            digest({k: asdict(v) for k, v in sorted(self.facts.items())})
            != self.guard.context.universe_evidence_hash
            or self.selector.hash != self.guard.context.universe_rules_hash
        ):
            raise Halt("universe evidence/rules outside grant")
        super()._risk(intent, snapshot, baseline, fees)

    def execute(self, key, intent, artifact=None, verifier=None):
        if artifact is not None or verifier is not None:
            raise Halt("autonomous decisions accept only the enrolled policy, no order override")
        self.guard.validate()
        self.state.event(
            self.run,
            "autonomous_policy_authorization",
            {
                "key": key,
                "grant_id": self.guard.artifact["policy"]["grant_id"],
                "policy_hash": digest(self.guard.artifact["policy"]),
                "policy": self.guard.artifact["policy"],
            },
        )
        return super().execute(
            key, intent, self.guard.artifact, GrantDecisionVerifier(self.guard, self.broker)
        )

    def cancel(self, *args, **kwargs):
        raise Halt("policy does not authorize automatic cancellation")


class AutonomousCanaryLifecycle(AutonomousPolicyLifecycle):
    """Policy mode is separate from per-order HumanApproval; real mode remains closed."""

    def __init__(
        self,
        state,
        adapter,
        config,
        risk,
        run,
        fence,
        guard,
        facts,
        selector,
        clock,
        histories_reader,
        selection_facts_reader=None,
    ):
        if guard.validate()["phase"] != "CANARY":
            raise Halt("canary lifecycle requires CANARY grant")
        self.guard, self.facts, self.selector = guard, facts, selector
        self.histories_reader = histories_reader
        self.selection_facts_reader = selection_facts_reader or (lambda: self.facts)
        super().__init__(state, adapter, config, risk, run, fence, guard, clock, facts, selector)

    def prepare(self, intent, bar_time, normalized_signal=None):
        self.guard.validate()
        grant_id = self.guard.artifact["policy"]["grant_id"]
        if self.state.db.execute(
            "SELECT 1 FROM policy_decisions WHERE grant_id=? AND side=?", (grant_id, intent.side)
        ).fetchone():
            raise Halt("canary side budget already reserved; reconcile without replay")
        if intent.side == "buy":
            s = self.broker.snapshot(intent)
            selected, decisions = self.selector.select(
                s,
                self.config,
                self.risk,
                self.selection_facts_reader(),
                self.histories_reader(),
                self.clock(),
                s.nav,
            )
            if selected is None or selected != intent:
                raise Halt("canary must use the deterministic lowest eligible proposal")
            normalized_signal = {
                "infrastructure_canary": True,
                "selection": decisions,
                "excluded_from_strategy_performance": True,
            }
        elif intent.side == "sell":
            buy = self.state.db.execute(
                "SELECT key FROM policy_decisions WHERE grant_id=? AND side='buy'", (grant_id,)
            ).fetchone()
            if not buy:
                raise Halt("no prior tagged canary buy")
            row = self.state.db.execute(
                "SELECT * FROM intents WHERE key=?", (buy["key"],)
            ).fetchone()
            payload = json.loads(row["payload"])
            event = self.state.db.execute(
                "SELECT time FROM events WHERE kind='submission_started' AND json_extract(payload,'$.key')=? ORDER BY seq LIMIT 1",
                (buy["key"],),
            ).fetchone()
            if row["status"] != "filled" or payload["symbol"] != intent.symbol or not event:
                raise Halt("canary buy is not fully reconciled")
            buy_day = datetime.fromtimestamp(event["time"], ZoneInfo("America/New_York")).date()
            if datetime.fromtimestamp(self.clock(), ZoneInfo("America/New_York")).date() <= buy_day:
                raise Halt("canary exit requires a later regular session")
        else:
            raise Halt("invalid canary side")
        plan = super().prepare(intent, bar_time, normalized_signal)
        if plan["status"] == "approval_required":
            self.state.event(
                self.run,
                "canary_tag",
                {
                    "key": plan["key"],
                    "grant_id": grant_id,
                    "side": intent.side,
                    "exclude_from_strategy_performance": True,
                    "simulation": self.broker.is_simulation,
                },
            )
        return plan

    def execute(self, key, intent, artifact=None, verifier=None):
        if artifact is not None or verifier is not None:
            raise Halt("autonomous decisions accept only the enrolled policy, no order override")
        self.guard.validate()
        row = self.state.db.execute("SELECT status FROM intents WHERE key=?", (key,)).fetchone()
        if not row or row["status"] != "reviewed":
            raise Halt("canary decision not an unsubmitted reviewed plan")
        # Durable reservation BEFORE all submission I/O. Even a rejected/failed
        # attempt consumes this canary side; no second attempt is authorized.
        with self.state.db:
            self.state.db.execute(
                "INSERT INTO policy_decisions VALUES(?,?,?)",
                (self.guard.artifact["policy"]["grant_id"], intent.side, key),
            )
        return super().execute(key, intent)

    def prepare_buy(self, bar_time):
        snapshot = self.broker.snapshot()
        intent, decisions = self.selector.select(
            snapshot,
            self.config,
            self.risk,
            self.selection_facts_reader(),
            self.histories_reader(),
            self.clock(),
            snapshot.nav,
        )
        if intent is None:
            self.state.event(self.run, "canary_no_candidate", {"decisions": decisions})
            return {"status": "no_candidate", "decisions": decisions}
        return self.prepare(intent, bar_time), intent

    def prepare_sell(self, bar_time):
        self.guard.validate()
        buy = self.state.db.execute(
            "SELECT key FROM policy_decisions WHERE grant_id=? AND side='buy'",
            (self.guard.artifact["policy"]["grant_id"],),
        ).fetchone()
        if not buy:
            raise Halt("no prior tagged canary buy")
        row = self.state.db.execute(
            "SELECT payload FROM intents WHERE key=?", (buy["key"],)
        ).fetchone()
        symbol = json.loads(row["payload"])["symbol"]
        snapshot = self.broker.snapshot()
        intent = self.selector.close_intent(
            symbol, snapshot, self.config, self.risk, self.clock(), snapshot.nav
        )
        return self.prepare(intent, bar_time), intent

    def cancel(self, *args, **kwargs):
        raise Halt("canary policy does not authorize automatic cancellation")

    def reconcile(self, key, intent):
        result = super().reconcile(key, intent)
        self.state.event(
            self.run,
            "autonomous_canary_result",
            {
                **result,
                "side": intent.side,
                "simulation": self.broker.is_simulation,
                "excluded_from_strategy_performance": True,
            },
        )
        self.state.export_log()
        return result
