import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from test_broker_contract import NOW, RawReadFake

from tradeagent.autonomous import AutonomousCanaryLifecycle, AutonomousPolicyLifecycle
from tradeagent.broker import Broker
from tradeagent.canary import CanarySelector, InstrumentFacts
from tradeagent.health import health_report
from tradeagent.model import Config, Halt, Intent, Risk, dec, digest
from tradeagent.policy import (
    GRANT_VERSION,
    GrantContext,
    PolicyGuard,
    PolicyLimits,
    PolicySignature,
    deployment_hash,
    require_autonomous_release,
)
from tradeagent.simulator import SimClock, SimulatedMCP, funded_snapshot
from tradeagent.state import State, dumps
from tradeagent.supervised import OfficialExecutionAdapter


def tiny_snapshot(clock, symbols=("TINY",)):
    s = funded_snapshot(clock)
    s.nav = s.cash = s.buying_power = dec(100)
    s.prices = {k: dec("2.315") if k == "TINY" else dec("1.112") for k in symbols}
    s.bids = {k: dec("2.31") if k == "TINY" else dec("1.110") for k in symbols}
    s.asks = {k: dec("2.32") if k == "TINY" else dec("1.114") for k in symbols}
    s.quote_times = s.bid_times = s.ask_times = {k: clock() for k in symbols}
    s.tradable = {k: True for k in symbols}
    s.liquidity = {k: {"asof": clock(), "bid_size": 10000, "ask_size": 10000} for k in symbols}
    s.option_quotes = {}
    s.option_level = ""
    return s


def facts(clock, symbols=("TINY",)):
    return {
        k: InstrumentFacts(
            k,
            "synthetic-" + k,
            clock(),
            "XNYS",
            True,
            False,
            False,
            False,
            True,
            "SYNTHETIC_ONLY",
            "a" * 64,
        )
        for k in symbols
    }


def histories(clock, symbols=("TINY",)):
    today = datetime.fromtimestamp(clock(), timezone.utc)
    return {
        k: [
            {
                "begins_at": (today - timedelta(days=220 - i)).replace(hour=0).isoformat(),
                "close_price": "2.315",
            }
            for i in range(220)
        ]
        for k in symbols
    }


def artifact(clock, context, limits, phase="CANARY"):
    return {
        "simulation": True,
        "actor": "SIMULATED_POLICY_OWNER",
        "policy": {
            "version": GRANT_VERSION,
            "grant_id": "synthetic-one-time-deployment",
            "phase": phase,
            "issued_at": clock(),
            "expires_at": clock() + 172800,
            "context": asdict(context),
            "limits": asdict(limits),
            "asset_class": "equity",
            "long_only": True,
            "no_leverage": True,
            "allowed_session": "XNYS_REGULAR",
            "order_type": "limit",
            "time_in_force": "gfd",
            "max_canary_buys": 1 if phase == "CANARY" else 0,
            "max_canary_sells": 1 if phase == "CANARY" else 0,
        },
    }


@contextmanager
def harness(path, monkeypatch, scenario="full_fill", phase="CANARY", clock=None):
    clock = clock or SimClock()
    monkeypatch.setattr(time, "time", clock)
    sim = SimulatedMCP(path / "broker", clock, scenario, tiny_snapshot(clock))
    state = State(path / "agent")
    config = Config(
        mode="SUPERVISED",
        supervised_enabled=True,
        allowed_symbols=["TINY"],
        strategy_version="infrastructure-canary-v1",
    )
    risk, limits, selector = Risk(), PolicyLimits(), CanarySelector()
    fs = facts(clock)
    adapter = OfficialExecutionAdapter(sim, sim, clock)
    ctx = GrantContext(
        adapter.account_digest,
        "synthetic-v1",
        "b" * 64,
        config.strategy_version,
        "c" * 64,
        digest(asdict(config)),
        digest(asdict(risk)),
        adapter.contracts.hash,
        selector.hash,
        digest({k: asdict(v) for k, v in fs.items()}),
    )
    art = artifact(clock, ctx, limits, phase)
    guard = PolicyGuard(art, ctx, limits, state, config, risk, True, clock, lambda: ctx)
    with state.lock(60) as fence:
        run = state.start(config, risk)
        if phase == "CANARY":
            e = AutonomousCanaryLifecycle(
                state,
                adapter,
                config,
                risk,
                run,
                fence,
                guard,
                fs,
                selector,
                clock,
                lambda: histories(clock),
            )
        else:
            e = AutonomousPolicyLifecycle(
                state, adapter, config, risk, run, fence, guard, clock, fs, selector
            )
        try:
            yield e, sim, state, clock
        finally:
            state.finish(run, "simulation_only")
            state.export_log()
    state.close()
    sim.close()


