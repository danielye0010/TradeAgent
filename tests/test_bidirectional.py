"""Focused offline signed selection, pricing, missing observations and timing."""

import copy
import json
from contextlib import contextmanager
from datetime import date

import pytest
from test_daily_news import news_assessment
from test_daily_research import data_for

from tradeagent.prospective import daily
from tradeagent.research import bidirectional, opportunities
from tradeagent.research.domain import identity
from tradeagent.research.put_shadow import PutReader, put_outcome
from tradeagent.research.research_universe import RESEARCH_UNIVERSE, feasible_universe


def candidate(direction=-1, expected=-0.02):
    return {
        "symbol": "QQQ",
        "scanner": {"symbol": "QQQ", "categories": ["relative_weakness"]},
        "predictions": [
            {
                "prediction_id": "p",
                "strategy_id": "residual_strength",
                "direction": direction,
                "expected_return": expected,
            }
        ],
        "outcome_costs": {"base_side_cost": 0.001, "stress_side_cost": 0.002},
    }


def assessment(stance):
    return {"QQQ": {"stance": stance, "rank": 1, "sources": [], "hypothesis_type": "price_action"}}


@pytest.mark.parametrize(
    "direction,expected,stance,decision",
    [
        (-1, -0.02, "bearish", "BEARISH"),
        (1, 0.02, "long", "LONG"),
        (-1, -0.002, "bearish", "CASH"),
        (0, 0, "watch", "CASH"),
    ],
)
def test_signed_selection_and_cost_screened_cash(direction, expected, stance, decision):
    arms = bidirectional.select(
        [candidate(direction, expected)],
        assessment(stance),
        assessment(stance),
        "VALID",
        "VALID",
        "seed",
        100,
    )
    for name in ("quant_only", "codex_price", "codex_news", "quant_codex"):
        assert arms[name]["decision"] == decision
        assert arms[name]["shadow_direction"] == (direction if decision != "CASH" else 0)
        assert arms[name]["plan"] is None
    disagreement = bidirectional.select(
        [candidate()], assessment("long"), assessment("watch"), "VALID", "VALID", "seed", 100
    )
    assert disagreement["quant_only"]["shadow_direction"] == -1
    assert disagreement["codex_price"]["decision"] == "CASH"


@pytest.mark.parametrize(
    "direction,gross,expected",
    [
        (1, 0.02, 101.898 / 100.1 - 1),
        (-1, -0.02, 0.02 - 0.001 * 1.98),
        (-1, 0.02, -0.02 - 0.001 * 2.02),
    ],
)
def test_signed_net_costs_and_common_reference_notional(direction, gross, expected):
    result = bidirectional.directional(
        {"gross": gross, "entry_price": 100, "exit_price": 100 * (1 + gross)},
        direction,
        {"base_side_cost": 0.001, "stress_side_cost": 0.002},
    )
    assert result["net_return"] == pytest.approx(expected)
    assert result["pnl_dollars"] == pytest.approx(1000 * expected)
    assert result["instrument"] == (
        "LONG_EQUITY" if direction > 0 else "THEORETICAL_BEARISH_UNDERLYING"
    )


def quote(stamp, *, bid=1.95, ask=2, size=10, contract="put", kind="put"):
    return {
        "book": {
            "contract_id": contract,
            "underlying": "QQQ",
            "kind": kind,
            "expiration": "2026-11-20",
            "strike": 100,
            "asof": stamp,
            "available_at": stamp,
            "bid": bid,
            "ask": ask,
            "multiplier": 100,
            "volume": 100,
            "open_interest": 1000,
        },
        "ask_size": size,
        "bid_size": size,
    }


def test_genuine_ask_bid_put_pnl_with_fees_and_idle_cash():
    selection, entry, end = (
        quote(1791900000),
        quote(1791900300),
        quote(1791903900, bid=2.5, ask=2.55),
    )
    result = put_outcome(
        selection, entry, end, entry["book"]["asof"], end["book"]["asof"], fees_per_contract=0.65
    )
    assert result["contracts"] == 4
    assert result["premium_per_contract"] == 200
    assert result["premium_required"] == 800
    assert result["pnl_dollars"] == pytest.approx(4 * 48.7)
    assert result["net_return"] == pytest.approx(194.8 / 1000)
    assert result["return_on_premium"] == pytest.approx(48.7 / 200)
    # Entry allocation cannot depend on later available exit size.
    small_exit = quote(end["book"]["asof"], bid=2.5, ask=2.55, size=1)
    assert (
        put_outcome(selection, entry, small_exit, entry["book"]["asof"], end["book"]["asof"])[
            "status"
        ]
        == "UNAVAILABLE"
    )


