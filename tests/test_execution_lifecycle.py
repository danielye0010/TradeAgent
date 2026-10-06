import base64
import copy
import hashlib
import json
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

import pytest

from tradebot.account_policy import POLICY, capital, eligible
from tradebot.accounting import Accounting
from tradebot.calendar import regular_session
from tradebot.codex_bridge import CodexBridge
from tradebot.model import Config, Halt, Intent, Risk, dec
from tradebot.options import OptionIntent, OptionsReader, check_option_order, normalize_order
from tradebot.risk import check_order, check_state
from tradebot.schema import Contracts
from tradebot.simulator import SimClock, SimulatedMCP, funded_snapshot
from tradebot.state import State
from tradebot.strategy import STATUS
from tradebot.strategy import TestTrendStrategy as TrendStrategy
from tradebot.supervised import (
    HumanApproval,
    NativeExecutionTransport,
    OfficialExecutionAdapter,
    SupervisedLifecycle,
)


def test_human_external_signature_verification_and_tamper(monkeypatch):
    # Ephemeral TEST key only, outside the repository and erased on exit.
    # Production has no signing method/key and remains independently locked.
    import tradebot.supervised as module
    from tradebot.state import dumps

    binding = {"key": "synthetic-signature-test", "simulation": False, "expires": 1791208830}
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        private, public, message, signature = (
            directory / name for name in ("private.pem", "public.pem", "message", "signature")
        )
        for command in (
            ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)],
            ["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
        ):
            subprocess.run(command, check=True, capture_output=True)
        message.write_bytes(dumps(binding).encode())
        subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-sign",
                "-inkey",
                str(private),
                "-rawin",
                "-in",
                str(message),
                "-out",
                str(signature),
            ],
            check=True,
            capture_output=True,
        )
        monkeypatch.setattr(
            module, "HUMAN_PUBLIC_KEY_SHA256", hashlib.sha256(public.read_bytes()).hexdigest()
        )
        artifact = {
            "binding": binding,
            "simulation": False,
            "signature": base64.b64encode(signature.read_bytes()).decode(),
        }
        verifier = HumanApproval(public)
        verifier.verify(artifact, binding, False)
        tampered = {**binding, "expires": 1791209999}
        with pytest.raises(Halt, match="signature"):
            verifier.verify({**artifact, "binding": tampered}, tampered, False)
        monkeypatch.setattr(module, "HUMAN_PUBLIC_KEY_SHA256", "wrong-key-pin")
        with pytest.raises(Halt, match="pin mismatch"):
            verifier.verify(artifact, binding, False)


def test_shadow_uses_same_risk_and_adapter_but_never_reviews_or_writes(tmp_path, monkeypatch):
    import time

    from tradebot.runner import cycle

    clock = SimClock()
    monkeypatch.setattr(time, "time", clock)
    sim = SimulatedMCP(tmp_path / "broker", clock)
    state = State(tmp_path / "agent")
    try:
        adapter = OfficialExecutionAdapter(sim, sim, clock)
        adapter.account_scope = [{"account_key": sim.snapshot().account_key, "synthetic": True}]
        adapter.histories = lambda: {
            k: [
                {"begins_at": "2026-10-02T13:30:00Z", "close_price": str(350 + 2 * i)}
                for i in range(220)
            ]
            for k in Config().allowed_symbols
        }
        adapter.reconcile = lambda initial: sim.snapshot()
        result = cycle(adapter, state, Config(), Risk())
        assert result["status"] == "halted"  # Three candidates exceed aggregate new exposure.
        assert any("new exposure" in item.get("reason", "") for item in result["risk"])
        assert result["proposals"]
        assert not sim.calls
        assert sim.db.execute("SELECT COUNT(*) FROM simulated_orders").fetchone()[0] == 0
    finally:
        state.close()
        sim.close()


