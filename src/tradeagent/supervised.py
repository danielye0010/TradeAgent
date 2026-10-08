"""Two-phase order review, submission, and reconciliation."""

import base64
import json
import subprocess
import tempfile
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .accounting import Accounting
from .broker import Broker, data, utc_time
from .model import Halt, dec, digest, timestamp_fresh
from .options import OptionIntent, check_option_order, normalize_order
from .risk import TERMINAL, check_order
from .schema import Contracts
from .state import dumps

# Enroll a HUMAN-owned offline/hardware signing key only during the future release.
# No signer, private key, enrollment CLI or real approval issuer exists in this project.
HUMAN_PUBLIC_KEY_SHA256 = None


def redact(value):
    if isinstance(value, dict):
        return {
            k: digest(v) if k in {"account_number", "rhs_account_number"} else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def state_binding(s, economic_only=False):
    # Observation timestamps change on refresh. Bind economic/permission state;
    # independently check ALL refreshed timestamps for freshness in risk.
    options = {}
    for key, q in s.option_quotes.items():
        contract = asdict(q.contract)
        contract.pop("observed_at")
        options[key] = {
            "contract": contract,
            "bid": q.bid,
            "ask": q.ask,
            "mark": q.mark,
            "bid_size": q.bid_size,
            "ask_size": q.ask_size,
            "volume": q.volume,
            "open_interest": q.open_interest,
        }
    return digest(
        {
            "account": s.account_key,
            "account_type": s.account_type,
            "eligible": s.agentic_eligible,
            "policy": s.account_policy,
            "option_level": s.option_level,
            "nav": None if economic_only else s.nav,
            "cash": s.cash,
            "buying_power": s.buying_power,
            "positions": s.positions,
            "available": s.available,
            "prices": {} if economic_only else s.prices,
            "asks": {} if economic_only else s.asks,
            "bids": {} if economic_only else s.bids,
            "orders": s.orders,
            "tradable": s.tradable,
            "options": s.options,
            "option_available": s.option_available,
            "option_basis": s.option_cost_basis,
            "quotes": options,
            "liquidity": {
                k: {f: v for f, v in book.items() if f != "asof"}
                for k, book in s.liquidity.items()
                if not economic_only
            },
            "regular_session": s.regular_session,
            "daily_turnover": s.daily_turnover,
        }
    )


class NativeExecutionTransport:
    is_simulation = False

    def __init__(self, bridge):
        self.bridge = bridge
        self.contracts = Contracts()

    def invoke(self, name, arguments):
        from .legacy.release import require_real_release

        require_real_release()  # Broker capability gate; no Config option overrides it.
        return self.bridge.execution_call(name, arguments)


class OfficialExecutionAdapter:
    """Production wire encoding/parsing also exercised by the simulated MCP transport."""

    def __init__(self, read_broker, transport, clock=time.time):
        self.read_broker, self.transport, self.clock = read_broker, transport, clock
        self.contracts = Contracts()

    @property
    def is_simulation(self):
        return self.transport.is_simulation is True

    @property
    def account_digest(self):
        return digest(self.read_broker.account["account_number"])

    def snapshot(self, intent=None):
        result = (
            self.read_broker.snapshot(clock=self.clock)
            if isinstance(self.read_broker, Broker)
            else self.read_broker.snapshot()
        )
        if isinstance(intent, OptionIntent) and intent.symbol not in result.option_quotes:
            from .options import OptionsReader

            q = OptionsReader(self.read_broker).contract_quote(intent.symbol, result.asof)
            result.option_quotes[intent.symbol] = q
        return result

    def arguments(self, intent, ref_id=None, review=False):
        asset = "option" if isinstance(intent, OptionIntent) else "equity"
        prefix = "review" if review else "place"
        name = f"{prefix}_{asset}_order"
        arguments = {
            "account_number": self.read_broker.account["account_number"],
            **intent.payload(),
        }
        if review and asset == "option":
            arguments.update(chain_symbol=intent.contract.underlying, underlying_type="equity")
        if not review and ref_id:
            arguments["ref_id"] = ref_id
        self.contracts.validate(name, arguments)
        return name, arguments

    def review(self, intent, ref_id):
        self.last_review = None
        name, args = self.arguments(intent, review=True)
        started = self.clock()
        response = self.transport.invoke(name, args)
        self.contracts.validate(name, response, "outputSchema")
        self.last_review = redact(response)
        reviewed = data(response)
        payload = intent.payload()
        echoed = (
            ("legs", "direction", "type", "quantity", "price", "time_in_force", "market_hours")
            if isinstance(intent, OptionIntent)
            else ("symbol", "side", "type", "quantity", "limit_price")
        )
        if any(reviewed.get(k) != payload[k] for k in echoed):
            raise Halt("broker review differs from exact requested payload")
        if (
            isinstance(intent, OptionIntent)
            and reviewed.get("account_number") != args["account_number"]
        ):
            raise Halt("review account mismatch")
        if reviewed.get("customer_approval_required") is True or (
            not self.is_simulation and reviewed.get("customer_approval_required") is not False
        ):
            raise Halt("broker customer approval required or uncertain; owner must resolve")
        checks = reviewed.get("order_checks")
        if not isinstance(checks, dict) or checks:
            raise Halt("broker review alerts require human investigation; no automated override")
        fees = (
            dec(reviewed.get("fees", {}).get("total_fee"))
            if isinstance(intent, OptionIntent)
            else dec(0)
        )
        if fees < 0:
            fees = dec(0)  # Never spend an anticipated discount.
        # Option review requests include chain/underlying specifically so fees are provided.
        result = {
            "payload": redact(args),
            "response": redact(response),
            "asof": started,
            "checks_passed": True,
            "fees": str(fees),
            "ref_id": ref_id,
        }
        _, place_args = self.arguments(intent, ref_id)
        result["submission_payload"] = redact(place_args)
        result["submission_hash"] = digest(place_args)
        return result

    def submit(self, intent, ref_id):
        self.last_submission = None
        name, args = self.arguments(intent, ref_id)
        response = self.transport.invoke(name, args)
        self.last_submission = redact(response)
        self.contracts.validate(name, response, "outputSchema")
        if not isinstance(intent, OptionIntent):
            guide = str(response.get("guide", "")).lower()
            if any(
                marker in guide
                for marker in (
                    "approval required",
                    "approval_required",
                    "requires approval",
                    "requires your approval",
                    "awaiting approval",
                    "pending approval",
                    "manually place",
                )
            ):
                raise Halt("broker approval required; no automatic retry")
        if data(response).get("approval") is not None:
            raise Halt("broker created a customer approval request; reconcile without resubmission")
        raw = data(response).get("order")
        if not isinstance(raw, dict):
            raise Halt("submission returned no order; ambiguous approval/acknowledgment")
        order = normalize_order(raw, "option" if isinstance(intent, OptionIntent) else "equity")
        if not isinstance(intent, OptionIntent) and (
            order["state"]
            not in TERMINAL | {"queued", "unconfirmed", "confirmed", "partially_filled"}
            or raw.get("time_in_force") != intent.payload()["time_in_force"]
            or raw.get("market_hours") != intent.payload()["market_hours"]
        ):
            raise Halt("unrecognized or mismatched broker acknowledgment")
        if (
            order["symbol"] != intent.symbol
            or order["side"] != intent.side
            or dec(order["quantity"]) != intent.quantity
            or dec(order["price"]) != intent.limit_price
            or (not isinstance(intent, OptionIntent) and order.get("ref_id") != ref_id)
        ):
            raise Halt("broker acknowledgment differs from approved order")
        return order

    def cancel(self, order_id, asset="equity"):
        # A caller cannot use this to bypass supervision: lifecycle.cancel below
        # requires a separate exact approval. Native transport is independently locked.
        if asset not in {"equity", "option"} or not isinstance(order_id, str) or not order_id:
            raise Halt("invalid cancel identity")
        name = f"cancel_{asset}_order"
        args = {"account_number": self.read_broker.account["account_number"], "order_id": order_id}
        self.contracts.validate(name, args)
        response = self.transport.invoke(name, args)
        self.contracts.validate(name, response, "outputSchema")
        if data(response).get("accepted") is not True:
            raise Halt("broker cancellation rejected; order remains unresolved")
        return response


class HumanApproval:
    """Verification only. A signed JSON artifact must be created by the human outside Codex."""

    def __init__(self, public_key_path=None):
        self.public_key_path = Path(public_key_path) if public_key_path else None

    def verify(self, artifact, binding, simulation=False):
        if not isinstance(artifact, dict) or artifact.get("binding") != binding:
            raise Halt("exact durable human approval artifact required")
        if simulation:
            if (
                artifact.get("simulation") is not True
                or set(artifact) != {"binding", "simulation", "actor"}
                or artifact.get("actor") != "SIMULATED_HUMAN"
            ):
                raise Halt("invalid simulated human approval")
            return
        if (
            artifact.get("simulation") is not False
            or not self.public_key_path
            or HUMAN_PUBLIC_KEY_SHA256 is None
        ):
            raise Halt("human signature enrollment/release required; agent cannot self-approve")
        import hashlib

        if hashlib.sha256(self.public_key_path.read_bytes()).hexdigest() != HUMAN_PUBLIC_KEY_SHA256:
            raise Halt("human approval public-key pin mismatch")
        try:
            signature = base64.b64decode(artifact["signature"], validate=True)
            with tempfile.TemporaryDirectory() as directory:
                message = Path(directory) / "message"
                sig = Path(directory) / "signature"
                message.write_bytes(dumps(binding).encode())
                sig.write_bytes(signature)
                result = subprocess.run(
                    [
                        "openssl",
                        "pkeyutl",
                        "-verify",
                        "-pubin",
                        "-inkey",
                        str(self.public_key_path),
                        "-rawin",
                        "-in",
                        str(message),
                        "-sigfile",
                        str(sig),
                    ],
                    capture_output=True,
                    check=False,
                )
                if result.returncode:
                    raise Halt("invalid human approval signature")
        except (KeyError, ValueError, OSError) as exc:
            raise Halt("malformed human approval signature") from exc


class SupervisedLifecycle:
    """Two-phase plans survive approval pauses; uncertain submissions NEVER retry."""

    def __init__(self, state, broker, config, risk, run_id, fence, clock=time.time):
        config.validate()
        risk.validate()
        if config.mode == "LIVE":
            from .execution_policy import StandingLifecycle

            if type(self) is not StandingLifecycle or self.guard.simulation is not False:
                raise Halt("LIVE lifecycle requires owner standing authorization")
        elif config.mode != "SUPERVISED" or config.supervised_enabled is not True:
            raise Halt("supervised lifecycle requires explicit mode")
        self.state, self.broker, self.config, self.risk, self.run, self.fence, self.clock = (
            state,
            broker,
            config,
            risk,
            run_id,
            fence,
            clock,
        )
        state.db.executescript("""
        CREATE TABLE IF NOT EXISTS plans(key TEXT PRIMARY KEY REFERENCES intents(key), packet TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals(key TEXT PRIMARY KEY REFERENCES intents(key), artifact TEXT NOT NULL,
            consumed INTEGER NOT NULL DEFAULT 0);
        """)

    def _state_binding(self, snapshot):
        return state_binding(snapshot)

    def _risk(self, intent, snapshot, baseline, fees=0):
        try:
            if isinstance(intent, OptionIntent):
                check_option_order(
                    intent, snapshot, self.config, self.risk, self.clock(), baseline, fees
                )
            else:
                check_order(intent, snapshot, self.config, self.risk, self.clock(), baseline)
        except Halt as exc:
            self.state.event(
                self.run, "risk_rejected", {"payload": intent.payload(), "reason": str(exc)}
            )
            raise

    def _review_market(self, intent, review, snapshot):
        body = review["response"]["data"]
        if isinstance(intent, OptionIntent):
            quotes = body.get("option_quotes") or []
            if len(quotes) != 1 or quotes[0].get("instrument_id") != intent.symbol:
                raise Halt("broker review option quote is incomplete/ambiguous")
            quote = quotes[0]
            current = snapshot.option_quotes[intent.symbol]
            observed = utc_time(quote.get("updated_at"))
            expected = {
                "bid_price": current.bid,
                "ask_price": current.ask,
                "mark_price": current.mark,
            }
            timestamps = [observed]
        else:
            quote = body.get("quote_data")
            if (
                not isinstance(quote, dict)
                or quote.get("symbol") != intent.symbol
                or quote.get("state") != "active"
                or quote.get("has_traded") is not True
            ):
                raise Halt("broker review equity quote is missing/ineligible")
            expected = {
                "bid_price": snapshot.bids[intent.symbol],
                "ask_price": snapshot.asks[intent.symbol],
            }
            timestamps = [
                utc_time(quote.get(k))
                for k in ("venue_bid_time", "venue_ask_time", "venue_last_trade_time")
            ]
        if any(
            not timestamp_fresh(t, self.clock(), self.risk.max_data_age_seconds) for t in timestamps
        ):
            raise Halt("stale broker review quote")
        if any(dec(quote.get(k)) != v for k, v in expected.items()):
            raise Halt("broker review and refreshed market data disagree")

    def prepare(self, intent, bar_time, normalized_signal=None):
        initial = self.broker.snapshot(intent)
        day = datetime.fromtimestamp(self.clock(), ZoneInfo("America/New_York")).date().isoformat()
        baseline = Accounting(self.state).observe(self.run, initial, day)
        self._risk(intent, initial, baseline)
        key = digest(
            [
                self.broker.account_digest,
                self.config.strategy_version,
                intent.asset,
                intent.symbol,
                intent.side,
                bar_time,
            ]
        )
        prior = self.state.db.execute("SELECT * FROM intents WHERE key=?", (key,)).fetchone()
        if prior:
            self.state.event(
                self.run, "duplicate_suppressed", {"key": key, "status": prior["status"]}
            )
            return {"key": key, "status": "duplicate_suppressed"}
        # Startup recovery only before a NEW decision; execute() deliberately preserves
        # the current reviewed plan across the separate human approval process.
        self.state.recover(self.run, initial)
        ref = self.state.prepare(key, self.run, intent)
        self.state.event(self.run, "strategy_signal", normalized_signal or {"bar_time": bar_time})
        self.state.event(
            self.run,
            "proposal",
            {
                "key": key,
                "ref_id": ref,
                "account_digest": self.broker.account_digest,
                "payload": intent.payload(),
                "simulation": self.broker.is_simulation,
            },
        )
        self.state.event(
            self.run, "risk_pass", {"key": key, "phase": "before_review", "baseline": baseline}
        )
        self.fence()
        try:
            review = self.broker.review(intent, ref)
            self._risk(intent, initial, baseline, review["fees"])
            fresh = self.broker.snapshot(intent)
            self._risk(intent, fresh, baseline, review["fees"])
            self._review_market(intent, review, fresh)
            if self._state_binding(initial) != self._state_binding(fresh):
                raise Halt("account/market changed during broker review")
        except Exception as exc:
            self.state.update(key, "abandoned")
            self.state.event(
                self.run,
                "review_failed",
                {
                    "key": key,
                    "class": type(exc).__name__,
                    "review": getattr(self.broker, "last_review", None),
                },
            )
            raise
        now = self.clock()
        binding = {
            "key": key,
            "account_digest": self.broker.account_digest,
            "payload": intent.payload(),
            "submission_hash": review["submission_hash"],
            "ref_id": ref,
            "strategy_version": self.config.strategy_version,
            "config": asdict(self.config),
            "risk": asdict(self.risk),
            "schema_hash": self.broker.contracts.hash,
            "review_hash": digest(review),
            "state_hash": self._state_binding(fresh),
            "expires": now + 30,
            "simulation": self.broker.is_simulation,
            "instrument": asdict(intent.contract)
            if isinstance(intent, OptionIntent)
            else intent.symbol,
        }
        packet = {
            "binding": binding,
            "review": review,
            "baseline": baseline,
            "initial": asdict(initial),
        }
        with self.state.db:
            self.state.db.execute("INSERT INTO plans VALUES(?,?)", (key, dumps(packet)))
        self.state.update(key, "reviewed")
        self.state.event(self.run, "broker_review", {"key": key, **review})
        self.state.event(self.run, "approval_required", binding)
        return {"key": key, "status": "approval_required", "binding": json.loads(dumps(binding))}

    def execute(self, key, intent, artifact, verifier=None):
        row = self.state.db.execute("SELECT * FROM intents WHERE key=?", (key,)).fetchone()
        plan = self.state.db.execute("SELECT packet FROM plans WHERE key=?", (key,)).fetchone()
        if not row or not plan or row["status"] != "reviewed":
            raise Halt("plan is not an unsubmitted reviewed decision")
        packet = json.loads(plan["packet"])
        binding = packet["binding"]
        (verifier or HumanApproval()).verify(artifact, binding, self.broker.is_simulation)
        if (
            not timestamp_fresh(packet["review"]["asof"], self.clock(), 30)
            or self.clock() > binding["expires"]
        ):
            self.state.update(key, "abandoned")
            raise Halt("approval/review expired")
        _, args = self.broker.arguments(intent, row["ref_id"])
        if (
            digest(args) != binding["submission_hash"]
            or redact(args) != packet["review"]["submission_payload"]
            or digest(intent.payload()) != digest(binding["payload"])
            or self.broker.account_digest != binding["account_digest"]
            or digest(asdict(self.config)) != digest(binding["config"])
            or digest(asdict(self.risk)) != digest(binding["risk"])
            or self.broker.contracts.hash != binding["schema_hash"]
            or self.broker.is_simulation != binding["simulation"]
            or digest(packet["review"]) != binding["review_hash"]
        ):
            self.state.update(key, "abandoned")
            raise Halt("approved payload/account/config/review/schema changed")
        self.fence()
        try:
            fresh = self.broker.snapshot(intent)
            self._risk(intent, fresh, packet["baseline"], packet["review"]["fees"])
            if self._state_binding(fresh) != binding["state_hash"]:
                raise Halt("account/market changed after approval; approval invalidated")
        except Exception:
            self.state.update(key, "abandoned")
            raise
        self.state.event(self.run, "risk_pass", {"key": key, "phase": "after_approval_refresh"})
        self.fence()
        if self.clock() > binding["expires"]:
            raise Halt("approval expired before submit")
        # Approval consumption + submitting marker committed in ONE transaction
        # BEFORE network send. A crash at any later point is reconciled, not replayed.
        with self.state.db:
            self.state.db.execute("INSERT INTO approvals VALUES(?,?,1)", (key, dumps(artifact)))
            self.state.db.execute(
                "UPDATE intents SET status='submitting',updated=? WHERE key=? AND status='reviewed'",
                (self.clock(), key),
            )
        self.state.event(
            self.run,
            "human_approval",
            {"key": key, "simulation": self.broker.is_simulation, "binding_hash": digest(binding)},
        )
        self.state.event(self.run, "submission_started", {"key": key, "ref_id": row["ref_id"]})
        try:
            order = self.broker.submit(intent, row["ref_id"])
        except Exception as exc:
            self.state.update(key, "unknown")
            self.state.event(
                self.run,
                "ambiguous_submission",
                {
                    "key": key,
                    "class": type(exc).__name__,
                    "retry": False,
                    "response": getattr(self.broker, "last_submission", None),
                },
            )
            raise Halt("submission outcome unknown; reconcile without replay") from exc
        self.state.update(key, "pending", order["id"])
        self.state.event(self.run, "broker_acknowledgment", order)
        return self.reconcile(key, intent)

    def reconcile(self, key, intent):
        row = self.state.db.execute("SELECT * FROM intents WHERE key=?", (key,)).fetchone()
        snapshot = self.broker.snapshot(intent)
        matches = [
            o
            for o in snapshot.orders
            if (row["broker_id"] and o["id"] == row["broker_id"])
            or (not isinstance(intent, OptionIntent) and o.get("ref_id") == row["ref_id"])
        ]
        if len(matches) != 1:
            raise Halt("ambiguous broker identity; no automatic retry")
        order = matches[0]
        if any(o["id"] != order["id"] and o.get("state") not in TERMINAL for o in snapshot.orders):
            raise Halt("conflicting/unknown pending broker order during final reconciliation")
        if (
            order["symbol"] != intent.symbol
            or order["side"] != intent.side
            or dec(order["quantity"]) != intent.quantity
            or dec(order["price"]) != intent.limit_price
        ):
            raise Halt("reconciled order differs from persisted intent")
        cumulative = dec(order.get("cumulative_quantity"))
        if not 0 <= cumulative <= intent.quantity:
            raise Halt("invalid cumulative fill quantity")
        packet = json.loads(
            self.state.db.execute("SELECT packet FROM plans WHERE key=?", (key,)).fetchone()[0]
        )
        initial = packet["initial"]
        multiplier = intent.contract.multiplier if isinstance(intent, OptionIntent) else dec(1)
        fills = order.get("executions") or []
        if sum((dec(f["quantity"]) for f in fills), dec(0)) != cumulative:
            raise Halt("fill detail does not match cumulative quantity")
        original = dec(
            initial["options" if isinstance(intent, OptionIntent) else "positions"].get(
                intent.symbol, 0
            )
        )
        held = dec(
            (snapshot.options if isinstance(intent, OptionIntent) else snapshot.positions).get(
                intent.symbol, 0
            )
        )
        expected = original + cumulative * (1 if intent.side == "buy" else -1)
        spent = sum((dec(f["quantity"]) * dec(f["price"]) * multiplier for f in fills), dec(0))
        if order.get("fees") is None:
            raise Halt("actual broker execution fees unavailable; cannot prove cash reconciliation")
        fees = dec(order["fees"])
        cash = dec(initial["cash"]) + spent * (1 if intent.side == "sell" else -1) - fees
        if held != expected or abs(snapshot.cash - cash) > dec(".01"):
            raise Halt("fills/positions/cash do not reconcile; operator action required")
        # Also verify all other holdings and aggregate NAV, not merely this symbol.
        positions = {k: dec(v) for k, v in initial["positions"].items()}
        options = {k: dec(v) for k, v in initial["options"].items()}
        expected_book = options if isinstance(intent, OptionIntent) else positions
        if expected:
            expected_book[intent.symbol] = expected
        else:
            expected_book.pop(intent.symbol, None)
        if snapshot.positions != positions or snapshot.options != options:
            raise Halt("unexplained non-order portfolio movement")
        marked = sum(q * snapshot.prices[k] for k, q in positions.items()) + sum(
            q * snapshot.option_quotes[k].mark * snapshot.option_quotes[k].contract.multiplier
            for k, q in options.items()
        )
        if abs(snapshot.nav - snapshot.cash - marked) > max(dec(".01"), snapshot.nav * dec(".01")):
            raise Halt("reconciled portfolio NAV mismatch")
        status = order["state"] if order["state"] in TERMINAL else "pending"
        if row["status"] in {"submitting", "unknown", "pending"}:
            self.state.update(key, status, order["id"])
        elif row["status"] != status:
            raise Halt("terminal broker state changed")
        self.state.event(
            self.run,
            "fill_reconciliation",
            {
                "key": key,
                "state": order["state"],
                "filled": str(cumulative),
                "remaining": str(intent.quantity - cumulative),
                "fees": str(fees),
                "executions": fills,
                "order": order,
                "positions": snapshot.positions,
                "options": snapshot.options,
                "cash": str(snapshot.cash),
                "nav": str(snapshot.nav),
                "decision": "matched",
                "simulation": self.broker.is_simulation,
            },
        )
        day = datetime.fromtimestamp(self.clock(), ZoneInfo("America/New_York")).date().isoformat()
        Accounting(self.state).observe(self.run, snapshot, day)
        self.state.export_log()
        return {
            "key": key,
            "status": status,
            "order_id": order["id"],
            "filled": str(cumulative),
            "remaining": str(intent.quantity - cumulative),
            "cash": str(snapshot.cash),
            "nav": str(snapshot.nav),
        }

    def cancel(self, key, intent, artifact, verifier=None):
        row = self.state.db.execute("SELECT * FROM intents WHERE key=?", (key,)).fetchone()
        if not row or row["status"] != "pending" or not row["broker_id"]:
            raise Halt("only a known pending order can be cancelled")
        binding = {
            "action": "cancel",
            "key": key,
            "order_id": row["broker_id"],
            "account_digest": self.broker.account_digest,
            "simulation": self.broker.is_simulation,
            "expires": artifact.get("binding", {}).get("expires")
            if isinstance(artifact, dict)
            else None,
        }
        expiry = binding["expires"]
        if (
            not isinstance(expiry, (int, float))
            or isinstance(expiry, bool)
            or not self.clock() < expiry <= self.clock() + 30
        ):
            raise Halt("invalid cancellation approval expiry")
        (verifier or HumanApproval()).verify(artifact, binding, self.broker.is_simulation)
        self.fence()
        self.state.event(self.run, "cancel_approval", binding)
        try:
            self.broker.cancel(
                row["broker_id"], "option" if isinstance(intent, OptionIntent) else "equity"
            )
        except Exception as exc:
            self.state.event(
                self.run, "cancel_unresolved", {"key": key, "class": type(exc).__name__}
            )
            raise Halt(
                "cancellation outcome unresolved; reconcile before any further action"
            ) from exc
        return self.reconcile(key, intent)