@pytest.mark.parametrize(
    "missing", ["selection", "entry", "exit", "stale", "wrong_contract", "wrong_strike", "call"]
)
def test_missing_or_wrong_put_quotes_never_become_underlying_pnl(missing):
    selection, start, end = (
        quote(1791900000),
        quote(1791900300),
        quote(1791903900, bid=2.5, ask=2.55),
    )
    if missing == "selection":
        selection = None
    if missing == "entry":
        start = None
    if missing == "exit":
        end = None
    if missing == "stale":
        end["book"]["asof"] -= 61
    if missing == "wrong_contract":
        end["book"]["contract_id"] = "other"
    if missing == "wrong_strike":
        start["book"]["strike"] = 101
    if missing == "call":
        start["book"]["kind"] = "call"
    result = put_outcome(selection, start, end, 1791900300, 1791903900)
    assert result["status"] == "UNAVAILABLE"
    assert result["pnl_dollars"] is None and result["net_return"] is None


def profile(count=31):
    return {
        "provider": "robinhood",
        "measurements": [
            {
                "symbols": list(RESEARCH_UNIVERSE[:count]),
                "complete": True,
                "elapsed_seconds": 1,
                "interval": "minute",
                "bounds": "regular",
            }
        ],
    }


def test_largest_measured_feasible_prefix_and_hard_collection_deadline(monkeypatch):
    p = profile()
    for count, elapsed in ((60, 20), (106, 46)):
        p["measurements"].append(
            {
                **p["measurements"][0],
                "symbols": list(RESEARCH_UNIVERSE[:count]),
                "elapsed_seconds": elapsed,
            }
        )
    assert feasible_universe(p) == RESEARCH_UNIVERSE[:60]
    clock = [1791900000]
    calls = []

    class Bridge:
        timeout = 20

        def read(self, name, args):
            calls.append(name)
            clock[0] += 2
            return {"data": {"results": []}}

    @contextmanager
    def provider(*args):
        yield type("Provider", (), {"bridge": Bridge()})()

    monkeypatch.setattr(opportunities, "open_provider", provider)
    with pytest.raises(ValueError, match="deadline"):
        opportunities.capture(RESEARCH_UNIVERSE, clock=lambda: clock[0], deadline=clock[0] + 1)
    assert calls == ["get_equity_historicals"]


def test_predefined_put_rule_uses_complete_chain_and_nearest_liquid_strike():
    clock = 1791900000
    c = {
        "id": "near",
        "chain_id": "chain",
        "chain_symbol": "QQQ",
        "underlying_type": "equity",
        "type": "put",
        "expiration_date": "2026-10-27",
        "state": "active",
        "tradability": "tradable",
        "strike_price": "100",
        "trade_value_multiplier": "100",
    }

    class Bridge:
        timeout = 20

        def read(self, name, args):
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
                                "expiration_dates": ["2026-10-14", "2026-10-27", "2026-11-20"],
                            }
                        ]
                    }
                }
            if name == "get_option_instruments":
                return {"data": {"instruments": [c, {**c, "id": "far", "strike_price": "110"}]}}
            return {
                "data": {
                    "results": [
                        {
                            "quote": {
                                "instrument_id": cid,
                                "updated_at": "2026-10-13T14:00:00+00:00",
                                "bid_price": "1.95",
                                "ask_price": "2",
                                "bid_size": 10,
                                "ask_size": 10,
                                "volume": 100,
                                "open_interest": 1000,
                            }
                        }
                        for cid in args["instrument_ids"]
                    ]
                }
            }

    # Use exact quote time; expiry is a predefined 14-day choice.
    from tradeagent.research.domain import timestamp

    clock = timestamp("2026-10-13T14:00:00+00:00")
    result = PutReader(Bridge(), lambda: clock).select("QQQ", 100)
    assert result["book"]["contract_id"] == "near"
    assert result["book"]["expiration"] == "2026-10-27"


