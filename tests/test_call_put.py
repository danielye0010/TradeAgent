"""Focused v4 software tests using synthetic quotes only; no broker/model transport."""

import copy
import importlib.util
import json
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pytest
from test_bidirectional import assessment, candidate, profile
from test_daily_news import news_assessment
from test_daily_research import data_for

from tradeagent.model import Halt
from tradeagent.prospective import daily
from tradeagent.prospective.daily_reporting import regenerate
from tradeagent.research import call_put
from tradeagent.research.domain import identity
from tradeagent.research.opportunity_workflow import DailyResearch
from tradeagent.research.put_shadow import OptionAccessDenied, OptionsResearchMCP
from tradeagent.standalone_mcp import StandaloneMCP

DAY = date(2026, 10, 12)
T = 1791817200


def quote(stamp, kind="call", symbol="QQQ", *, bid=1.95, ask=2, size=10):
    return {
        "book": {
            "contract_id": symbol + kind,
            "underlying": symbol,
            "kind": kind,
            "expiration": "2026-10-27",
            "strike": 100,
            "asof": stamp,
            "available_at": stamp,
            "bid": bid,
            "ask": ask,
            "multiplier": 100,
            "volume": 100,
            "open_interest": 1000,
        },
        "bid_size": size,
        "ask_size": size,
    }


def selection(stamp=T, kind="call", symbol="QQQ"):
    q = quote(stamp, kind, symbol)
    q["contract"] = {
        "id": symbol + kind,
        "chain_symbol": symbol,
        "type": kind,
        "expiration_date": "2026-10-27",
        "strike_price": "100",
        "trade_value_multiplier": "100",
    }
    return q


def endpoint(q, selected, target, phase="entry"):
    return {
        "status": "OBSERVED",
        "quote": q,
        "allocation": call_put.allocate(selected, q, target) if phase == "entry" else None,
    }


@pytest.mark.parametrize("kind,exit_bid,expected", [("call", 2.5, 194.8), ("put", 1.5, -205.2)])
def test_actual_sides_fees_whole_contracts_and_idle_cash(kind, exit_bid, expected):
    chosen = selection(kind=kind)
    start, end = quote(T + 300, kind), quote(T + 3900, kind, bid=exit_bid, ask=exit_bid + 0.05)
    entry = endpoint(start, chosen, T + 300)
    exit_record = endpoint(end, chosen, T + 3900, "exit")
    result = call_put.option_outcome(chosen, entry, exit_record, T + 300, T + 3900, kind=kind)
    assert result["status"] == "AVAILABLE" and result["instrument"] == "LONG_" + kind.upper()
    assert result["contracts"] == 4 and result["premium_required"] == 800
    assert result["known_modeled_fees"] == pytest.approx(5.2)
    assert result["idle_cash"] == pytest.approx(197.4)
    assert result["exit_fee_reserve"] == pytest.approx(2.6)
    assert result["pnl_dollars"] == pytest.approx(expected)
    assert result["net_return"] == pytest.approx(expected / 1000)
    assert result["return_on_premium"] == pytest.approx(expected / 800)
    assert result["idle_cash"] + 4 * exit_bid * 100 - result["exit_fee_reserve"] == pytest.approx(
        1000 + expected
    )
    end["bid_size"] = 1
    assert (
        call_put.option_outcome(chosen, entry, exit_record, T + 300, T + 3900, kind=kind)["status"]
        == "UNAVAILABLE"
    )


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "late",
        "wrong_id",
        "wrong_kind",
        "wrong_expiry",
        "future_selection",
        "fractional_size",
        "allocation",
    ],
)
def test_unusable_quotes_never_become_cash_or_underlying_pnl(invalid):
    chosen = selection()
    start, end = quote(T + 300), quote(T + 3900)
    entry, exit_record = endpoint(start, chosen, T + 300), endpoint(end, chosen, T + 3900, "exit")
    if invalid == "missing":
        exit_record = None
    if invalid == "late":
        end["book"]["available_at"] += 60
    if invalid == "wrong_id":
        end["book"]["contract_id"] = "other"
    if invalid == "wrong_kind":
        end["book"]["kind"] = "put"
    if invalid == "wrong_expiry":
        end["book"]["expiration"] = "2026-11-20"
    if invalid == "future_selection":
        chosen["book"]["available_at"] = T + 301
    if invalid == "fractional_size":
        start["ask_size"] = 1.5
    if invalid == "allocation":
        entry["allocation"]["contracts"] = 1
    result = call_put.option_outcome(chosen, entry, exit_record, T + 300, T + 3900, kind="call")
    assert (
        result["status"] == "UNAVAILABLE"
        and result["pnl_dollars"] is None
        and result["net_return"] is None
    )


