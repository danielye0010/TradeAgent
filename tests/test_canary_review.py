"""Review-only authority and unchanged RSI/risk decisions with synthetic brokers."""

import copy
from dataclasses import replace

import pytest
from test_release_policy import facts, histories, tiny_snapshot

from tradeagent import canary_review as module
from tradeagent.canary_review import CanaryReviewBridge, CatalogOnly, run_review
from tradeagent.codex_bridge import SERVER, CodexBridge
from tradeagent.legacy.canary import CanarySelector
from tradeagent.legacy.policy import require_autonomous_release
from tradeagent.model import Config, Halt, Risk, digest
from tradeagent.research.demo import fixture
from tradeagent.research.lab import scan, seed
from tradeagent.research.store import Experience
from tradeagent.schema import Contracts
from tradeagent.simulator import SimClock
from tradeagent.state import State


def market(clock, negative=False):
    snapshot, _ = fixture(clock(), "prospective")
    bars = tuple(
        replace(
            b,
            symbol="TINY",
            open=b.open * 0.023,
            high=b.high * 0.023,
            low=b.low * 0.023,
            close=b.close * 0.023,
        )
        for b in snapshot.bars
    )
    return replace(
        snapshot,
        symbol="TINY",
        bars=bars,
        options=(),
        bid=2.31,
        ask=2.32,
        session_open=2.4 if negative else 2.3,
        previous_close=2.5 if negative else 2.2954,
    )


def review_response(clock, **changes):
    quote = {
        "symbol": "TINY",
        "last_trade_price": "2.31",
        "venue_last_trade_time": "2026-10-05T15:00:00Z",
        "last_non_reg_trade_price": None,
        "venue_last_non_reg_trade_time": None,
        "adjusted_previous_close": "2.3",
        "previous_close": "2.3",
        "previous_close_date": "2026-10-02",
        "bid_price": "2.31",
        "venue_bid_time": "2026-10-05T15:00:00Z",
        "ask_price": "2.32",
        "venue_ask_time": "2026-10-05T15:00:00Z",
        "has_traded": True,
        "state": "active",
    }
    from datetime import datetime, timezone

    stamp = datetime.fromtimestamp(clock(), timezone.utc).isoformat()
    for key in ("venue_last_trade_time", "venue_bid_time", "venue_ask_time"):
        quote[key] = stamp
    return {
        "data": {
            "symbol": "TINY",
            "side": "buy",
            "quantity": "1",
            "type": "limit",
            "limit_price": "2.32",
            "order_checks": {},
            "quote_data": quote,
            **changes,
        },
        "guide": "synthetic review only",
    }


def bridge(tmp_path, monkeypatch, clock, response=None, lose=False):
    b = CanaryReviewBridge(tmp_path, lambda: "SYNTHETIC_TOKEN")
    b.tools = copy.deepcopy(Contracts().tools)
    b.inventory = {"data": [{"name": SERVER, "serverInfo": {"version": "1.7.0"}}]}
    b.thread_id = "synthetic"
    b.before_review = lambda: None
    b.review_arguments_hash = digest(arguments())
    sent = []
    monkeypatch.setattr(CodexBridge, "send", lambda self, message: sent.append(message))

    def receive(deadline):
        if lose:
            raise TimeoutError("synthetic lost review response")
        return {
            "id": b.serial,
            "result": {
                "structuredContent": response or review_response(clock),
                "content": [],
                "isError": False,
            },
        }

    b.receive = receive
    return b, sent


def arguments():
    return {
        "account_number": "synthetic-only",
        "symbol": "TINY",
        "side": "buy",
        "quantity": "1",
        "type": "limit",
        "limit_price": "2.32",
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
    }