def test_option_missing_actual_fees_cannot_claim_final_reconciliation(tmp_path):
    with harness(tmp_path) as (e, sim, state, clock):
        quote = next(iter(sim.snapshot().option_quotes.values()))
        intent = OptionIntent(quote.contract, "buy", dec(1), quote.ask)
        plan = e.prepare(intent, "bar")
        snapshot = sim.snapshot

        def no_fees():
            result = snapshot()
            for order in result.orders:
                order["fees"] = None
            return result

        sim.snapshot = no_fees
        with pytest.raises(Halt, match="fees unavailable"):
            e.execute(plan["key"], intent, approval(plan))
        assert state.db.execute("SELECT status FROM intents").fetchone()[0] == "pending"


def approval(plan):
    return {
        "binding": copy.deepcopy(plan["binding"]),
        "simulation": True,
        "actor": "SIMULATED_HUMAN",
    }


@contextmanager
def harness(path, scenario="full_fill", initial=None):
    clock = SimClock()
    sim = SimulatedMCP(path / "broker", clock, scenario, initial)
    state = State(path / "agent")
    config = Config(mode="SUPERVISED", supervised_enabled=True)
    risk = Risk()
    adapter = OfficialExecutionAdapter(sim, sim, clock)
    with state.lock(60) as fence:
        run = state.start(config, risk)
        engine = SupervisedLifecycle(state, adapter, config, risk, run, fence, clock)
        try:
            yield engine, sim, state, clock
        finally:
            state.finish(run, "simulation_test")
            state.export_log()
    state.close()
    sim.close()


def equity(quantity=1):
    return Intent("SPY", "buy", dec(quantity), dec("769.65"))


def test_equity_full_lifecycle_duplicate_and_durable_artifact(tmp_path):
    with harness(tmp_path) as (e, sim, state, clock):
        snapshot = sim.snapshot()
        histories = {
            k: [
                {"begins_at": "2026-10-02T13:30:00Z", "close_price": str(350 + 2 * i)}
                for i in range(220)
            ]
            for k in e.config.allowed_symbols
        }
        strategy = TrendStrategy()
        signal = strategy.signals(histories, e.config)["SPY"]
        assert signal.status == STATUS and signal.direction == "long"
        intent = strategy.proposal(signal, snapshot, e.config)
        plan = e.prepare(intent, signal.bar_time, asdict(signal))
        assert plan["status"] == "approval_required"
        assert state.db.execute("SELECT status FROM intents").fetchone()[0] == "reviewed"
        result = e.execute(plan["key"], intent, approval(plan))
        assert result["status"] == "filled" and result["cash"] == "24230.35"
        assert sim.snapshot().positions == {"SPY": dec(1)}
        assert result["nav"] == "24999.95"
        second = e.prepare(intent, signal.bar_time, asdict(signal))
        assert second["status"] == "duplicate_suppressed"
        assert sim.db.execute("SELECT COUNT(*) FROM simulated_orders").fetchone()[0] == 1
        assert (
            state.db.execute("SELECT COUNT(*) FROM approvals WHERE consumed=1").fetchone()[0] == 1
        )
        kinds = [r[0] for r in state.db.execute("SELECT kind FROM events")]
        for kind in (
            "proposal",
            "risk_pass",
            "broker_review",
            "approval_required",
            "human_approval",
            "submission_started",
            "broker_acknowledgment",
            "fill_reconciliation",
            "duplicate_suppressed",
        ):
            assert kind in kinds
        with pytest.raises(Halt, match="unsubmitted"):
            e.execute(plan["key"], intent, approval(plan))


def test_reviewed_plan_resumes_after_human_pause_and_restart(tmp_path):
    with harness(tmp_path, "accepted") as (e, sim, state, clock):
        intent = equity()
        plan = e.prepare(intent, "bar")
        artifact = approval(plan)
    with harness(tmp_path, "full_fill") as (e, sim, state, clock):
        assert e.execute(plan["key"], intent, artifact)["status"] == "filled"
        assert state.db.execute("SELECT consumed FROM approvals").fetchone()[0] == 1