def test_new_pipeline_signed_resolution_and_put_unavailability_preserves_frozen_protocols(tmp_path):
    day = date(2026, 10, 12)
    clock = [daily.window(day, bidirectional.PROTOCOL)[0]]
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(json.dumps(profile()))
    start = clock[0]
    baseline = data_for(day, start)
    for b in baseline["bars"]:
        b["volume"] *= 100
        if b["symbol"] != "SPY":
            b["open"], b["close"], b["high"], b["low"] = (
                200 - b["open"],
                200 - b["close"],
                200 - b["low"],
                200 - b["high"],
            )
    for symbol, q in baseline["quotes"].items():
        if symbol != "SPY":
            q.update(bid=98.7, ask=98.72)
    for ref in baseline["references"].values():
        ref["previous_close"] = 101

    def collector(symbols, provider, helper, session=None, deadline=None):
        data = copy.deepcopy(baseline)
        if clock[0] > start:
            for symbol in data["symbols"]:
                price = 100
                for t in range(int(start), int(clock[0]), 60):
                    data["bars"].append(
                        {
                            "symbol": symbol,
                            "start": t,
                            "end": t + 60,
                            "available_at": clock[0],
                            "open": price,
                            "high": price + 0.01,
                            "low": price - 0.02,
                            "close": price - 0.01,
                            "volume": 100000,
                        }
                    )
                    price -= 0.01
        data["observed_at"] = clock[0]
        for q in data["quotes"].values():
            q.update(asof=clock[0], observed_at=clock[0])
        return data

    @contextmanager
    def missing_options(*args):
        raise ValueError("synthetic missing option quotes")
        yield

    old_v1, old_v2 = identity(daily.CONFIG), identity(daily.NEWS_CONFIG)
    pipeline = daily.Pipeline(
        tmp_path / "state",
        collector=collector,
        news_assessor=news_assessment(["bearish"], price_stances=["bearish"]),
        clock=lambda: clock[0],
        evidence_kind="synthetic",
        protocol=bidirectional.PROTOCOL,
        universe_profile=profile_file,
        put_reader_factory=missing_options,
    )
    try:
        result = pipeline.tick()
        assert result["run"]["status"] == "COMPLETED"
        decision = pipeline.store.decision()
        assert decision["comparisons"]["quant_only"]["shadow_direction"] == -1
        assert len(decision["comparison_universe"]) <= 3
        assert decision["selected_plan"] is None
        clock[0] = decision["outcome_exit_time"] + 60
        resolved = pipeline.tick()
        o = pipeline.store.records("outcomes")[0]
        assert o["comparisons"]["quant_only"]["directional_benchmark"]["direction"] == -1
        assert o["comparisons"]["quant_only"]["instrument_simulation"]["status"] == "UNAVAILABLE"
        assert pipeline.store.prior_rows() == []
        assert (
            resolved["comparison"]["evidence_pools"]["synthetic"]["comparisons"][
                "instrument_simulation"
            ]["arms"]["quant_only"]["paired_days"]
            == 0
        )
        assert identity(daily.CONFIG) == old_v1 and identity(daily.NEWS_CONFIG) == old_v2
        assert resolved["orders_submitted"] == 0
    finally:
        pipeline.store.close()


