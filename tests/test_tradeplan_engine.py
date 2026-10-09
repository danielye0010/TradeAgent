from dataclasses import asdict, replace
from decimal import Decimal

import pytest

from tradeagent.model import Halt
from tradeagent.research.alpha_data import ResearchBars
from tradeagent.research.alpha_experiment import adaptive_choice, choose_validation
from tradeagent.research.alpha_signals import AlphaStrategy, prior_beta
from tradeagent.research.domain import Bar, MarketSnapshot, OptionBook, Payload
from tradeagent.research.option_economics import debit_spread, long_premium
from tradeagent.research.tradeplan import (
    COSTS,
    Costs,
    build_plan,
    economic_evidence,
    to_execution_intent,
)


def snapshot(kind="prospective"):
    now = 1788793200.0

    def bars(symbol, opening):
        return tuple(
            Bar(
                symbol,
                now - 1800 + i * 300,
                now - 1500 + i * 300,
                now - 1500 + i * 300,
                opening + i * 0.1,
                opening + i * 0.1 + 0.11,
                opening + i * 0.1 - 0.01,
                opening + i * 0.1 + 0.1,
                10000,
            )
            for i in range(6)
        )

    return MarketSnapshot(
        "QQQ",
        now,
        "fixture",
        kind,
        "SPY",
        bars("QQQ", 100),
        bars("SPY", 200),
        100.59,
        100.61,
        now,
        now,
        100,
        99,
        (),
        now - 1800,
        now - 86400,
    )


def forecast(snap):
    return AlphaStrategy("opening_continuation").predict(snap, snap.decision_time)


def evidence(p, count=25, gross=0.004):
    return [
        {
            "prediction_id": str(i),
            "strategy": p.strategy_id,
            "version": p.strategy_version,
            "symbol": p.symbol,
            "horizon": p.horizon,
            "decision_time": p.decision_time - (i + 1) * 86400,
            "resolved_at": p.decision_time - (i + 1) * 86400 + p.horizon + 300,
            "evidence_kind": p.context.value["evidence_kind"],
            "source": p.context.value["source"],
            "decision_offset": p.features.value.get("decision_offset"),
            "entry_delay_seconds": p.features.value.get("entry_delay_seconds", 0),
            "regime": p.context.value["regime"],
            "active": True,
            "gross": gross,
        }
        for i in range(count)
    ]


def test_costs_use_both_sides_and_reject_invalid_numbers():
    assert Costs(1, 1, 0.1).net(0.01) == pytest.approx(1.01 * 0.99979 / 1.00021 - 1)
    assert COSTS["stress"].net(0.0005) < 0
    for x in (float("nan"), float("inf"), -1, True):
        with pytest.raises(ValueError):
            Costs(x)


def test_prior_evidence_cannot_leak_or_cross_pool_version_horizon():
    p = forecast(snapshot())
    rows = evidence(p)
    polluted = [
        dict(rows[0], prediction_id="future", resolved_at=p.decision_time),
        dict(rows[0], prediction_id="pool", evidence_kind="replay"),
        dict(rows[0], prediction_id="version", version="other"),
        dict(rows[0], prediction_id="horizon", horizon=7200),
        dict(rows[0], prediction_id="symbol", symbol="IWM"),
        dict(rows[0], prediction_id="offset", decision_offset=90),
    ]
    assert economic_evidence(p, rows + polluted) == economic_evidence(p, rows)


def test_same_day_counts_once_and_duplicates_rejected():
    p = forecast(snapshot())
    rows = evidence(p, 1)
    other = dict(rows[0], prediction_id="distinct", gross=-0.002)
    result = economic_evidence(p, rows + [other])
    assert result["days"] == 1
    assert result["mean_gross"] == pytest.approx(0.001)
    with pytest.raises(ValueError, match="duplicate"):
        economic_evidence(p, rows * 2)


def test_no_trade_for_insufficient_or_cost_erased_evidence():
    snap = snapshot()
    p = forecast(snap)
    for rows in ([], evidence(p, 19), evidence(p, gross=0.0005)):
        plan = build_plan(p, snap, rows)
        assert plan.decision.kind == "NO_TRADE"
        with pytest.raises(Halt):
            to_execution_intent(plan, 1, snap.decision_time)
    assert build_plan(p, snap, evidence(p), selected=False).decision.kind == "NO_TRADE"