def test_crash_after_ack_before_local_persistence_is_recovered(tmp_path):
    with harness(tmp_path, "full_fill") as (e, sim, state, clock):
        intent = equity()
        plan = e.prepare(intent, "bar")
        update = state.update

        def crash(key, status, broker_id=None):
            if status == "pending":
                raise SystemExit("simulated crash after ACK before local persistence")
            return update(key, status, broker_id)

        state.update = crash
        with pytest.raises(SystemExit):
            e.execute(plan["key"], intent, approval(plan))
        assert state.db.execute("SELECT status FROM intents").fetchone()[0] == "submitting"
    with harness(tmp_path, "accepted") as (e, sim, state, clock):
        assert e.reconcile(plan["key"], intent)["status"] == "filled"
        assert e.prepare(intent, "bar")["status"] == "duplicate_suppressed"
        assert not sim.calls


def test_official_option_reader_blank_underlying_resolves_by_search_only():
    clock = SimClock()
    option = next(iter(funded_snapshot(clock).option_quotes.values()))
    cid, chain = option.contract.identifier, option.contract.chain_id
    instrument_id = "8f92e76f-1e0e-4478-8580-16a6ffcfaef5"
    calls = []

    class Read:
        def read(self, name, args):
            calls.append(name)
            if name == "get_option_instruments":
                return {
                    "data": {
                        "instruments": [
                            {
                                "id": cid,
                                "chain_id": chain,
                                "chain_symbol": "SPY",
                                "underlying_type": "equity",
                                "type": "call",
                                "state": "active",
                                "tradability": "tradable",
                                "strike_price": "775",
                                "expiration_date": "2026-11-20",
                                "trade_value_multiplier": "100",
                                "min_ticks": {
                                    "above_tick": ".01",
                                    "below_tick": ".01",
                                    "cutoff_price": "0",
                                },
                            }
                        ]
                    }
                }
            if name == "get_option_chains":
                return {
                    "data": {
                        "chains": [
                            {
                                "id": chain,
                                "symbol": "SPY",
                                "can_open_position": True,
                                "cash_component": None,
                                "expiration_dates": ["2026-11-20"],
                                "trade_value_multiplier": "100",
                                "underlying_instruments": [
                                    {
                                        "symbol": "",
                                        "instrument": f"http://internal.invalid/instruments/{instrument_id}/",
                                    }
                                ],
                            }
                        ]
                    }
                }
            if name == "search":
                return {"data": {"results": [{"symbol": "SPY", "instrument_id": instrument_id}]}}
            assert name == "get_option_quotes"
            return {
                "data": {
                    "results": [
                        {
                            "quote": {
                                "instrument_id": cid,
                                "bid_price": ".98",
                                "ask_price": "1.00",
                                "mark_price": ".99",
                                "updated_at": clock.iso(),
                                "bid_size": 100,
                                "ask_size": 100,
                                "volume": 1200,
                                "open_interest": 3500,
                            }
                        }
                    ]
                }
            }

    class Broker:
        bridge = Read()

    normalized = OptionsReader(Broker()).contract_quote(cid, clock())
    assert normalized.contract.tick_cutoff == 0
    assert normalized.contract.underlying == "SPY"
    assert calls == ["get_option_instruments", "get_option_chains", "search", "get_option_quotes"]


@pytest.mark.parametrize(
    "scenario,terminal",
    [("accepted", "pending"), ("rejected", "rejected"), ("delayed_ack", "pending")],
)
def test_ack_is_not_fill(tmp_path, scenario, terminal):
    with harness(tmp_path, scenario) as (e, sim, state, clock):
        i = equity()
        p = e.prepare(i, "bar")
        result = e.execute(p["key"], i, approval(p))
        assert result["status"] == terminal
        assert result["filled"] == "0"
        assert sim.snapshot().positions == {}