def test_prospective_put_quotes_resolve_without_future_contract_choice(tmp_path):
    from tradeagent.research.opportunity_workflow import DailyResearch

    selection = quote(1791900000)
    selection["contract"] = {"id": "put"}
    store = DailyResearch(tmp_path)
    arms = bidirectional.select(
        [candidate()],
        assessment("bearish"),
        assessment("bearish"),
        "VALID",
        "VALID",
        "seed",
        1791900000,
    )
    decision = {
        "decision_id": "d",
        "research_protocol": bidirectional.PROTOCOL,
        "put_selections": {"QQQ": selection},
        "outcome_entry_time": 1791900300,
        "outcome_exit_time": 1791903900,
        "comparisons": arms,
        "quantitative_observations": [candidate(), {**candidate(), "symbol": "SPY"}],
        "evidence_kind": "synthetic",
    }

    class Reader:
        def quotes(self, contracts):
            assert contracts == [{"id": "put"}]
            return [quote(now[0], bid=2.5, ask=2.55) if now[0] == 1791903900 else quote(now[0])]

    now = [1791900300]
    try:
        store.save("decisions", decision, "d", 1791900000)
        bidirectional.observe_options(store, Reader(), now[0])
        bidirectional.observe_options(store, Reader(), now[0])
        now[0] = 1791903900
        bidirectional.observe_options(store, Reader(), now[0])
        observations = store.records("option_observations")
        assert len(observations) == 2
        underlying = {
            s: {"gross": -0.02, "entry_price": 100, "exit_price": 98} for s in ("QQQ", "SPY")
        }
        resolved = bidirectional.resolve_arms(decision, underlying, observations)
        assert resolved["quant_only"]["instrument_simulation"]["pnl_dollars"] == 250
        assert resolved["quant_only"]["instrument_simulation"]["contracts"] == 5
        assert resolved["quant_only"]["directional_benchmark"]["pnl_dollars"] == pytest.approx(
            18.02
        )
        assert resolved["cash"]["instrument_simulation"]["pnl_dollars"] == 0
        store.save(
            "outcomes",
            {"decision_id": "d", "comparisons": resolved, "evidence_kind": "synthetic"},
            "d",
            now[0] + 60,
        )
        report = bidirectional.compare(store)["evidence_pools"]["synthetic"]["comparisons"][
            "instrument_simulation"
        ]
        assert "seeded_random" not in report["arms"]
        assert (
            report["paired_differences"]["quant_only"]["cash"]["mean_pnl_difference_dollars"] == 250
        )
    finally:
        store.close()


def test_missed_put_quote_window_is_unavailable_without_late_read(tmp_path):
    from tradeagent.research.opportunity_workflow import DailyResearch

    selection = quote(1791900000)
    selection["contract"] = {"id": "put"}
    store = DailyResearch(tmp_path)

    class Reader:
        def quotes(self, contracts):
            pytest.fail("missed observation cannot be backfilled")

    try:
        store.save(
            "decisions",
            {
                "decision_id": "d",
                "research_protocol": bidirectional.PROTOCOL,
                "put_selections": {"QQQ": selection},
                "outcome_entry_time": 1791900300,
                "outcome_exit_time": 1791903900,
            },
            "d",
            1791900000,
        )
        bidirectional.observe_options(store, Reader(), 1791903960)
        assert all(q["status"] == "UNAVAILABLE" for q in store.records("option_observations"))
    finally:
        store.close()


@pytest.mark.parametrize("row", [None, {"quote": None}])
def test_nullable_quote_rows_record_unavailable_endpoint(tmp_path, row):
    from tradeagent.research.opportunity_workflow import DailyResearch
    from tradeagent.research.put_shadow import OptionsResearchMCP

    bridge = OptionsResearchMCP(lambda: pytest.fail("no authentication in regression tests"))
    response = {"data": {"results": [row]}, "guide": "synthetic"}
    bridge.contracts.validate("get_option_quotes", response, "outputSchema")
    bridge.read = lambda name, arguments: response
    reader = PutReader(bridge, lambda: 1791900300)
    contract = {"id": "put"}
    assert reader.quotes([contract]) == []
    selection = {**quote(1791900000), "contract": contract}
    store = DailyResearch(tmp_path)
    try:
        store.save(
            "decisions",
            {
                "decision_id": "nullable",
                "research_protocol": bidirectional.PROTOCOL,
                "put_selections": {"QQQ": selection},
                "outcome_entry_time": 1791900300,
                "outcome_exit_time": 1791903900,
            },
            "nullable",
            1791900000,
        )
        bidirectional.observe_options(store, reader, 1791900300)
        saved = store.records("option_observations")
        assert len(saved) == 1
        assert saved[0]["status"] == "UNAVAILABLE" and saved[0]["quote"] is None
        assert (
            put_outcome(selection, saved[0]["quote"], quote(1791903900), 1791900300, 1791903900)[
                "net_return"
            ]
            is None
        )
        # Null rows must not discard a usable quote elsewhere in the same response.
        response["data"]["results"].append(
            {
                "quote": {
                    "instrument_id": "put",
                    "updated_at": "2026-10-13T14:00:00+00:00",
                    "bid_price": "1.95",
                    "ask_price": "2",
                    "bid_size": 10,
                    "ask_size": 10,
                    "volume": 100,
                    "open_interest": 1000,
                }
            }
        )
        reader.clock = lambda: 1791900300
        valid = reader.quotes(
            [
                {
                    **contract,
                    "chain_symbol": "QQQ",
                    "type": "put",
                    "expiration_date": "2026-11-20",
                    "strike_price": "100",
                    "trade_value_multiplier": "100",
                }
            ]
        )
        assert len(valid) == 1 and valid[0]["book"]["contract_id"] == "put"
    finally:
        store.close()