def test_one_review_without_production_key_and_no_replay(tmp_path, monkeypatch):
    monkeypatch.setattr("tradeagent.legacy.policy.AUTONOMOUS_PUBLIC_KEY_SHA256", None)
    b, sent = bridge(tmp_path, monkeypatch, SimClock())
    assert b.review_equity_once(arguments())["data"]["quantity"] == "1"
    with pytest.raises(Halt, match="consumed"):
        b.review_equity_once(arguments())
    assert b.wire_calls == ["review_equity_order"] and len(sent) == 1


@pytest.mark.parametrize(
    "name",
    [
        "place_equity_order",
        "cancel_equity_order",
        "replace_equity_order",
        "place_option_order",
        "unknown_write",
    ],
)
def test_execution_is_unreachable_even_with_enrolled_key(tmp_path, monkeypatch, name):
    monkeypatch.setattr("tradeagent.legacy.policy.AUTONOMOUS_PUBLIC_KEY_SHA256", "a" * 64)
    b, sent = bridge(tmp_path, monkeypatch, SimClock())
    params = {"server": SERVER, "tool": name, "arguments": arguments()}
    for attempt in (
        lambda: b.execution_call(name, arguments()),
        lambda: b.rpc("mcpServer/tool/call", params),
        lambda: b._exchange("mcpServer/tool/call", params),
        lambda: b.send({"method": "mcpServer/tool/call", "params": params}),
    ):
        with pytest.raises(Halt):
            attempt()
    assert not sent and not b.wire_calls


def test_timeout_spends_review_before_send_and_never_retries(tmp_path, monkeypatch):
    b, sent = bridge(tmp_path, monkeypatch, SimClock(), lose=True)
    with pytest.raises(TimeoutError):
        b.review_equity_once(arguments())
    with pytest.raises(Halt, match="consumed"):
        b.review_equity_once(arguments())
    with pytest.raises(Halt):
        b.send(
            {
                "method": "mcpServer/tool/call",
                "params": {
                    "server": SERVER,
                    "tool": "review_equity_order",
                    "arguments": arguments(),
                },
            }
        )
    assert len(sent) == 1 and b.wire_calls == ["review_equity_order"]


def test_execution_paths_still_require_production_authority(tmp_path, monkeypatch):
    monkeypatch.setattr("tradeagent.legacy.policy.AUTONOMOUS_PUBLIC_KEY_SHA256", None)
    with pytest.raises(Halt, match="pinned production key"):
        require_autonomous_release("CANARY")
    with pytest.raises(Halt, match="signed production guard"):
        CodexBridge(tmp_path).execution_call("place_equity_order", arguments())


@pytest.mark.parametrize("drift", ["version", "schema"])
def test_review_schema_version_drift_blocks_before_wire(tmp_path, monkeypatch, drift):
    b, sent = bridge(tmp_path, monkeypatch, SimClock())
    if drift == "version":
        b.inventory["data"][0]["serverInfo"]["version"] = "1.7.1"
    else:
        b.tools["review_equity_order"]["inputSchema"] = {}
    with pytest.raises(Halt, match="drift"):
        b.review_equity_once(arguments())
    assert not sent and not b.wire_calls


def test_metadata_capability_cannot_call_any_broker_tool():
    for name in ("get_accounts", "review_equity_order", "place_equity_order"):
        with pytest.raises(Halt, match="every broker call"):
            CatalogOnly(lambda: "SYNTHETIC_TOKEN").rpc("tools/call", {"name": name})