def test_valid_plan_to_existing_intent_keeps_price_cap_and_no_broker():
    snap = snapshot()
    p = forecast(snap)
    plan = build_plan(p, snap, evidence(p))
    assert plan.decision.kind == "UNDERLYING"
    assert plan.economics.value["max_loss_fraction"] == 1
    intent = to_execution_intent(plan, Decimal("2"), snap.decision_time + 1)
    assert intent.payload()["symbol"] == "QQQ"
    assert intent.quantity == 2
    assert intent.limit_price <= Decimal(str(plan.decision.entry_limit))
    with pytest.raises(Halt):
        to_execution_intent(plan, "0.5", snap.decision_time)
    with pytest.raises(Halt):
        to_execution_intent(plan, "1", plan.entry_deadline)


def test_historical_plan_can_never_be_adapted():
    snap = snapshot("replay")
    p = forecast(snap)
    plan = build_plan(p, snap, evidence(p))
    assert plan.research_only
    with pytest.raises(Halt, match="research-only"):
        to_execution_intent(plan, 1, snap.decision_time)


def test_snapshot_identity_and_bearish_rejected():
    snap = snapshot()
    p = forecast(snap)
    with pytest.raises(ValueError, match="mismatch"):
        build_plan(p, replace(snap, ask=snap.ask + 0.01), evidence(p))
    bearish = replace(p, direction=-1, expected_return=-0.01)
    assert build_plan(bearish, snap, evidence(bearish)).decision.kind == "NO_TRADE"


def test_planned_delay_preserves_signal_but_requires_real_quote_refresh():
    snap = snapshot()
    p = forecast(snap)
    p = replace(p, features=Payload.of({**p.features.plain(), "entry_delay_seconds": 300}))
    plan = build_plan(p, snap, evidence(p))
    assert plan.decision.kind == "UNDERLYING"
    assert plan.entry_after == snap.decision_time + 300
    with pytest.raises(Halt, match="fresh quote"):
        to_execution_intent(plan, 1, plan.entry_after)
    quote = {
        "asof": plan.entry_after,
        "observed_at": plan.entry_after,
        "ask": plan.decision.entry_limit,
    }
    assert to_execution_intent(
        plan, "0.5", plan.entry_after, order_type="market", quote=quote
    ).quantity == Decimal("0.5")


def test_regime_selector_uses_prior_active_evidence_and_abstains():
    p = forecast(snapshot())
    selected, _ = adaptive_choice([p], evidence(p))
    assert selected == p.strategy_id
    assert adaptive_choice([p], evidence(p, gross=-0.002))[0] is None
    assert adaptive_choice([p], evidence(p, count=9))[0] is None
    mismatched = [dict(r, regime="different") for r in evidence(p)]
    assert adaptive_choice([p], mismatched, regime_specific=True)[0] is None


def test_beta_is_estimated_on_supplied_prior_pairs():
    assert prior_beta([(i * 0.001, i * 0.002) for i in range(20)]) == pytest.approx(2)
    assert prior_beta([(i, 9 * i) for i in range(20)]) == 3
    assert prior_beta([(1, 2)]) == 1


def test_public_bar_input_missing_and_duplicate_fail_closed():
    snap = snapshot()
    dataset = {
        "metadata": {"source": "fixture", "interval_seconds": 300, "adjustment": "raw"},
        "bars": [asdict(b) for b in snap.bars],
    }
    history = ResearchBars(dataset)
    assert (
        len(history.exact("QQQ", snap.decision_time - 1800, snap.decision_time, snap.decision_time))
        == 6
    )
    with pytest.raises(ValueError, match="incomplete"):
        history.exact(
            "QQQ", snap.decision_time - 1800, snap.decision_time + 300, snap.decision_time + 300
        )
    with pytest.raises(ValueError, match="duplicate"):
        ResearchBars({**dataset, "bars": dataset["bars"] + dataset["bars"][:1]})


def test_options_pay_real_spreads_and_four_spread_fees():
    a = OptionBook("long", "QQQ", "call", "2026-09-18", 500, 1788793200, 1788793200, 2.0, 2.2)
    b = replace(a, asof=a.asof + 3600, available_at=a.asof + 3600, bid=2.1, ask=2.3)
    assert long_premium(a, b)["pnl"] == pytest.approx(-11.3)
    short = replace(a, contract_id="short", strike=505, bid=1, ask=1.2)
    short_exit = replace(short, asof=b.asof, available_at=b.asof, bid=0.8, ask=1)
    spread = debit_spread(a, short, b, short_exit)
    assert spread["entry_debit"] == pytest.approx(120)
    assert spread["pnl"] == pytest.approx(-12.6)
    with pytest.raises(ValueError, match="contract mismatch"):
        long_premium(a, replace(b, contract_id="other"))
    with pytest.raises(ValueError, match="synchronized"):
        debit_spread(
            a,
            short,
            b,
            replace(short_exit, asof=short_exit.asof + 1, available_at=short_exit.available_at + 1),
        )