def test_deterministic_lowest_notional_without_return_ranking():
    c = SimClock()
    symbols = ("TINY", "LESS")
    s = tiny_snapshot(c, symbols)
    cfg = Config(allowed_symbols=list(reversed(symbols)))
    selected, decisions = CanarySelector().select(
        s, cfg, Risk(), facts(c, symbols), histories(c, symbols), c(), dec(100)
    )
    assert selected == Intent("LESS", "buy", dec(1), dec("1.12"))
    assert [d["symbol"] for d in decisions] == ["LESS", "TINY"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("ordinary_common", None),
        ("adr", True),
        ("etf", True),
        ("leveraged_inverse", True),
        ("corporate_action_clear", None),
        ("exchange", "UNKNOWN"),
        ("asof", 0),
    ],
)
def test_unknown_or_excluded_instruments_never_selected(field, value):
    c = SimClock()
    fs = facts(c)
    fs["TINY"] = replace(fs["TINY"], **{field: value})
    i, ds = CanarySelector().select(
        tiny_snapshot(c), Config(allowed_symbols=["TINY"]), Risk(), fs, histories(c), c(), dec(100)
    )
    assert i is None and ds[0]["decision"] == "reject"


@pytest.mark.parametrize(
    "fault", ["stale", "future", "empty_book", "spread", "conflict", "depth", "history", "capital"]
)
def test_canary_frozen_market_and_account_gates(fault):
    c = SimClock()
    s, hs = tiny_snapshot(c), histories(c)
    if fault == "stale":
        s.quote_times["TINY"] -= 121
    if fault == "future":
        s.liquidity["TINY"]["asof"] += 0.251
    if fault == "empty_book":
        s.liquidity["TINY"]["ask_size"] = 0
    if fault == "spread":
        s.bids["TINY"] = dec(2)
    if fault == "conflict":
        s.orders = [{"state": "queued"}]
    if fault == "depth":
        s.liquidity["TINY"]["bid_size"] = 999
    if fault == "history":
        hs["TINY"] = []
    if fault == "capital":
        s.nav = s.cash = s.buying_power = dec(50)
    if fault == "conflict":
        with pytest.raises(Halt):
            CanarySelector().select(
                s, Config(allowed_symbols=["TINY"]), Risk(), facts(c), hs, c(), dec(100)
            )
    else:
        i, ds = CanarySelector().select(
            s, Config(allowed_symbols=["TINY"]), Risk(), facts(c), hs, c(), dec(100)
        )
        assert i is None and ds[0]["decision"] == "reject"


@pytest.mark.parametrize("field", list(GrantContext.__dataclass_fields__))
def test_every_context_binding_change_invalidates_grant(tmp_path, monkeypatch, field):
    with harness(tmp_path, monkeypatch) as (e, sim, _s, _c):
        changed = replace(e.guard.context, **{field: "changed"})
        e.guard.context_reader = lambda: changed
        with pytest.raises(Halt, match="outside grant"):
            e.prepare_buy("bar")
        assert sim.calls == []


@pytest.mark.parametrize(
    "fault", ["expired", "risk", "cash", "options", "account", "kill", "override", "schema"]
)
def test_policy_revalidation_before_place(tmp_path, monkeypatch, fault):
    with harness(tmp_path, monkeypatch) as (e, sim, _s, c):
        plan, intent = e.prepare_buy("bar")
        if fault == "expired":
            c.advance(172800)
        if fault == "risk":
            e.guard.risk = replace(e.risk, max_positions=4)
        if fault == "cash":
            sim.initial.cash -= 1
        if fault == "options":
            sim.initial.options = {"synthetic": dec(1)}
        if fault == "account":
            sim.initial.account_key = "another"
        if fault == "kill":
            e.guard.halt_entries("operator safety halt")
        if fault == "schema":
            e.broker.contracts.hash = "changed-schema-pin"
        with pytest.raises((Halt, KeyError)):
            e.execute(plan["key"], intent, artifact={} if fault == "override" else None)
        assert not any(x["tool"].startswith("place") for x in sim.calls)