def test_normal_rsi_canary_and_risk_decisions_reused_without_intents(tmp_path, monkeypatch):
    c = SimClock()
    b, _ = bridge(tmp_path, monkeypatch, c)
    monkeypatch.setattr(module, "deployment_hash", lambda root: "synthetic-freeze")
    config, risk = Config(allowed_symbols=["TINY"]), Risk()
    snap = tiny_snapshot(c)

    class Broker:
        account = {"account_number": "synthetic-only"}

        def snapshot(self):
            return copy.deepcopy(snap)

        def histories(self):
            return histories(c)

    state, store, control = (
        State(tmp_path / "journal"),
        Experience(tmp_path / "research"),
        Experience(tmp_path / "control"),
    )
    try:
        seed(store, c() - 86400)
        seed(control, c() - 86400)
        expected = scan(control, market(c), c())
        intent, decisions = CanarySelector().select(
            snap, config, risk, facts(c), histories(c), c(), snap.nav
        )
        result = run_review(
            b,
            Broker(),
            state,
            store,
            config,
            risk,
            tmp_path,
            digest("synthetic-only"),
            lambda: {"TINY": market(c)},
            lambda: facts(c),
            c,
        )
        assert result["status"] == "READY_FOR_USER_CONFIRMED_MANUAL_CANARY"
        assert result["RSI"][0]["ranking"] == expected["ranking"]
        assert result["selection"] == decisions
        assert result["proposed_order"] == intent.payload()
        assert set(result["risk_gates"].values()) == {"PASS"}
        assert result["broker_counts"] == {"reads": 0, "review": 1, "place": 0, "cancel": 0}
        assert state.db.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 0
        assert not state.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='policy_decisions'"
        ).fetchone()
    finally:
        state.close()
        store.close()
        control.close()


@pytest.mark.parametrize("fault", ["no_signal", "stale", "spread", "depth"])
def test_no_trade_or_failed_frozen_gate_never_forces_review(tmp_path, monkeypatch, fault):
    c = SimClock()
    b, sent = bridge(tmp_path, monkeypatch, c)
    monkeypatch.setattr(module, "deployment_hash", lambda root: "synthetic-freeze")
    s = tiny_snapshot(c)
    m = market(c)
    if fault == "no_signal":
        # All versions abstain under unchanged parameters on a flat completed market.
        m = replace(
            m,
            bars=tuple(replace(x, open=2.3, high=2.3, low=2.3, close=2.3) for x in m.bars),
            benchmark_bars=tuple(
                replace(x, open=500.0, high=500.0, low=500.0, close=500.0) for x in m.benchmark_bars
            ),
            session_open=2.3,
            previous_close=2.3,
        )
    elif fault == "stale":
        s.quote_times["TINY"] = c() - 121
    elif fault == "spread":
        from tradeagent.model import dec

        s.bids["TINY"] = dec(2)
        m = replace(m, bid=2.0)
    else:
        s.liquidity["TINY"]["ask_size"] = 0

    class Broker:
        account = {"account_number": "synthetic-only"}

        def snapshot(self):
            return s

        def histories(self):
            return histories(c)

    state, store = State(tmp_path / "journal"), Experience(tmp_path / "research")
    try:
        seed(store, c() - 86400)
        result = run_review(
            b,
            Broker(),
            state,
            store,
            Config(allowed_symbols=["TINY"]),
            Risk(),
            tmp_path,
            digest("synthetic-only"),
            lambda: {"TINY": m},
            lambda: facts(c),
            c,
        )
        assert result["status"] in {"NO_TRADE", "HALT"}
        assert result["broker_counts"]["review"] == 0 and not sent
    finally:
        state.close()
        store.close()


def test_changed_prepared_payload_never_reaches_review(tmp_path, monkeypatch):
    b, sent = bridge(tmp_path, monkeypatch, SimClock())
    with pytest.raises(Halt, match="prepared candidate"):
        b.review_equity_once({**arguments(), "limit_price": "2.33"})
    assert not sent and not b.wire_calls


