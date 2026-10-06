import copy
import json
import time
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from robinhood_agent.account_policy import POLICY
from robinhood_agent.broker import Broker, utc_time
from robinhood_agent.codex_bridge import READ_TOOLS, CodexBridge
from robinhood_agent.execution import Execution
from robinhood_agent.model import Config, Halt, Intent, Risk, Snapshot, dec
from robinhood_agent.risk import check_order, check_state
from robinhood_agent.runner import cycle
from robinhood_agent.state import State
from robinhood_agent.strategy import signal
from robinhood_agent.vendor import run_lock
from robinhood_agent.vendor.client import _parse_result


@pytest.fixture
def snap():
    now = time.time()
    return Snapshot(
        "test-account",
        now,
        D(10000),
        D(10000),
        D(10000),
        {},
        {},
        {"SPY": D(100)},
        {"SPY": now},
        {"SPY": D("100.01")},
        {"SPY": D("99.99")},
        [],
        D(0),
        tradable={"SPY": True},
        bid_times={"SPY": now},
        ask_times={"SPY": now},
        regular_session=True,
        liquidity={"SPY": {"asof": now, "bid_size": 10000, "ask_size": 10000}},
        agentic_eligible=True,
        account_policy=POLICY,
    )


@pytest.fixture
def config():
    return Config(allowed_symbols=["SPY"])


def check(intent, snap, config, risk=None, baseline=10000, exposure=0, turnover=0):
    return check_order(
        intent, snap, config, risk or Risk(), time.time(), baseline, exposure, turnover
    )


def buy(q=5, symbol="SPY"):
    return Intent(symbol, "buy", D(q), D("100.01"))


def test_conservative_valid_buy(snap, config):
    check(buy(), snap, config)


@pytest.mark.parametrize(
    "intent,reason",
    [
        (buy(11), "new exposure"),
        (buy("0.5"), "whole-share"),
        (buy(5, "TSLA"), "not allowed"),
        (Intent("SPY", "sell", D(1), D("99.99")), "short"),
        (Intent("SPY", "buy", D(1), D("100.01"), "crypto"), "not allowed"),
        (Intent("SPY", "buy", D("NaN"), D("100.01")), "non-finite"),
        (Intent("SPY", "buy", D(1), D(1)), "differs"),
    ],
)
def test_order_risk_rejects(intent, reason, snap, config):
    with pytest.raises(Halt, match=reason):
        check(intent, snap, config)


def test_concentration(snap, config):
    snap.positions["SPY"] = D(19)
    snap.available["SPY"] = D(19)
    snap.cash = snap.buying_power = D(8100)
    with pytest.raises(Halt, match="concentration"):
        check(buy(2), snap, config)


def test_cash_reserve(snap, config):
    snap.positions["OTHER"] = D(79)
    snap.prices["OTHER"] = D(100)
    snap.quote_times["OTHER"] = time.time()
    snap.cash = snap.buying_power = D(2100)
    with pytest.raises(Halt, match="cash reserve"):
        check(buy(2), snap, config)


def test_position_count(snap, config):
    for i in range(5):
        symbol = f"HELD{i}"
        snap.positions[symbol] = D(1)
        snap.prices[symbol] = D(100)
        snap.quote_times[symbol] = time.time()
    snap.cash = snap.buying_power = D(9500)
    with pytest.raises(Halt, match="position count"):
        check(buy(), snap, config)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda s: setattr(s, "asof", time.time() - 121), "stale account"),
        (lambda s: setattr(s, "asof", float("nan")), "stale account"),
        (lambda s: setattr(s, "nav", D("Infinity")), "non-finite"),
        (lambda s: setattr(s, "reconciled", False), "unreconciled"),
        (lambda s: setattr(s, "account_type", "margin"), "non-cash"),
        (lambda s: s.orders.append({"id": "open", "state": "queued"}), "open/unknown"),
        (lambda s: s.orders.append({"id": "unknown", "state": "nonsense"}), "open/unknown"),
        (lambda s: s.quote_times.update(SPY=time.time() - 121), "stale market"),
        (lambda s: setattr(s, "cash", D(9000)), "valuation"),
        (lambda s: s.positions.update(SPY=D(-1)), "short position"),
    ],
)
def test_state_risk_rejects(mutation, reason, snap):
    mutation(snap)
    with pytest.raises(Halt, match=reason):
        check_state(snap, Risk(), time.time())


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda s: setattr(s, "regular_session", False), "regular session"),
        (lambda s: s.bid_times.update(SPY=time.time() - 121), "stale bid/ask"),
        (lambda s: s.bids.update(SPY=D(101)), "invalid bid/ask"),
        (lambda s: s.asks.update(SPY=D(101)), "spread"),
        (lambda s: s.tradable.update(SPY=False), "not tradable"),
        (lambda s: setattr(s, "buying_power", D(1)), "buying power"),
    ],
)
def test_execution_risk_rejects(mutation, reason, snap, config):
    mutation(snap)
    with pytest.raises(Halt, match=reason):
        check(buy(), snap, config)