@pytest.mark.parametrize("kind", ["call", "put"])
@pytest.mark.parametrize("missing_quote", [False, True])
def test_contract_selection_dte_liquidity_and_fee_inclusive_affordability(kind, missing_quote):
    calls = []

    class Bridge:
        timeout = 20

        def read(self, name, args):
            calls.append((name, dict(args)))
            if name == "get_option_chains":
                return {
                    "data": {
                        "chains": [
                            {
                                "id": "chain",
                                "symbol": "QQQ",
                                "can_open_position": True,
                                "trade_value_multiplier": "100",
                                "cash_component": None,
                                "underlying_instruments": [{}],
                                "expiration_dates": ["2026-10-13", "2026-10-27", "2026-11-20"],
                            }
                        ]
                    }
                }
            if name == "get_option_instruments":
                assert args["type"] == kind and args["expiration_dates"] == "2026-10-27"
                return {
                    "data": {
                        "instruments": [
                            {
                                "id": str(strike),
                                "chain_id": "chain",
                                "chain_symbol": "QQQ",
                                "type": kind,
                                "expiration_date": "2026-10-27",
                                "strike_price": str(strike),
                                "underlying_type": "equity",
                                "state": "active",
                                "tradability": "tradable",
                                "trade_value_multiplier": "100",
                            }
                            for strike in (99, 100, 101, 102)
                        ]
                    }
                }
            results = []
            for cid in args["instrument_ids"]:
                if missing_quote and cid == "100":
                    results.append({"quote": None})
                else:
                    ask = 10 if cid == "100" else 2
                    results.append(
                        {
                            "quote": {
                                "instrument_id": cid,
                                "updated_at": T,
                                "bid_price": str(ask - 0.05),
                                "ask_price": str(ask),
                                "volume": 100,
                                "open_interest": 1000,
                                "ask_size": 10,
                                "bid_size": 10,
                            }
                        }
                    )
            return {"data": {"results": results}}

    reader = call_put.OptionReader(Bridge(), lambda: T)
    if missing_quote:
        with pytest.raises(ValueError, match="complete genuine option quotes"):
            reader.select("QQQ", 100, kind=kind)
    else:
        result = reader.select("QQQ", 100, kind=kind)
        assert result["book"]["kind"] == kind and result["book"]["strike"] == 99
    assert [name for name, _ in calls].count("get_option_quotes") == 1


def test_access_denial_is_sanitized_typed_and_write_boundary_remains_closed(monkeypatch):
    calls = []

    def rpc(self, method, params, **kwargs):
        calls.append(method)
        return {"isError": True, "content": [{"type": "text", "text": "API error 403: "}]}

    monkeypatch.setattr(StandaloneMCP, "rpc", rpc)
    bridge = OptionsResearchMCP(lambda: pytest.fail("mock must not load authentication"))
    with pytest.raises(OptionAccessDenied, match="HTTP 403"):
        bridge.rpc(
            "tools/call", {"name": "get_option_quotes", "arguments": {"instrument_ids": ["id"]}}
        )
    for name in ("place_equity_order", "place_option_order", "get_accounts", "cancel_order"):
        with pytest.raises(Halt):
            bridge.read(name, {})
    assert calls == ["tools/call"]