def test_live_collector_never_backdates_receipt_to_a_completed_bar():
    from datetime import datetime, timezone

    from tradeagent.canary_review import live_rsi_snapshots

    c = SimClock()
    times = iter([c(), c() + 0.1, c() + 0.2])

    class Reads:
        def read(self, name, args):
            if name == "get_equity_quotes":
                return {
                    "data": {
                        "results": [
                            {
                                "quote": {
                                    "symbol": s,
                                    "bid_price": "2.31",
                                    "ask_price": "2.32",
                                    "venue_bid_time": datetime.fromtimestamp(
                                        c(), timezone.utc
                                    ).isoformat(),
                                    "venue_ask_time": datetime.fromtimestamp(
                                        c(), timezone.utc
                                    ).isoformat(),
                                }
                            }
                            for s in ["TINY", "SPY"]
                        ]
                    }
                }
            return {
                "data": {
                    "results": [
                        {
                            "symbol": s,
                            "interval": "5minute",
                            "bounds": "regular",
                            "bars": [
                                {
                                    "begins_at": datetime.fromtimestamp(
                                        c() - (5 - i) * 300, timezone.utc
                                    ).isoformat(),
                                    "open_price": "2.3",
                                    "high_price": "2.3",
                                    "low_price": "2.3",
                                    "close_price": "2.3",
                                    "volume": 100,
                                    "interpolated": False,
                                }
                                for i in range(5)
                            ],
                        }
                        for s in ["TINY", "SPY"]
                    ]
                }
            }

    captured = []
    with pytest.raises(ValueError, match="snapshot|future"):
        live_rsi_snapshots(Reads(), ["TINY"], lambda: next(times), captured.append)
    assert captured[0]["history_receipt"] > captured[0]["latest_completed_ends"]["TINY"]
    assert captured[0]["quote_receipt"] == c() + 0.1


def test_new_cli_route_is_separate_from_placement_cli(monkeypatch):
    from tradeagent.cli import main

    seen = []
    monkeypatch.setattr("tradeagent.canary_review_cli.main", lambda args: seen.append(args) or 0)
    assert main(["canary-review", "--output", "synthetic-unused"]) == 0
    assert seen == [["--output", "synthetic-unused"]]


def test_metadata_parity_ignores_codex_null_presentation_fields(tmp_path, monkeypatch):
    complete = copy.deepcopy(Contracts().tools)
    server = {"name": SERVER, "version": "1.7.0"}

    class Catalog:
        def __init__(self, *args):
            pass

        def __enter__(self):
            self.tools, self.server_info = complete, server
            return self

        def __exit__(self, *args):
            pass

    def connected(self):
        self.tools = {
            "review_equity_order": {
                **copy.deepcopy(complete["review_equity_order"]),
                "annotations": None,
            }
        }
        self.inventory = {
            "data": [
                {
                    "name": SERVER,
                    "serverInfo": {**server, "title": None, "description": None, "icons": None},
                }
            ]
        }
        return self

    monkeypatch.setattr(module, "CatalogOnly", Catalog)
    monkeypatch.setattr(CodexBridge, "__enter__", connected)
    with CanaryReviewBridge(tmp_path, lambda: "SYNTHETIC_TOKEN") as b:
        assert b.tools == complete and not b.wire_calls


@pytest.mark.parametrize("fault", ["name", "version", "schema"])
def test_dual_catalog_parity_still_fails_closed(tmp_path, monkeypatch, fault):
    complete = copy.deepcopy(Contracts().tools)
    server = {"name": SERVER, "version": "1.7.0"}

    class Catalog:
        def __init__(self, *args):
            pass

        def __enter__(self):
            self.tools, self.server_info = complete, server
            return self

        def __exit__(self, *args):
            pass

    def connected(self):
        live_server = {**server, "title": None}
        self.tools = {"review_equity_order": copy.deepcopy(complete["review_equity_order"])}
        if fault in {"name", "version"}:
            live_server[fault] = "unexpected"
        else:
            self.tools["review_equity_order"]["inputSchema"] = {}
        self.inventory = {"data": [{"name": SERVER, "serverInfo": live_server}]}
        return self

    monkeypatch.setattr(module, "CatalogOnly", Catalog)
    monkeypatch.setattr(CodexBridge, "__enter__", connected)
    with pytest.raises(Halt, match="disagree"):
        CanaryReviewBridge(tmp_path, lambda: "SYNTHETIC_TOKEN").__enter__()