@pytest.mark.parametrize(
    "scenario", ["lost_ack", "timeout_after_acceptance", "crash_after_acceptance"]
)
def test_restart_finds_accepted_order_without_replay(tmp_path, scenario):
    with harness(tmp_path, scenario) as (e, sim, state, clock):
        i = equity()
        p = e.prepare(i, "bar")
        expected = SystemExit if scenario == "crash_after_acceptance" else Halt
        with pytest.raises(expected):
            e.execute(p["key"], i, approval(p))
        assert state.db.execute("SELECT status FROM intents").fetchone()[0] in {
            "unknown",
            "submitting",
        }
        identity = sim.db.execute("SELECT id FROM simulated_orders").fetchone()[0]
        key = p["key"]
    # BOTH databases reopened; broker acceptance persists independently of local ACK.
    with harness(tmp_path, "accepted") as (e, sim, state, clock):
        with pytest.raises(Halt, match="pending"):
            state.recover(e.run, sim.snapshot())
        sim.fill(identity, 1)
        result = e.reconcile(key, i)
        assert result["status"] == "filled"
        assert e.prepare(i, "bar")["status"] == "duplicate_suppressed"
        assert sim.db.execute("SELECT COUNT(*) FROM simulated_orders").fetchone()[0] == 1
        assert not sim.calls


@pytest.mark.parametrize("scenario", ["timeout_before_ack", "crash_during_submission"])
def test_before_ack_absence_halts_and_never_retries(tmp_path, scenario):
    with harness(tmp_path, scenario) as (e, sim, state, clock):
        i = equity()
        p = e.prepare(i, "bar")
        with pytest.raises(SystemExit if scenario.startswith("crash") else Halt):
            e.execute(p["key"], i, approval(p))
        assert sim.db.execute("SELECT COUNT(*) FROM simulated_orders").fetchone()[0] == 0
        with pytest.raises(Halt, match="ambiguous"):
            state.recover(e.run, sim.snapshot())
        with pytest.raises(Halt, match="unsubmitted"):
            e.execute(p["key"], i, approval(p))
        assert len([x for x in sim.calls if x["tool"].startswith("place")]) == 1


def test_partial_fill_cancel_remainder(tmp_path):
    with harness(tmp_path, "partial_fill") as (e, sim, state, clock):
        i = equity(2)
        p = e.prepare(i, "bar")
        result = e.execute(p["key"], i, approval(p))
        assert (
            result["status"] == "pending" and result["filled"] == "1" and result["remaining"] == "1"
        )
        assert sim.snapshot().positions["SPY"] == 1
        binding = {
            "action": "cancel",
            "key": p["key"],
            "order_id": result["order_id"],
            "account_digest": e.broker.account_digest,
            "simulation": True,
            "expires": clock() + 20,
        }
        result = e.cancel(
            p["key"], i, {"binding": binding, "simulation": True, "actor": "SIMULATED_HUMAN"}
        )
        assert result["status"] == "partially_filled_rest_cancelled"
        assert sim.snapshot().positions["SPY"] == 1


def test_partial_then_full_fill_reconciliation(tmp_path):
    with harness(tmp_path, "partial_fill") as (e, sim, state, clock):
        i = equity(2)
        p = e.prepare(i, "bar")
        r = e.execute(p["key"], i, approval(p))
        clock.advance(5)
        sim.fill(r["order_id"], 2)
        result = e.reconcile(p["key"], i)
        assert result["status"] == "filled" and result["filled"] == "2"
        assert len(sim.snapshot().fills) == 2