def test_independent_call_put_cash_and_unavailable_discovery():
    candidates = [candidate(1, 0.02)]
    available = {
        "QQQ:call": {"status": "SELECTED", "selection": selection()},
        "QQQ:put": {"status": "SELECTED", "selection": selection(kind="put")},
    }
    arms = call_put.select(
        candidates, assessment("long"), assessment("bearish"), "VALID", "VALID", "s", T, available
    )
    assert arms["codex_price"]["instrument"] == "LONG_CALL"
    assert arms["codex_news"]["instrument"] == "LONG_PUT"
    assert arms["codex_news"]["prediction_id"] is None
    unavailable = {"QQQ:call": {"status": "UNAVAILABLE", "reason": "HTTP 403"}}
    arms = call_put.select(
        candidates, assessment("long"), {}, "VALID", "UNAVAILABLE", "s", T, unavailable
    )
    assert (
        arms["codex_price"]["shadow_direction"] == 1
        and arms["codex_price"]["option_status"] == "UNAVAILABLE"
    )
    assert arms["codex_news"]["status"] == "UNAVAILABLE"
    unavailable["QQQ:call"] = {"status": "NO_ELIGIBLE_CONTRACT", "reason": "unaffordable"}
    arms = call_put.select(
        candidates, assessment("long"), assessment("watch"), "VALID", "VALID", "s", T, unavailable
    )
    assert arms["codex_price"]["decision"] == "CASH" and arms["codex_price"]["status"] == "VALID"
    d = {
        "decision_id": "d",
        "comparisons": arms,
        "option_rule": call_put.RULE,
        "option_selections": unavailable,
        "quantitative_observations": [candidate(), {**candidate(), "symbol": "SPY"}],
    }
    resolved = call_put.resolve_arms(d, {s: {"gross": 0.01} for s in ("QQQ", "SPY")}, [])
    assert resolved["codex_price"]["instrument_simulation"]["pnl_dollars"] == 0
    assert resolved["codex_price"]["equity_benchmark"]["instrument"] == "LONG_EQUITY"


def test_transport_http_403_is_typed_without_changing_default_mcp_error(monkeypatch):
    calls = []

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            calls.append("request")

        def getresponse(self):
            return SimpleNamespace(status=403)

        def close(self):
            calls.append("closed")

    monkeypatch.setattr("tradeagent.standalone_mcp.http.client.HTTPSConnection", Connection)
    bridge = OptionsResearchMCP(lambda: "synthetic-token")
    with pytest.raises(OptionAccessDenied, match="HTTP 403"):
        bridge.rpc(
            "tools/call", {"name": "get_option_quotes", "arguments": {"instrument_ids": ["id"]}}
        )
    default = StandaloneMCP(lambda: "synthetic-token")
    with pytest.raises(Halt, match="official MCP HTTP failure; no retry or redirect") as error:
        default.rpc("initialize", {})
    assert type(error.value) is Halt
    assert calls == ["request", "closed", "request", "closed"]


def frozen(store, pool="synthetic"):
    selections = {
        "QQQ:" + k: {"status": "SELECTED", "selection": selection(kind=k)} for k in ("call", "put")
    }
    arms = call_put.select(
        [candidate(1, 0.02)],
        assessment("long"),
        assessment("bearish"),
        "VALID",
        "VALID",
        "s",
        T,
        selections,
    )
    d = {
        "decision_id": "d",
        "research_protocol": call_put.PROTOCOL,
        "evidence_kind": pool,
        "source": "robinhood-opportunity-minute-v1",
        "decision_time": T,
        "outcome_entry_time": T + 300,
        "outcome_exit_time": T + 3900,
        "option_rule": call_put.RULE,
        "option_selections": selections,
        "comparisons": arms,
        "quantitative_observations": [candidate(), {**candidate(), "symbol": "SPY"}],
    }
    store.save("decisions", d, "d", T)
    return d


def test_endpoint_observation_is_idempotent_and_403_stops_other_reads(tmp_path):
    store = DailyResearch(tmp_path)
    clock, reads = [T + 300], []

    class Bridge:
        timeout = 20

        def read(self, *args):
            reads.append(args)
            raise OptionAccessDenied("synthetic HTTP 403")

    @contextmanager
    def factory(*args):
        yield Bridge()

    pipeline = SimpleNamespace(
        store=store,
        clock=lambda: clock[0],
        directory=tmp_path,
        put_reader_factory=factory,
        helper=None,
    )
    try:
        frozen(store)
        call_put.observe(pipeline)
        call_put.observe(pipeline)
        assert len(reads) == 1 and len(store.records("option_observations")) == 2
        assert call_put.access_status(tmp_path)["status"] == "BLOCKED"
        clock[0] = T + 3900
        call_put.observe(pipeline)
        assert len(reads) == 1 and len(store.records("option_observations")) == 4
        assert all(q["status"] == "UNAVAILABLE" for q in store.records("option_observations"))
        assert not call_put.activation_ready(tmp_path)
    finally:
        store.close()