def test_autonomous_simulated_buy_hold_sell_and_persistent_halt(tmp_path, monkeypatch):
    with harness(tmp_path, monkeypatch) as (e, sim, s, c):
        plan, intent = e.prepare_buy("buy-bar")
        buy = e.execute(plan["key"], intent)
        assert buy["status"] == "filled" and buy["cash"] == "97.68"
        assert sim.snapshot().positions == {"TINY": dec(1)}
        with pytest.raises(Halt, match="later regular session"):
            e.prepare_sell("same-day")
        with pytest.raises(Halt):
            e.prepare_buy("buy-again")
        e.guard.halt_entries("new entries disabled; keep exit/reconciliation")
        # Durable state is closed/reopened; no reset of policy quota or halt.
        s.db.close()
        s.db = sqlite3.connect(s.path)
        s.db.row_factory = sqlite3.Row
        c.advance(84600)  # Next regular session, within evidence's 24-hour cap.
        assert e.reconcile(plan["key"], intent)["status"] == "filled"
        sell_plan, sell_intent = e.prepare_sell("next-session-exit")
        sell = e.execute(sell_plan["key"], sell_intent)
        assert sell["status"] == "filled" and sell["cash"] == "99.99"
        assert (
            sim.snapshot().positions == {}
            and s.db.execute("SELECT COUNT(*) FROM policy_decisions").fetchone()[0] == 2
        )
        assert [x["tool"] for x in sim.calls].count("place_equity_order") == 2
        assert all(x["tool"] in {"review_equity_order", "place_equity_order"} for x in sim.calls)
        report = health_report(s.directory)
        assert report["emergency_halt"] and report["fees_evidence"] == "0"
        assert report["last_reconciliation"]["payload"]["positions"] == {}
        tags = s.db.execute("SELECT payload FROM events WHERE kind='canary_tag'").fetchall()
        assert len(tags) == 2 and all(
            json.loads(t[0])["exclude_from_strategy_performance"] for t in tags
        )


def test_limited_policy_is_not_per_order_human_approval(tmp_path, monkeypatch):
    with harness(tmp_path, monkeypatch, phase="LIMITED_EQUITY") as (e, sim, _s, _c):
        i = Intent("TINY", "buy", dec(1), dec("2.32"))
        p = e.prepare(i, "bar")
        assert e.execute(p["key"], i)["status"] == "filled"
        assert any(x["tool"] == "place_equity_order" for x in sim.calls)
        with pytest.raises(Halt):
            e.cancel()


@pytest.mark.parametrize(
    "scenario",
    [
        "lost_ack",
        "timeout_after_acceptance",
        "crash_after_acceptance",
        "timeout_before_ack",
        "crash_during_submission",
    ],
)
def test_autonomous_ambiguous_outcomes_never_replay(tmp_path, monkeypatch, scenario):
    with harness(tmp_path, monkeypatch, scenario) as (e, sim, s, _c):
        plan, i = e.prepare_buy("bar")
        with pytest.raises(SystemExit if scenario.startswith("crash") else Halt):
            e.execute(plan["key"], i)
        with pytest.raises(Halt):
            e.prepare_buy("new-bar")
        with pytest.raises(Halt):
            e.execute(plan["key"], i)
        assert [x["tool"] for x in sim.calls].count("place_equity_order") == 1
        assert s.db.execute("SELECT COUNT(*) FROM policy_decisions").fetchone()[0] == 1


@pytest.mark.parametrize(
    "scenario,expected",
    [("accepted", "pending"), ("rejected", "rejected"), ("delayed_ack", "pending")],
)
def test_autonomous_ack_not_fill_and_rejection_consumes_attempt(
    tmp_path, monkeypatch, scenario, expected
):
    with harness(tmp_path, monkeypatch, scenario) as (e, sim, _s, _c):
        plan, i = e.prepare_buy("bar")
        r = e.execute(plan["key"], i)
        assert r["status"] == expected and r["filled"] == "0"
        with pytest.raises(Halt):
            e.prepare_buy("again")
        assert [x["tool"] for x in sim.calls].count("place_equity_order") == 1