@pytest.mark.parametrize(
    "scenario,expected", [("accepted", "cancelled"), ("cancel_rejected", "pending")]
)
def test_cancel_accepted_and_rejected(tmp_path, scenario, expected):
    with harness(tmp_path, scenario) as (e, sim, state, clock):
        i = equity()
        p = e.prepare(i, "bar")
        r = e.execute(p["key"], i, approval(p))
        binding = {
            "action": "cancel",
            "key": p["key"],
            "order_id": r["order_id"],
            "account_digest": e.broker.account_digest,
            "simulation": True,
            "expires": clock() + 20,
        }
        artifact = {"binding": binding, "simulation": True, "actor": "SIMULATED_HUMAN"}
        if scenario == "cancel_rejected":
            with pytest.raises(Halt, match="unresolved"):
                e.cancel(p["key"], i, artifact)
        else:
            assert e.cancel(p["key"], i, artifact)["status"] == expected
        assert state.db.execute("SELECT status FROM intents").fetchone()[0] == expected


@pytest.mark.parametrize(
    "change",
    ["cash", "price", "open_order", "account_type", "permission", "config", "risk", "payload"],
)
def test_changed_state_or_payload_invalidates_approval(tmp_path, change):
    with harness(tmp_path) as (e, sim, state, clock):
        i = equity()
        p = e.prepare(i, "bar")
        if change == "cash":
            sim.initial.cash += 1
        elif change == "price":
            sim.initial.prices["SPY"] += 1
            sim.initial.asks["SPY"] += 1
            sim.initial.bids["SPY"] += 1
        elif change == "open_order":
            original = e.broker.snapshot

            def pending(intent=None):
                s = original(intent)
                s.orders.append({"id": "external", "state": "queued"})
                return s

            e.broker.snapshot = pending
        elif change == "account_type":
            sim.initial.account_type = "margin"
        elif change == "permission":
            sim.initial.agentic_eligible = False
        elif change == "config":
            e.config = replace(e.config, target_fraction=".04")
        elif change == "risk":
            e.risk = replace(e.risk, max_positions=4)
        else:
            i = replace(i, quantity=dec(2))
        with pytest.raises(Halt):
            e.execute(p["key"], i, approval(p))
        assert not any(x["tool"].startswith("place") for x in sim.calls)


def test_exact_approval_cannot_be_yes_or_self_signed_real(tmp_path):
    with harness(tmp_path) as (e, sim, state, clock):
        i = equity()
        p = e.prepare(i, "bar")
        for bad in ("yes", {}, {"binding": p["binding"], "simulation": True, "actor": "CODEX"}):
            with pytest.raises(Halt):
                e.execute(p["key"], i, bad)
        with pytest.raises(Halt, match="signature enrollment"):
            HumanApproval().verify(
                {"binding": p["binding"], "simulation": False}, p["binding"], False
            )
        assert not any(x["tool"].startswith("place") for x in sim.calls)


@pytest.mark.parametrize("advance", [31, 121])
def test_stale_approval_rejected(tmp_path, advance):
    with harness(tmp_path) as (e, sim, state, clock):
        i = equity()
        p = e.prepare(i, "bar")
        clock.advance(advance)
        with pytest.raises(Halt, match="expired"):
            e.execute(p["key"], i, approval(p))
        assert state.db.execute("SELECT status FROM intents").fetchone()[0] == "abandoned"


def test_stale_review_latency_cannot_reset_freshness(tmp_path):
    with harness(tmp_path, "stale_review") as (e, sim, state, clock):
        i = equity()
        p = e.prepare(i, "bar")
        with pytest.raises(Halt, match="expired"):
            e.execute(p["key"], i, approval(p))


def test_schema_and_version_drift_rejected():
    c = Contracts()
    c.check_current(c.tools, "1.6.2")
    for changed, version in [(c.tools, "9.0"), ({**c.tools, "place_equity_order": {}}, "1.6.2")]:
        with pytest.raises(Halt, match="drift"):
            c.check_current(changed, version)
    with pytest.raises(Halt, match="inputSchema"):
        c.validate(
            "place_equity_order",
            {
                "account_number": "synthetic",
                "symbol": "SPY",
                "side": "buy",
                "type": "limit",
                "unexpected": 1,
            },
        )