def test_missed_endpoints_do_not_open_connection_or_backfill(tmp_path):
    store = DailyResearch(tmp_path)
    try:
        frozen(store)
        pipeline = SimpleNamespace(
            store=store,
            clock=lambda: T + 4000,
            directory=tmp_path,
            put_reader_factory=lambda *a: pytest.fail("late quote cannot be fetched"),
            helper=None,
        )
        call_put.observe(pipeline)
        assert len(store.records("option_observations")) == 4
        assert all(q["quote"] is None for q in store.records("option_observations"))
    finally:
        store.close()


@pytest.mark.parametrize(
    "pool,missing_put,expected",
    [("synthetic", False, False), ("prospective", True, False), ("prospective", False, True)],
)
def test_activation_gate_requires_both_complete_prospective_cycles(
    tmp_path, pool, missing_put, expected
):
    # These are software fixtures, never evidence of actual broker access or a real cycle.
    store = DailyResearch(tmp_path)
    try:
        d = frozen(store, pool)
        for kind in ("call", "put"):
            selected = d["option_selections"]["QQQ:" + kind]["selection"]
            for phase, target in (("entry", T + 300), ("exit", T + 3900)):
                item = {
                    "decision_id": "d",
                    "selection_key": "QQQ:" + kind,
                    "phase": phase,
                    "status": "UNAVAILABLE"
                    if missing_put and kind == "put" and phase == "exit"
                    else "OBSERVED",
                    "quote": quote(target, kind),
                    "allocation": call_put.allocate(selected, quote(target, kind), target)
                    if phase == "entry"
                    else None,
                }
                store.save("option_observations", item, kind + phase, target)
        store.save(
            "outcomes",
            {
                "decision_id": "d",
                "evidence_kind": pool,
                "candidate_outcomes": {s: {"gross": 0.01} for s in ("QQQ", "SPY")},
            },
            "o",
            T + 4000,
        )
        assert call_put.activation_ready(tmp_path) is expected
    finally:
        store.close()


def test_blocked_report_only_does_not_create_journal_or_call_provider(tmp_path):
    call_put.block_access(tmp_path, T)
    result = regenerate(tmp_path, call_put.PROTOCOL, T)
    text = (tmp_path / "daily-report.md").read_text()
    assert "BLOCKED" in text and "PENDING: no frozen prospective decision" in text
    assert result["orders_submitted"] == 0 and not (tmp_path / "experience.sqlite3").exists()


def test_existing_service_activity_skips_options_codex_without_market_requests(
    tmp_path, monkeypatch
):
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(profile()))
    monkeypatch.setattr(call_put, "codex_slot_available", lambda: False)
    pipeline = daily.Pipeline(
        tmp_path / "state",
        protocol=call_put.PROTOCOL,
        collector=lambda *a, **k: pytest.fail(
            "busy-slot gate must precede collection and inference"
        ),
        clock=lambda: daily.window(DAY, call_put.PROTOCOL)[0],
        universe_profile=p,
    )
    try:
        result = pipeline.tick()
        assert result["run"]["status"] == "INCOMPLETE"
        assert "v1/v2 SHADOW service active" in result["run"]["reason"]
        assert pipeline.store.records("decisions") == []
    finally:
        pipeline.store.close()