@pytest.mark.parametrize("error_type", [ValueError, daily.Halt])
@pytest.mark.parametrize("exhaust_deadline", [False, True])
def test_put_discovery_continues_per_candidate_until_shared_deadline(
    tmp_path, monkeypatch, error_type, exhaust_deadline
):
    from types import SimpleNamespace

    from tradeagent.research import alpha_signals

    clock = [100]
    calls = []

    @contextmanager
    def factory(*args):
        yield None

    class Reader:
        def __init__(self, bridge, clock):
            pass

        def select(self, symbol, spot, *, deadline):
            calls.append((symbol, deadline))
            if symbol == "AAPL":
                clock[0] += 10 if exhaust_deadline else 1
                raise error_type("synthetic chain/quote failure")
            return {"symbol": symbol}

    monkeypatch.setattr(daily, "PutReader", Reader)
    monkeypatch.setattr(daily, "research_snapshot", lambda *args: SimpleNamespace(bid=100, ask=101))
    monkeypatch.setattr(
        alpha_signals,
        "AlphaStrategy",
        lambda *args: SimpleNamespace(predict=lambda *args: SimpleNamespace(direction=-1)),
    )
    pipeline = daily.Pipeline(
        tmp_path, clock=lambda: clock[0], put_reader_factory=factory, evidence_kind="synthetic"
    )
    try:
        selections = pipeline.select_puts(
            {"candidates": [{"symbol": s} for s in ("AAPL", "QQQ", "IWM")]}, {}, 110
        )
        assert selections["AAPL"] is None
        if exhaust_deadline:
            assert calls == [("AAPL", 110)] and set(selections) == {"AAPL"}
        else:
            assert calls == [("AAPL", 110), ("QQQ", 110), ("IWM", 110)]
            assert selections["QQQ"] == {"symbol": "QQQ"}
            assert selections["IWM"] == {"symbol": "IWM"}
    finally:
        pipeline.store.close()


def test_individual_results_remain_visible_without_complete_case_pairs(tmp_path):
    from tradeagent.research.opportunity_workflow import DailyResearch

    known = {"status": "AVAILABLE", "net_return": 0.01, "pnl_dollars": 10}
    comparisons = {
        name: {"direction": 1, "directional_benchmark": known, "instrument_simulation": known}
        for name in bidirectional.ARMS
    }
    comparisons["codex_news"] = {
        "direction": -1,
        "directional_benchmark": known,
        "instrument_simulation": {"status": "UNAVAILABLE", "net_return": None, "pnl_dollars": None},
    }
    store = DailyResearch(tmp_path)
    try:
        store.save(
            "decisions",
            {
                "decision_id": "partial",
                "research_protocol": bidirectional.PROTOCOL,
                "evidence_kind": "synthetic",
            },
            "partial",
            100,
        )
        store.save(
            "outcomes",
            {"decision_id": "partial", "evidence_kind": "synthetic", "comparisons": comparisons},
            "partial",
            200,
        )
        report = bidirectional.compare(store)["evidence_pools"]["synthetic"]["comparisons"]
        instruments = report["instrument_simulation"]
        quant = instruments["arms"]["quant_only"]
        assert quant["observed_days"] == 1 and quant["observed_mean_net_return"] == 0.01
        assert quant["observed_mean_pnl_dollars"] == 10
        assert quant["paired_days"] == 0 and quant["mean_net_return"] is None
        missing = instruments["arms"]["codex_news"]
        assert missing["observed_days"] == 0 and missing["observed_mean_net_return"] is None
        assert missing["observed_mean_pnl_dollars"] is None and missing["unavailable_days"] == 1
        paired = instruments["paired_differences"]["quant_only"]["cash"]
        assert paired["paired_days"] == 0
        assert (
            paired["mean_net_return_difference"] is None
            and paired["mean_pnl_difference_dollars"] is None
        )
        assert report["directional_benchmark"]["arms"]["quant_only"]["paired_days"] == 1
    finally:
        store.close()