def test_daily_and_cycle_limits(snap, config):
    with pytest.raises(Halt, match="daily loss"):
        check(buy(), snap, config, baseline=11000)
    with pytest.raises(Halt, match="daily turnover"):
        check(buy(), snap, config, turnover=1600)
    with pytest.raises(Halt, match="new exposure"):
        check(buy(), snap, config, exposure=600)
    with pytest.raises(Halt, match="risk accounting"):
        check(buy(), snap, config, exposure=-1)


def test_sell_reserved_shares(snap, config):
    snap.positions["SPY"] = D(5)
    snap.available["SPY"] = D(1)
    snap.cash = snap.buying_power = D(9500)
    with pytest.raises(Halt, match="reserved"):
        check(Intent("SPY", "sell", D(2), D("99.99")), snap, config)


@pytest.mark.parametrize(
    "config",
    [
        Config(mode="LIVE"),
        Config(live_enabled=True),
        Config(mode="SUPERVISED"),
        Config(allowed_symbols=[["SPY"]]),
        Config(target_fraction="NaN"),
    ],
)
def test_bad_configuration(config):
    with pytest.raises(Halt):
        config.validate()


class FakeBroker:
    def __init__(self, snapshot):
        self.initial = copy.deepcopy(snapshot)
        self.account_scope = [{"account_key": snapshot.account_key}]
        self.writes = []
        self.reconciliations = 0

    def snapshot(self):
        return copy.deepcopy(self.initial)

    def histories(self):
        return {
            "SPY": [
                {"begins_at": "2026-10-02T13:30:00Z", "close_price": str(50 + i / 10)}
                for i in range(220)
            ]
        }

    def reconcile(self, initial):
        self.reconciliations += 1
        return self.snapshot()

    def review(self, intent):
        self.writes.append("review")
        return {"payload": intent.payload(), "checks_passed": True, "asof": time.time()}

    def submit(self, intent, ref_id):
        self.writes.append("submit")
        raise TimeoutError("acknowledgment lost")

    def cancel(self, *_):
        self.writes.append("cancel")


def test_shadow_complete_and_duplicate(tmp_path, snap, config):
    state = State(tmp_path / "state")
    broker = FakeBroker(snap)
    first = cycle(broker, state, config, Risk())
    second = cycle(broker, state, config, Risk())
    assert first["status"] == "completed"
    assert first["proposals"][0]["status"] == "shadow_recorded"
    assert second["proposals"][0]["status"] == "duplicate_suppressed"
    assert state.db.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 1
    assert not broker.writes
    assert broker.reconciliations == 2
    assert state.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    log = [json.loads(x) for x in (state.directory / "events.jsonl").read_text().splitlines()]
    assert len(log) == state.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    state.close()


def test_real_broker_rejects_all_write_categories(config):
    broker = Broker(None, config, Risk())
    for call in (
        lambda: broker.review(buy()),
        lambda: broker.submit(buy(), "id"),
        lambda: broker.cancel("id"),
    ):
        with pytest.raises(Halt, match="disabled"):
            call()


def test_supervised_requires_bound_approval(tmp_path, snap, config):
    config = replace(config, mode="SUPERVISED", supervised_enabled=True)
    state = State(tmp_path)
    run = state.start(config, Risk())
    broker = FakeBroker(snap)
    engine = Execution(state, broker, config, Risk(), run, lambda: None)
    with pytest.raises(Halt, match="explicit user approval"):
        engine.process(buy(), snap, 10000, "bar", approval="yes")
    assert broker.writes == ["review"]
    assert state.db.execute("SELECT status FROM intents").fetchone()[0] == "reviewed"
    state.close()


def test_supervised_rechecks_risk_before_review(tmp_path, snap, config):
    config = replace(config, mode="SUPERVISED", supervised_enabled=True)
    state = State(tmp_path)
    run = state.start(config, Risk())
    broker = FakeBroker(snap)
    engine = Execution(state, broker, config, Risk(), run, lambda: None)
    with pytest.raises(Halt, match="new exposure"):
        engine.process(buy(11), snap, 10000, "bar")
    assert not broker.writes
    state.close()


def test_live_never_runs(tmp_path, snap):
    state = State(tmp_path)
    broker = FakeBroker(snap)
    with pytest.raises(Halt, match="LIVE"):
        cycle(broker, state, Config(mode="LIVE"), Risk())
    assert not broker.writes
    assert state.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    state.close()