@pytest.mark.parametrize("fault", ["network unavailable", "MCP unavailable", "OAuth expired"])
def test_unavailable_read_halts_before_decision(tmp_path, monkeypatch, fault):
    with harness(tmp_path, monkeypatch) as (e, sim, s, _c):

        def unavailable(*args):
            raise Halt(fault)

        e.broker.snapshot = unavailable
        with pytest.raises(Halt, match=fault):
            e.prepare_buy("bar")
        assert not sim.calls and s.db.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 0


def test_real_policy_gate_before_database_mutation(tmp_path):
    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError("database touched before real gate")

    with pytest.raises(Halt, match="release closed"):
        PolicyGuard(None, None, None, NoDatabase(), None, None, False, None, None)
    with pytest.raises(Halt):
        require_autonomous_release()
    with pytest.raises(Halt):
        PolicySignature().verify({"policy": {}, "simulation": False, "signature": ""}, False)


def raw_filled_order(raw):
    row = {
        "id": "fake-order",
        "ref_id": "fake-ref",
        "symbol": "SPY",
        "side": "buy",
        "type": "limit",
        "state": "filled",
        "quantity": "1",
        "cumulative_quantity": "1",
        "price": "100.01",
        "fees": "0.01",
        "executions": [
            {
                "id": "fake-fill",
                "quantity": "1",
                "price": "100.01",
                "fees": "0.01",
                "timestamp": NOW.isoformat(),
            }
        ],
    }
    raw.payloads["get_equity_orders"]["orders"] = [row]
    raw.payloads["get_equity_positions"]["positions"] = [
        {
            "symbol": "SPY",
            "quantity": "1",
            "shares_available_for_sells": "1",
            "type": "long",
            "shares_pending_from_options_events": "0",
        }
    ]
    raw.payloads["get_portfolio"].update(cash="9899.98", equity_value="100", total_value="9999.98")
    return row


def test_official_read_normalizer_preserves_actual_fills_and_fees():
    raw = RawReadFake()
    row = raw_filled_order(raw)
    s = Broker(raw, Config(allowed_symbols=["SPY"]), Risk()).snapshot(NOW.timestamp())
    assert s.orders[0]["executions"] == row["executions"] and s.orders[0]["fees"] == "0.01"
    assert s.fills[0]["cash_delta"] == "-100.02" and s.cash == dec("9899.98")


@pytest.mark.parametrize("fault", ["missing_order", "missing_execution", "negative", "mismatch"])
def test_official_fee_ambiguity_fails_closed(fault):
    raw = RawReadFake()
    row = raw_filled_order(raw)
    if fault == "missing_order":
        row.pop("fees")
    if fault == "missing_execution":
        row["executions"][0].pop("fees")
    if fault == "negative":
        row["fees"] = row["executions"][0]["fees"] = "-0.01"
    if fault == "mismatch":
        row["fees"] = "0.02"
    with pytest.raises(Halt):
        Broker(raw, Config(allowed_symbols=["SPY"]), Risk()).snapshot(NOW.timestamp())


def test_deployment_freeze_tamper_and_untracked_source(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    p = tmp_path / "src/a.py"
    p.write_text("# pinned\n")
    files = {"src/a.py": hashlib.sha256(p.read_bytes()).hexdigest()}
    sha = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    (tmp_path / "docs/deployment_manifest.json").write_text(
        json.dumps({"files": files, "framework_sha256": sha})
    )
    assert deployment_hash(tmp_path) == sha
    (tmp_path / "src/b.py").write_text("# extra\n")
    with pytest.raises(Halt, match="unfrozen"):
        deployment_hash(tmp_path)
    (tmp_path / "src/b.py").unlink()
    p.write_text("# changed\n")
    with pytest.raises(Halt, match="changed"):
        deployment_hash(tmp_path)


def test_sqlite_interrupted_transaction_rolls_back(tmp_path):
    path = tmp_path / "interrupted.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE marker(value)")
    program = "import sqlite3,os,sys; d=sqlite3.connect(sys.argv[1]); d.execute('INSERT INTO marker VALUES(1)'); os._exit(91)"
    assert subprocess.run([sys.executable, "-c", program, str(path)]).returncode == 91
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT COUNT(*) FROM marker").fetchone()[0] == 0