def test_both_real_capability_gates_independent():
    class NeverCalled:
        def execution_call(self, *args):
            pytest.fail("native broker capability bypassed")

    with pytest.raises(Halt, match="disabled"):
        NativeExecutionTransport(NeverCalled()).invoke("place_equity_order", {})
    bridge = CodexBridge(Path.cwd())
    for name in (
        "review_equity_order",
        "place_equity_order",
        "cancel_equity_order",
        "place_option_order",
    ):
        with pytest.raises(Halt, match="disabled"):
            bridge.execution_call(name, {})
    assert bridge.serial == 0 and not bridge.calls
    from tradebot.supervised_cli import main as supervised_main

    with pytest.raises(Halt, match="real SUPERVISED execution is disabled"):
        supervised_main(["submit", "--config", "does-not-exist"])


@pytest.mark.parametrize("change", ["alerts", "quote", "quantity"])
def test_real_review_contract_fails_closed_on_mismatch(tmp_path, change):
    with harness(tmp_path) as (e, sim, state, clock):
        invoke = sim.invoke

        def corrupt(name, args):
            response = invoke(name, args)
            if name == "review_equity_order":
                if change == "alerts":
                    response["data"]["order_checks"] = {"alert_type": "SYNTHETIC_ALERT"}
                elif change == "quote":
                    response["data"]["quote_data"]["ask_price"] = "799"
                else:
                    response["data"]["quantity"] = "2"
            return response

        sim.invoke = corrupt
        with pytest.raises(Halt):
            e.prepare(equity(), "bar")
        assert state.db.execute("SELECT status FROM intents").fetchone()[0] == "abandoned"
        assert (
            state.db.execute("SELECT COUNT(*) FROM events WHERE kind='review_failed'").fetchone()[0]
            == 1
        )
        assert not any(x["tool"].startswith("place") for x in sim.calls)


@pytest.mark.parametrize("kind", ["cash", "limited_margin", "margin", "unknown"])
def test_account_eligibility_model(kind):
    a = {
        "type": kind,
        "agentic_allowed": True,
        "brokerage_account_type": "individual",
        "state": "active",
        "deactivated": False,
        "permanently_deactivated": False,
    }
    if kind in {"cash", "limited_margin"}:
        assert eligible(a) == POLICY
    else:
        with pytest.raises(Halt):
            eligible(a)
    a["agentic_allowed"] = False
    with pytest.raises(Halt):
        eligible(a)


def test_capital_never_includes_margin_or_pending_funds():
    p = {
        "cash": "1000",
        "pending_deposits": "300",
        "currency": "USD",
        "buying_power": {
            "buying_power": "5000",
            "unleveraged_buying_power": "800",
            "display_currency": "USD",
        },
    }
    assert capital(p) == 700
    p["buying_power"]["unleveraged_buying_power"] = "-1"
    with pytest.raises(Halt):
        capital(p)


def test_cashflow_adjusted_baseline_and_unknown_movement_latch(tmp_path):
    with harness(tmp_path) as (e, sim, state, clock):
        a = Accounting(state)
        s = sim.snapshot()
        assert a.observe(e.run, s, "2026-10-05") == "25000"
        s.cash += 1000
        s.nav += 1000
        s.buying_power += 1000
        s.cashflows = [{"id": "synthetic-deposit", "kind": "deposit", "amount": "1000"}]
        assert a.observe(e.run, s, "2026-10-05") == "26000"
        assert s.high_water_nav == 26000
        s.cash -= 500
        s.nav -= 500
        s.cashflows.append({"id": "withdrawal", "kind": "withdrawal", "amount": "-500"})
        assert a.observe(e.run, s, "2026-10-05") == "25500"
        s.cash += 1
        s.nav += 1
        s.cashflows = None
        with pytest.raises(Halt, match="unexplained"):
            a.observe(e.run, s, "2026-10-05")
        with pytest.raises(Halt, match="latched"):
            a.observe(e.run, s, "2026-10-06")