def test_pending_and_interrupted_recovery(tmp_path, snap, config):
    state = State(tmp_path)
    previous = state.start(config, Risk())
    ref_id = state.prepare("pending-key", previous, buy())
    state.update("pending-key", "reviewed")
    state.update("pending-key", "submitting")
    state.update("pending-key", "unknown")
    current = state.start(config, Risk())
    with pytest.raises(Halt, match="ambiguous"):
        state.recover(current, snap)
    order = {
        "id": "broker-id",
        "ref_id": ref_id,
        "symbol": "SPY",
        "side": "buy",
        "quantity": "5",
        "state": "queued",
    }
    snap.orders = [order]
    with pytest.raises(Halt, match="pending"):
        state.recover(current, snap)
    assert state.db.execute("SELECT status FROM intents").fetchone()[0] == "pending"
    order["state"] = "filled"
    state.recover(current, snap)
    assert state.db.execute("SELECT status FROM intents").fetchone()[0] == "filled"
    assert (
        state.db.execute("SELECT status FROM runs WHERE id=?", (previous,)).fetchone()[0]
        == "interrupted"
    )
    state.close()


def test_prepared_and_reviewed_never_replayed(tmp_path, snap, config):
    state = State(tmp_path)
    old = state.start(config, Risk())
    state.prepare("old", old, buy())
    new = state.start(config, Risk())
    state.recover(new, snap)
    assert state.db.execute("SELECT status FROM intents").fetchone()[0] == "abandoned"
    assert state.prepare("old", new, buy()) is None
    state.close()


def test_single_run_process_lock_and_fence(tmp_path):
    first, second = State(tmp_path), State(tmp_path)
    with first.lock(60) as fence:
        fence()
        with pytest.raises(Halt, match="process lock"):
            with second.lock(60):
                pytest.fail("concurrent lock admitted")
        first.db.execute("SELECT 1")
    with second.lock(60) as fence:
        fence()
    first.close()
    second.close()


def test_upstream_expired_lease_fencing(tmp_path):
    path = str(tmp_path / "lease.sqlite3")
    old = run_lock.acquire(path, now=100, lease_seconds=60)
    assert old["ok"]
    assert not run_lock.acquire(path, now=101, lease_seconds=60)["ok"]
    new = run_lock.acquire(path, now=161, lease_seconds=60)
    assert new["ok"]
    assert not run_lock.renew(old["token"], path, now=162, lease_seconds=60)["ok"]
    assert not run_lock.release(old["token"], path)["ok"]
    assert run_lock.release(new["token"], path)["ok"]


def test_runtime_rejects_windows_mounts():
    with pytest.raises(Halt, match="Linux filesystem"):
        State(__import__("pathlib").Path("/mnt/c/not-allowed"))


def test_transport_error_is_not_success():
    with pytest.raises(Exception, match="broker tool returned an error"):
        _parse_result(SimpleNamespace(isError=True, structuredContent={"data": "bad"}))
    assert _parse_result(SimpleNamespace(structuredContent={"data": 1})) == {"data": 1}


def test_read_bridge_rejects_write_without_rpc():
    b = CodexBridge(__import__("pathlib").Path.cwd())
    b.tools = {name: {} for name in READ_TOOLS}
    for name in ("place_equity_order", "review_equity_order", "cancel_equity_order"):
        with pytest.raises(Halt, match="read-only capability"):
            b.read(name, {})
    assert not b.calls


@pytest.mark.parametrize("value", [None, True, "NaN", "Infinity", "oops"])
def test_invalid_decimal(value):
    with pytest.raises(Halt):
        dec(value)


def test_timestamps_and_strategy():
    with pytest.raises(Halt):
        utc_time("2026-10-05T12:00:00")
    with pytest.raises(Halt):
        signal(["1"] * 204, "bar")
    assert signal([str(50 + i) for i in range(220)], "bar")["score"] == 2
    assert signal([str(300 - i) for i in range(220)], "bar")["score"] == -2


def test_pagination_must_complete(config):
    class RepeatedCursor:
        def read(self, *_):
            return {"data": {"positions": [], "next": "again"}}

    broker = Broker(RepeatedCursor(), config, Risk())
    broker.account = {"account_number": "fake-account"}
    with pytest.raises(Halt, match="repeated"):
        broker.paged("get_equity_positions", "positions")


def test_real_reconciliation_detects_external_change(snap, config):
    broker = Broker(None, config, Risk())
    changed = copy.deepcopy(snap)
    changed.cash -= D(1)
    broker.snapshot = lambda: changed
    with pytest.raises(Halt, match="state changed"):
        broker.reconcile(snap)


def test_generic_protocol_cannot_invoke_writes_or_model_turns():
    bridge = CodexBridge(__import__("pathlib").Path.cwd())
    bridge.tools = {name: {"annotations": {"readOnlyHint": True}} for name in READ_TOOLS}
    for name in ("place_equity_order", "cancel_equity_order", "review_equity_order"):
        with pytest.raises(Halt, match="read-only capability"):
            bridge.rpc("mcpServer/tool/call", {"server": "robinhood-trading", "tool": name})
    with pytest.raises(Halt, match="read-only project capability"):
        bridge.rpc("turn/start", {})
    assert bridge.serial == 0 and not bridge.calls