def test_second_process_and_killed_process_preserve_lease(tmp_path):
    directory = tmp_path / "locked"
    program = "from pathlib import Path; from tradeagent.state import State; import sys,time; s=State(Path(sys.argv[1]));\nwith s.lock(60):\n print('LOCKED',flush=True); time.sleep(30)"
    process = subprocess.Popen(
        [sys.executable, "-c", program, str(directory)],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    try:
        assert process.stdout.readline().strip() == "LOCKED"
        s = State(directory)
        with pytest.raises(Halt):
            with s.lock(60):
                pass
        process.kill()
        process.wait(timeout=5)
        with pytest.raises(Halt):
            with s.lock(60):
                pass
        assert s.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        s.close()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_policy_signature_valid_tamper_and_wrong_key(tmp_path, monkeypatch):
    import tradeagent.policy as module

    private, public, message, sig = (
        tmp_path / x for x in ("private.pem", "public.pem", "message", "signature")
    )
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
        check=True,
        capture_output=True,
    )
    policy = {"synthetic": "signature-test-only"}
    message.write_text(dumps(policy))
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
            str(sig),
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(
        module, "AUTONOMOUS_PUBLIC_KEY_SHA256", hashlib.sha256(public.read_bytes()).hexdigest()
    )
    a = {
        "policy": policy,
        "simulation": False,
        "signature": base64.b64encode(sig.read_bytes()).decode(),
    }
    PolicySignature(public).verify(a, False)
    with pytest.raises(Halt):
        PolicySignature(public).verify({**a, "policy": {"synthetic": "tampered"}}, False)
    monkeypatch.setattr(module, "AUTONOMOUS_PUBLIC_KEY_SHA256", "bad")
    with pytest.raises(Halt):
        PolicySignature(public).verify(a, False)
    private.unlink()  # Ephemeral synthetic test key; never enrolled or exported.


@pytest.mark.parametrize(
    "name,value",
    [
        ("max_order_fraction", ".11"),
        ("max_single_name_fraction", ".21"),
        ("min_cash_fraction", ".19"),
        ("max_positions", 6),
        ("max_quote_age_seconds", 121),
        ("max_book_age_seconds", 121),
        ("max_book_future_skew_seconds", 0.251),
        ("min_depth_shares", 99),
        ("max_spread_fraction", ".006"),
        ("max_daily_turnover_fraction", ".21"),
        ("daily_loss_halt_fraction", ".03"),
        ("max_drawdown_fraction", ".11"),
    ],
)
def test_policy_cannot_expand_frozen_risk(name, value):
    with pytest.raises(Halt):
        replace(PolicyLimits(), **{name: value}).validate(Risk())


@pytest.mark.parametrize("fault", ["unordered", "nonpositive", "incomplete"])
def test_canary_history_does_not_accept_unvalidated_bars(fault):
    c = SimClock()
    h = histories(c)
    if fault == "unordered":
        h["TINY"].reverse()
    if fault == "nonpositive":
        h["TINY"][-1]["close_price"] = "0"
    if fault == "incomplete":
        h["TINY"][-1]["begins_at"] = c.iso()
    i, d = CanarySelector().select(
        tiny_snapshot(c), Config(allowed_symbols=["TINY"]), Risk(), facts(c), h, c(), dec(100)
    )
    assert i is None and d[0]["decision"] == "reject"


def test_local_health_cli_readonly_and_persistent_halt(tmp_path, monkeypatch):
    from tradeagent.health import halt_entries

    with harness(tmp_path, monkeypatch) as (e, sim, s, _c):
        before = s.path.read_bytes()
        assert health_report(s.directory)["integrity"] == "ok"
        assert s.path.read_bytes() == before
        halt_entries(s.directory, "human deterministic stop")
        halt_entries(s.directory, "cannot silently replace first halt")
        assert (
            health_report(s.directory)["emergency_halt"][0]["reason"] == "human deterministic stop"
        )
        with pytest.raises(Halt, match="emergency halt"):
            e.prepare_buy("blocked")
        assert sim.calls == []