def test_v4_pipeline_capture_endpoints_resolution_and_daily_report(tmp_path, monkeypatch):
    start = daily.window(DAY, call_put.PROTOCOL)[0]
    clock = [start]
    base = data_for(DAY, start)
    for b in base["bars"]:
        b["volume"] *= 100

    def collector(symbols, provider, helper, session=None, deadline=None):
        data = copy.deepcopy(base)
        for symbol in data["symbols"]:
            for stamp in range(int(start), int(clock[0]), 60):
                data["bars"].append(
                    {
                        "symbol": symbol,
                        "start": stamp,
                        "end": stamp + 60,
                        "available_at": clock[0],
                        "open": 100,
                        "high": 100.02,
                        "low": 99.99,
                        "close": 100.01,
                        "volume": 1000000,
                    }
                )
        data["observed_at"] = clock[0]
        for q in data["quotes"].values():
            q.update(asof=clock[0], observed_at=clock[0])
        return data

    class Reader:
        def __init__(self, bridge, clock):
            pass

        def select(self, symbol, spot, *, kind, deadline=None):
            return selection(start, kind, symbol)

        def quotes(self, contracts):
            return [
                quote(
                    clock[0],
                    c["type"],
                    c["chain_symbol"],
                    bid=2.5 if c["type"] == "call" else 1.5,
                    ask=2.55 if c["type"] == "call" else 1.55,
                )
                if clock[0] >= start + 3900
                else quote(clock[0], c["type"], c["chain_symbol"])
                for c in contracts
            ]

    @contextmanager
    def factory(*args):
        yield None

    monkeypatch.setattr(call_put, "OptionReader", Reader)
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(profile()))
    assessor = news_assessment(["bearish"], price_stances=["long"])
    old = [
        identity(daily.CONFIG),
        identity(daily.NEWS_CONFIG),
        identity(daily.BIDIRECTIONAL_CONFIG),
    ]
    pipeline = daily.Pipeline(
        tmp_path / "state",
        protocol=call_put.PROTOCOL,
        collector=collector,
        clock=lambda: clock[0],
        evidence_kind="synthetic",
        news_assessor=assessor,
        universe_profile=p,
        put_reader_factory=factory,
    )
    try:
        first = pipeline.tick()
        assert first["run"]["status"] == "COMPLETED"
        d = pipeline.store.decision()
        assert d["comparisons"]["codex_price"]["instrument"] == "LONG_CALL"
        assert d["comparisons"]["codex_news"]["instrument"] == "LONG_PUT"
        pipeline.tick()
        assert len(pipeline.store.records("decisions")) == 1 and len(assessor.calls) == 2
        clock[0] = d["outcome_entry_time"]
        pipeline.tick()
        clock[0] = d["outcome_exit_time"]
        pipeline.tick()
        assert pipeline.store.records("outcomes") == []
        clock[0] += 60
        result = pipeline.tick()
        o = pipeline.store.records("outcomes")[0]
        for name, kind in (("codex_price", "LONG_CALL"), ("codex_news", "LONG_PUT")):
            assert o["comparisons"][name]["instrument_simulation"]["status"] == "AVAILABLE"
            assert o["comparisons"][name]["instrument_simulation"]["instrument"] == kind
            assert o["comparisons"][name]["equity_benchmark"]["instrument"] == "LONG_EQUITY"
        assert o["comparisons"]["codex_news"]["instrument_simulation"]["pnl_dollars"] < 0
        assert result["comparison"]["protocol"] == call_put.PROTOCOL
        assert "LONG_CALL" in (pipeline.directory / "daily-report.md").read_text()
        assert pipeline.store.prior_rows() == [] and pipeline.store.records("executions") == []
        assert not call_put.activation_ready(
            pipeline.directory
        )  # synthetic cycles never arm a timer
        counts = len(pipeline.store.records("option_observations"))
        pipeline.tick()
        assert len(pipeline.store.records("option_observations")) == counts
        assert old == [
            identity(daily.CONFIG),
            identity(daily.NEWS_CONFIG),
            identity(daily.BIDIRECTIONAL_CONFIG),
        ]
    finally:
        pipeline.store.close()


def test_options_installer_prepares_only_new_unit_and_blocks_unproven_activation(
    tmp_path, monkeypatch
):
    spec = importlib.util.spec_from_file_location("install_options", "scripts/install_shadow.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    interpreter = tmp_path / "python"
    interpreter.touch()
    measured = tmp_path / "profile.json"
    measured.write_text(json.dumps(profile()))
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout="UnitFileState=disabled\nActiveState=inactive\n")

    monkeypatch.setattr(installer.subprocess, "run", run)
    installer.install_daily(
        tmp_path / "state",
        tmp_path / "helper",
        interpreter,
        call_put.PROTOCOL,
        universe_profile=measured,
        enable=True,
    )
    assert ["systemctl", "--user", "disable", "--now", "tradeagent-daily-options.timer"] in calls
    assert not any("enable" in args for args in calls)
    units = tmp_path / ".config/systemd/user"
    timer = (units / "tradeagent-daily-options.timer").read_text()
    assert "11:00..59:00 America/New_York" in timer and "12:00..15:00 America/New_York" in timer
    assert not any(
        (units / (name + ".timer")).exists()
        for name in (
            "tradeagent-daily-research",
            "tradeagent-daily-news",
            "tradeagent-daily-bidirectional",
        )
    )