def test_drawdown_and_liquidity_gates():
    clock = SimClock()
    s = funded_snapshot(clock)
    r = Risk()
    c = Config()
    s.high_water_nav = dec(30000)
    with pytest.raises(Halt, match="drawdown"):
        check_state(s, r, clock())
    s.high_water_nav = None
    s.liquidity["SPY"]["ask_size"] = 1
    with pytest.raises(Halt, match="liquidity"):
        check_order(equity(), s, c, r, clock(), 25000)


@pytest.mark.parametrize(
    "stamp,is_open",
    [
        ("2026-10-05T13:29:59Z", False),
        ("2026-10-05T13:30:00Z", True),
        ("2026-10-05T20:00:00Z", False),
        ("2026-10-03T15:00:00Z", False),
        ("2026-11-26T15:00:00Z", False),
        ("2026-11-27T17:59:59Z", True),
        ("2026-11-27T18:00:00Z", False),
        ("2026-12-25T15:00:00Z", False),
        ("2026-06-19T15:00:00Z", False),
        ("2026-07-03T15:00:00Z", False),
    ],
)
def test_market_calendar_holidays_weekends_earlyclose(stamp, is_open):
    assert (
        regular_session(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()) is is_open
    )


def test_single_long_option_complete_lifecycle_and_duplicate(tmp_path):
    with harness(tmp_path) as (e, sim, state, clock):
        q = next(iter(sim.snapshot().option_quotes.values()))
        i = OptionIntent(q.contract, "buy", dec(1), q.ask)
        p = e.prepare(i, "option-bar")
        r = e.execute(p["key"], i, approval(p))
        assert r["status"] == "filled" and r["cash"] == "24900.00" and r["nav"] == "24999.00"
        assert sim.snapshot().options[i.symbol] == 1
        assert sim.snapshot().option_cost_basis[i.symbol] == 100
        assert e.prepare(i, "option-bar")["status"] == "duplicate_suppressed"
        raw = json.loads(sim.db.execute("SELECT response FROM simulated_orders").fetchone()[0])[
            "data"
        ]["order"]
        assert "ref_id" not in raw  # Do not invent a reference omitted by actual schema.


@pytest.mark.parametrize(
    "change,reason",
    [
        ("short", "long premium"),
        ("level", "approval"),
        ("stale", "stale"),
        ("spread", "spread"),
        ("size", "liquidity"),
        ("dte", "DTE"),
        ("multiplier", "nonstandard"),
        ("premium", "premium-at-risk"),
        ("total", "total option"),
        ("positions", "position count"),
        ("tick", "increment"),
    ],
)
def test_option_deterministic_risk_gates(change, reason):
    clock = SimClock()
    s = funded_snapshot(clock)
    q = next(iter(s.option_quotes.values()))
    i = OptionIntent(q.contract, "buy", dec(1), q.ask)
    r = Risk()
    c = Config()
    if change == "short":
        i = replace(i, side="sell", effect="open")
    elif change == "level":
        s.option_level = ""
    elif change == "stale":
        s.option_quotes[i.symbol] = replace(q, asof=clock() - 121)
    elif change == "spread":
        s.option_quotes[i.symbol] = replace(q, bid=dec(".10"))
    elif change == "size":
        s.option_quotes[i.symbol] = replace(q, ask_size=0)
    elif change == "dte":
        i = replace(i, contract=replace(q.contract, expiration="2026-10-06"))
    elif change == "multiplier":
        i = replace(i, contract=replace(q.contract, multiplier=dec(150)))
    elif change == "premium":
        i = replace(i, quantity=dec(3))
    elif change == "total":
        s.option_cost_basis["existing"] = dec(700)
    elif change == "positions":
        r = replace(r, max_option_positions=1)
        s.options["other"] = 1
        s.option_quotes["other"] = q
        s.option_cost_basis["other"] = 100
        s.nav += 99
    elif change == "tick":
        i = replace(i, limit_price=dec("1.001"))
    with pytest.raises(Halt, match=reason):
        check_option_order(i, s, c, r, clock(), 25000)