def test_raw_official_fill_fee_reconciles_through_production_lifecycle(tmp_path, monkeypatch):
    # Real wire normalizer, synthetic raw official data, no execution transport.
    from tradeagent.supervised import SupervisedLifecycle

    raw = RawReadFake()

    def clock():
        return NOW.timestamp()

    monkeypatch.setattr(time, "time", clock)
    cfg = Config(mode="SUPERVISED", supervised_enabled=True, allowed_symbols=["SPY"])
    b = Broker(raw, cfg, Risk())

    class ReadOnlyTransport:
        is_simulation = False

        def invoke(self, *args):
            raise AssertionError("write attempted by read-only reconciliation test")

    adapter = OfficialExecutionAdapter(b, ReadOnlyTransport(), clock)
    initial = b.snapshot(clock())
    s = State(tmp_path / "agent")
    try:
        run = s.start(cfg, Risk())
        i = Intent("SPY", "buy", dec(1), dec("100.01"))
        key = "synthetic-production-normalizer-receipt"
        ref = s.prepare(key, run, i)
        with s.db:
            s.db.execute("CREATE TABLE plans(key PRIMARY KEY,packet)")
            s.db.execute(
                "INSERT INTO plans VALUES(?,?)", (key, dumps({"initial": asdict(initial)}))
            )
        s.update(key, "reviewed")
        s.update(key, "submitting")
        row = raw_filled_order(raw)
        row["ref_id"] = ref
        # No constructor or submission path is used; only canonical reconciliation.
        e = object.__new__(SupervisedLifecycle)
        e.state, e.broker, e.run, e.clock = s, adapter, run, clock
        result = e.reconcile(key, i)
        assert result["status"] == "filled" and result["cash"] == "9899.98"
        receipt = json.loads(
            s.db.execute("SELECT payload FROM events WHERE kind='fill_reconciliation'").fetchone()[
                0
            ]
        )
        assert receipt["fees"] == "0.01" and receipt["executions"] == row["executions"]
        assert all(n.startswith("get_") for n in raw.calls)
    finally:
        s.close()


def test_policy_expiry_between_refresh_and_network_send_cannot_place(tmp_path, monkeypatch):
    with harness(tmp_path, monkeypatch) as (e, sim, s, c):
        plan, i = e.prepare_buy("bar")
        e.guard.artifact["policy"]["expires_at"] = c() + 1
        fence = e.fence
        n = 0

        def elapsed_fence():
            nonlocal n
            fence()
            n += 1
            if n == 2:
                c.advance(2)

        e.fence = elapsed_fence
        with pytest.raises(Halt, match="unknown"):
            e.execute(plan["key"], i)
        assert not any(x["tool"].startswith("place") for x in sim.calls)
        assert s.db.execute("SELECT status FROM intents").fetchone()[0] == "unknown"


@pytest.mark.parametrize("fault", ["config", "risk", "schema"])
def test_engine_context_drift_stops_before_broker_review(tmp_path, monkeypatch, fault):
    with harness(tmp_path, monkeypatch) as (e, sim, _s, _c):
        if fault == "config":
            e.config = replace(e.config, target_fraction=".04")
        if fault == "risk":
            e.risk = replace(e.risk, max_positions=4)
        if fault == "schema":
            e.broker.contracts.hash = "changed"
        with pytest.raises(Halt, match="outside policy context"):
            e.prepare_buy("bar")
        assert sim.calls == []


def test_health_uses_latest_account_evidence_not_old_fill(tmp_path, monkeypatch):
    with harness(tmp_path, monkeypatch) as (e, _sim, s, _c):
        s.event(
            e.run,
            "fill_reconciliation",
            {"positions": {"TINY": "1"}, "cash": "97.68", "nav": "99.995"},
        )
        s.event(
            e.run,
            "final_reconciliation",
            {
                "decision": "matched",
                "snapshot": {"positions": {}, "cash": "99.99", "nav": "99.99", "orders": []},
            },
        )
        report = health_report(s.directory)
        assert report["last_account_evidence"]["positions"] == {}
        assert report["last_account_evidence"]["cash"] == "99.99"
        assert report["last_reconciliation"]["kind"] == "final_reconciliation"