def test_final_test_returns_cannot_change_validation_choice():
    protocol = {"train_end": "2026-08-14", "validation_end": "2026-09-01", "test_end": "2026-09-19"}
    rows = [
        {
            "date": f"2026-08-{day}",
            "active": True,
            "configuration": "opening_continuation:30:60",
            "strategy": "opening_continuation",
            "gross": 0.002,
            "adverse": -0.001,
            "residual_gross": 0.001,
        }
        for day in range(17, 22)
    ]
    selected = choose_validation(rows, protocol)
    assert selected == "opening_continuation:30:60"
    for gross in (-0.99, 10.0):
        assert (
            choose_validation(rows + [dict(rows[0], date="2026-09-02", gross=gross)], protocol)
            == selected
        )


def test_quote_age_does_not_shorten_signal_window():
    snap = snapshot()
    snap = replace(
        snap, quote_time=snap.decision_time - 100, quote_available_at=snap.decision_time - 100
    )
    p = forecast(snap)
    plan = build_plan(p, snap, evidence(p))
    assert plan.entry_deadline == snap.decision_time + 120
    with pytest.raises(Halt):
        to_execution_intent(plan, 1, snap.decision_time + 21)


def test_experiment_delays_entry_and_future_data_cannot_change_signal():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from tradeagent.research.alpha_experiment import evaluate

    bars = []
    for day in (3, 4, 5):
        start = datetime(2026, 8, day, 9, 30, tzinfo=ZoneInfo("America/New_York")).timestamp()
        for symbol, scale in (("QQQ", 1), ("IWM", 2), ("SPY", 3)):
            for i in range(78):
                value = scale * 100 + day + i * 0.03
                bars.append(
                    asdict(
                        Bar(
                            symbol,
                            start + i * 300,
                            start + (i + 1) * 300,
                            start + (i + 1) * 300,
                            value,
                            value + 0.04,
                            value - 0.01,
                            value + 0.03,
                            1000,
                        )
                    )
                )
    dataset = {
        "metadata": {"source": "fixture", "interval_seconds": 300, "adjustment": "raw"},
        "bars": bars,
    }
    protocol = {
        "symbols": ["QQQ", "IWM"],
        "decision_minutes_after_open": [30, 90],
        "holding_minutes": [30, 60, 120],
        "entry_delay_minutes": 5,
        "supplemental_end": "2026-08-06",
    }
    rows, skipped, plans, choices = evaluate(ResearchBars(dataset), protocol)
    assert len(skipped) == 2
    first = next(r for r in rows if r["configuration"] == "opening_continuation:30:60")
    assert first["entry_time"] == first["decision_time"] + 300
    assert first["exit_time"] == first["decision_time"] + first["horizon"]
    assert first["exit_time"] - first["entry_time"] == 3600
    # Changing only this day's future prices must not change its earlier forecast or selection.
    changed = []
    for b in bars:
        b = dict(b)
        if first["entry_time"] <= b["start"] < first["decision_time"] + 6 * 3600:
            for key in ("open", "high", "low", "close"):
                b[key] *= 1.1
        changed.append(b)
    altered, _, _, altered_choices = evaluate(ResearchBars({**dataset, "bars": changed}), protocol)
    other = next(r for r in altered if r["prediction_id"] == first["prediction_id"])
    assert other["expected"] == first["expected"]
    assert other["active"] == first["active"]
    assert [c for c in choices if c["date"] == first["date"]] == [
        c for c in altered_choices if c["date"] == first["date"]
    ]


def test_adapter_intent_passes_existing_risk_and_does_not_bypass_capital():
    from tradeagent.account_policy import POLICY
    from tradeagent.model import Config, Risk, Snapshot
    from tradeagent.risk import check_order

    market = snapshot()
    p = forecast(market)
    intent = to_execution_intent(build_plan(p, market, evidence(p)), 1, market.decision_time)
    d = Decimal
    account = Snapshot(
        "synthetic",
        market.decision_time,
        d(10000),
        d(10000),
        d(10000),
        {},
        {},
        {"QQQ": d("100.60")},
        {"QQQ": market.decision_time},
        {"QQQ": d("100.61")},
        {"QQQ": d("100.59")},
        [],
        d(0),
        tradable={"QQQ": True},
        bid_times={"QQQ": market.decision_time},
        ask_times={"QQQ": market.decision_time},
        regular_session=True,
        agentic_eligible=True,
        account_policy=POLICY,
        liquidity={"QQQ": {"asof": market.decision_time, "bid_size": 10000, "ask_size": 10000}},
    )
    check_order(intent, account, Config(), Risk(), market.decision_time, 10000, 0, 0)
    with pytest.raises(Halt):
        check_order(
            replace(intent, quantity=d(1000)),
            account,
            Config(),
            Risk(),
            market.decision_time,
            10000,
            0,
            0,
        )