def test_option_lost_ack_without_id_stays_ambiguous(tmp_path):
    with harness(tmp_path, "lost_ack") as (e, sim, state, clock):
        q = next(iter(sim.snapshot().option_quotes.values()))
        i = OptionIntent(q.contract, "buy", dec(1), q.ask)
        p = e.prepare(i, "option-bar")
        with pytest.raises(Halt, match="unknown"):
            e.execute(p["key"], i, approval(p))
        with pytest.raises(Halt, match="ambiguous"):
            e.reconcile(p["key"], i)
        with pytest.raises(Halt, match="unsubmitted"):
            e.execute(p["key"], i, approval(p))
        assert sim.db.execute("SELECT COUNT(*) FROM simulated_orders").fetchone()[0] == 1


def test_bad_broker_fill_is_not_invented_position(tmp_path):
    with harness(tmp_path) as (e, sim, state, clock):
        i = equity()
        p = e.prepare(i, "bar")
        original = sim.snapshot

        def wrong():
            s = original()
            if s.orders:
                s.positions = {}
            return s

        sim.snapshot = wrong
        with pytest.raises(Halt, match="reconcile"):
            e.execute(p["key"], i, approval(p))
        assert state.db.execute("SELECT status FROM intents").fetchone()[0] == "pending"


def test_simulator_idempotency_rejects_changed_ref_payload(tmp_path):
    with harness(tmp_path, "accepted") as (e, sim, state, clock):
        name, args = e.broker.arguments(equity(), "eb34d7cf-df61-407f-aade-ddcaf793d061")
        first = sim.invoke(name, args)
        assert sim.invoke(name, args) == first
        with pytest.raises(Halt, match="different payload"):
            sim.invoke(name, {**args, "quantity": "2"})
        assert sim.db.execute("SELECT COUNT(*) FROM simulated_orders").fetchone()[0] == 1


def test_normalizer_rejects_ambiguous_multileg_and_incomplete_fill():
    with pytest.raises(Halt, match="multileg"):
        normalize_order({"legs": []}, "option")
    with pytest.raises(Halt, match="incomplete"):
        normalize_order(
            {
                "id": "fake",
                "symbol": "SPY",
                "side": "buy",
                "type": "limit",
                "state": "filled",
                "quantity": "1",
                "cumulative_quantity": "0",
                "price": "100",
                "executions": [],
            }
        )


@pytest.mark.parametrize("asset", ["equity", "option"])
def test_held_long_closing_sell_reconciles_without_shorting(tmp_path, asset):
    with harness(tmp_path) as (e, sim, state, clock):
        quote = next(iter(sim.snapshot().option_quotes.values()))
        buy = (
            equity()
            if asset == "equity"
            else OptionIntent(quote.contract, "buy", dec(1), quote.ask)
        )
        plan = e.prepare(buy, "entry-bar")
        e.execute(plan["key"], buy, approval(plan))
        sell = (
            Intent("SPY", "sell", dec(1), dec("769.55"))
            if asset == "equity"
            else OptionIntent(quote.contract, "sell", dec(1), quote.bid, "close")
        )
        exit_plan = e.prepare(sell, "exit-bar")
        result = e.execute(exit_plan["key"], sell, approval(exit_plan))
        assert result["status"] == "filled"
        assert not sim.snapshot().positions and not sim.snapshot().options
        assert sim.db.execute("SELECT COUNT(*) FROM simulated_orders").fetchone()[0] == 2
        assert dec(result["cash"]) == (dec("24999.90") if asset == "equity" else dec("24998.00"))
