"""Offline opportunity policy regression tests; no account or broker fixtures."""

import copy
import math
import statistics
from dataclasses import replace

import pytest
from test_opportunities import NOW, assessment, market, store_scan
from test_tradeplan_engine import evidence, forecast, snapshot

from tradeagent.research.domain import Payload
from tradeagent.research.evidence_cohorts import (
    CRITICAL,
    POLICY,
    cohort_evidence,
    comparison_context,
    daily_summary,
)
from tradeagent.research.opportunity_workflow import (
    ARMS,
    DailyResearch,
    prediction_from_dict,
    research_support,
)
from tradeagent.research.tradeplan import build_plan, economic_evidence


def economic_fields(result):
    diagnostics = {
        "total_resolved_days",
        "resolved_days_by_pool",
        "same_pool_resolved_days",
        "matching_cohort_days",
        "exclusion_categories",
        "exclusion_counting",
    }
    return {k: v for k, v in result.items() if k not in diagnostics}


def target():
    p = forecast(snapshot())
    return replace(
        p,
        features=Payload.of(
            {
                **p.features.plain(),
                "entry_delay_seconds": 300,
                "decision_offset": 37,
                "observed_spread_bps": 2,
            }
        ),
    )


def peers(p, *, target_days=5, gross=0.004):
    rows = evidence(p, 20, gross)
    result = []
    for i, row in enumerate(rows):
        common = dict(
            row, comparison_context=comparison_context(p.features.plain()), decision_offset=38
        )
        result.append(dict(common, prediction_id=f"peer-{i}", symbol="IWM"))
        if i < target_days:
            result.append(dict(common, prediction_id=f"target-{i}", symbol=p.symbol))
    return result


def test_comparable_peers_accumulate_days_without_exact_minute_or_symbol_match():
    p = target()
    rows = peers(p)
    assert economic_evidence(p, rows)["days"] == 0
    result = cohort_evidence(p, rows)
    assert result["days"] == 20 and result["target_days"] == 5
    assert result["observations"] == 25 and result["sufficient"]
    assert result["critical_value"] == CRITICAL > 1.96
    assert build_plan(p, snapshot(), rows, evidence_policy=POLICY).decision.kind == "UNDERLYING"
    # Default/frozen alpha experiments retain the original exact policy.
    assert build_plan(p, snapshot(), rows).decision.kind == "NO_TRADE"
    assert (
        build_plan(p, snapshot(), evidence(p)).economics.value["stress_side_cost"]
        == (
            build_plan(p, snapshot(), rows, evidence_policy=POLICY).economics.value[
                "stress_side_cost"
            ]
        )
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "other-provider"},
        {"version": "other-strategy-version"},
        {"strategy": "other-family"},
        {"evidence_kind": "historical_market"},
        {"horizon": 7200},
        {"entry_delay_seconds": 0},
        {"decision_offset": 60},
        {"regime": "unrecognized"},
        {"symbol": "AAPL"},
        {"comparison_context": {"opening_rv": 0.02, "observed_spread_bps": 2}},
        {"comparison_context": {"opening_rv": 0.002, "observed_spread_bps": 30}},
        {"comparison_context": {}},
        {"active": False},
    ],
)
def test_incompatible_rows_cannot_inflate_prior_evidence(changes):
    p = target()
    rows = peers(p)
    polluted = [
        dict(r, **changes, prediction_id="bad-" + r["prediction_id"], gross=0.9) for r in rows
    ]
    result = cohort_evidence(p, rows + polluted)
    assert economic_fields(result) == economic_fields(cohort_evidence(p, rows))
    assert sum(result["exclusion_categories"].values()) == len(polluted)


def test_future_late_resolved_and_outside_lookback_are_excluded():
    p = target()
    rows = peers(p)
    invalid = [
        dict(
            rows[0],
            prediction_id="future",
            decision_time=p.decision_time + 1,
            resolved_at=p.decision_time + p.horizon + 1,
            gross=0.9,
        ),
        dict(rows[0], prediction_id="late", resolved_at=p.decision_time, gross=0.9),
        dict(rows[0], prediction_id="old", decision_time=p.decision_time - 181 * 86400, gross=0.9),
    ]
    result = cohort_evidence(p, rows + invalid)
    assert economic_fields(result) == economic_fields(cohort_evidence(p, rows))
    assert result["exclusion_categories"] == {
        "not_strictly_prior": 2,
        "outside_180_day_lookback": 1,
    }
    with pytest.raises(ValueError, match="duplicate"):
        cohort_evidence(p, rows + [rows[0]])


def test_intraday_duplicates_and_more_symbols_do_not_create_independent_days():
    p = target()
    row = peers(p)[0]
    rows = [
        dict(
            row,
            prediction_id=str(i),
            symbol="QQQ" if i % 2 else "IWM",
            gross=-0.002 if i % 2 else 0.004,
        )
        for i in range(40)
    ]
    result = cohort_evidence(p, rows)
    assert result["days"] == result["target_days"] == 1
    assert result["observations"] == 40
    assert result["mean_gross"] == pytest.approx(0.001)
    assert result["target_mean_gross"] == pytest.approx(-0.002)
    assert not result["sufficient"]


def test_target_gate_and_dispersion_prevent_winning_peers_masking_target_losses():
    p = target()
    rows = peers(p)
    negative = [dict(r, gross=-0.002) if r["symbol"] == p.symbol else r for r in rows]
    result = cohort_evidence(p, negative)
    assert result["sufficient"]  # Count alone does not authorize a plan.
    assert result["between_symbol_discount"] > 0
    assert result["lower_gross_estimate"] <= -0.002
    assert build_plan(p, snapshot(), negative, evidence_policy=POLICY).decision.kind == "NO_TRADE"
    for thin in (peers(p, target_days=4), peers(p)[:19]):
        assert not cohort_evidence(p, thin)["sufficient"]
        assert build_plan(p, snapshot(), thin, evidence_policy=POLICY).decision.kind == "NO_TRADE"
    assert (
        build_plan(p, snapshot(), peers(p, gross=0.0005), evidence_policy=POLICY).decision.kind
        == "NO_TRADE"
    )


def test_serial_dependence_increases_uncertainty_without_negative_ac_precision_gain():
    values = [0.001] * 10 + [0.006] * 10
    naive = statistics.stdev(values) / math.sqrt(len(values))
    assert daily_summary(values)["se"] > naive
    alternating = [0.001, 0.006] * 10
    assert daily_summary(alternating)["se"] >= statistics.stdev(alternating) / math.sqrt(20)


def test_single_symbol_assets_and_stock_sectors_do_not_borrow_across_asset_groups():
    p = target()
    for symbol in ("TLT", "GLD", "UNKNOWN", "UBER"):
        changed = replace(p, symbol=symbol)
        assert cohort_evidence(changed, peers(p))["days"] == 0
    aapl = replace(p, symbol="AAPL")
    rows = peers(aapl)
    assert cohort_evidence(aapl, rows)["days"] == 5
    technology = [dict(r, symbol="MSFT") if r["symbol"] == "IWM" else r for r in rows]
    assert cohort_evidence(aapl, technology)["sufficient"]
    energy = [dict(r, symbol="XOM") if r["symbol"] == "IWM" else r for r in rows]
    assert cohort_evidence(aapl, energy)["days"] == 5


@pytest.mark.parametrize("stance", ["long", "watch", "avoid"])
def test_market_only_codex_support_is_optional_and_quant_control_is_independent(tmp_path, stance):
    store, report = store_scan(tmp_path, market())
    try:
        store.assessment(dict(assessment(report, stance=stance), sources=[]), NOW)
        initial = store.decide(report, market(), now=NOW)
        rows = []
        for c in initial["ranked_candidates"]:
            if c["symbol"] == "QQQ":
                for raw in c["predictions"]:
                    p = prediction_from_dict(raw)
                    for r in peers(p):
                        rows.append(dict(r, prediction_id=p.prediction_id + r["prediction_id"]))
        store.prior_rows = lambda: rows
        result = store.decide(report, market(), now=NOW)
        arms = result["comparisons"]
        assert arms["quant_only"]["symbol"] == "QQQ"
        assert arms["quant_only"]["decision"] == "UNDERLYING"
        assert arms["codex_only"]["shadow_direction"] == int(stance == "long")
        assert arms["quant_codex"]["shadow_direction"] == int(stance == "long")
        assert arms["codex_only"]["plan"] is None and arms["codex_only"]["research_only"]
        if stance == "long":
            assert arms["quant_codex"]["plan"] == arms["quant_only"]["plan"]
        else:
            assert arms["quant_codex"]["plan"] is None
        assert result["orders_submitted"] == 0
        assert result["news_is_calibrated_return_prediction"] is False
    finally:
        store.close()


def test_event_thesis_needs_fresh_proof_but_price_thesis_does_not():
    candidate = {"symbol": "QQQ", "categories": ["relative_strength"]}
    item = dict(assessment({"scan_id": "fixture"}), sources=[])
    assert research_support(candidate, item, NOW)["supported"]
    event_item = dict(item, hypothesis_type="event_driven")
    assert not research_support(candidate, event_item, NOW)["supported"]
    verified = dict(
        candidate,
        event_evidence={
            "verified": True,
            "symbol": "QQQ",
            "published_at": NOW - 60,
            "observed_at": NOW,
        },
    )
    assert research_support(verified, event_item, NOW)["supported"]
    assert research_support(
        candidate, dict(assessment({"scan_id": "fixture"}), hypothesis_type="event_driven"), NOW
    )["supported"]
    assert not research_support(dict(candidate, categories=["verified_event"]), item, NOW)[
        "supported"
    ]


def test_legacy_comparability_is_read_from_frozen_forecasts_without_rewriting_history(tmp_path):
    store, report = store_scan(tmp_path / "source", market())
    legacy = DailyResearch(tmp_path / "legacy")
    try:
        decision = store.decide(report, market(), now=NOW, holding_seconds=1800, delay_seconds=300)
        outcome = store.resolve(market(end=NOW + 2100), now=NOW + 2100)["resolved"][0]
        original = copy.deepcopy(outcome)
        original["economic_rows"] = [
            r for o in store.records("symbol_outcomes") for r in o["economic_rows"]
        ]
        decision = copy.deepcopy(decision)
        decision.pop("quantitative_observations")
        for c in decision["ranked_candidates"]:
            for p in c["predictions"]:
                p["features"].pop("sampling_key")
        for r in original["economic_rows"]:
            r.pop("comparison_context")
            r.pop("sampling_key")
        legacy.save("decisions", decision, decision["decision_id"], NOW)
        legacy.save("outcomes", original, decision["decision_id"], NOW + 2100)
        frozen = legacy.records("outcomes")
        rows = legacy.prior_rows()
        assert rows and all(
            r["comparison_context"]["observed_spread_bps"] is not None for r in rows
        )
        assert legacy.records("outcomes") == frozen == [original]
        assert legacy.records("decisions") == [decision]
    finally:
        legacy.close()
        store.close()


def test_paired_reports_separate_old_and_new_frozen_research_protocols(tmp_path):
    store = DailyResearch(tmp_path)
    try:
        for i, policy in enumerate(("exact-v1", POLICY)):
            key = f"explicit-fixture-{i}"
            store.save(
                "decisions",
                {
                    "decision_id": key,
                    "skill_version": f"fixture-v{i}",
                    **({"economic_evidence_policy": policy} if i else {}),
                },
                key,
                NOW + i,
            )
            store.save(
                "outcomes",
                {
                    "decision_id": key,
                    "evidence_kind": "synthetic",
                    "comparisons": {name: {"base_net": 0.001 * (i + 1)} for name in ARMS},
                },
                key,
                NOW + i + 10,
            )
        result = store.performance()["evidence_pools"]["synthetic"]
        assert result["mixed_protocols"]
        assert len(result["protocols"]) == 2
        for protocol in result["protocols"].values():
            assert all(a["decisions"] == 1 for a in protocol["arms"].values())
            assert protocol["paired_mean_net_minus_quant"] == {"codex_only": 0, "quant_codex": 0}
    finally:
        store.close()


@pytest.mark.parametrize("field", ["confidence", "expected_return"])
def test_subjective_forecast_fields_cannot_enter_assessment_schema(tmp_path, field):
    store, report = store_scan(tmp_path, market())
    try:
        with pytest.raises(ValueError, match="invented forecast"):
            store.assessment({**assessment(report), field: 0.99}, NOW)
    finally:
        store.close()


def test_missing_or_stale_target_evidence_cannot_be_refreshed_by_new_peer_days():
    p = target()
    rows = peers(p)
    stale = [
        dict(
            r,
            decision_time=r["decision_time"] - 40 * 86400,
            resolved_at=r["resolved_at"] - 40 * 86400,
        )
        if r["symbol"] == p.symbol
        else r
        for r in rows
    ]
    result = cohort_evidence(p, stale)
    assert result["sufficient"] and result["target_days"] == 5
    assert p.decision_time - result["asof"] > 30 * 86400
    plan = build_plan(p, snapshot(), stale, evidence_policy=POLICY)
    assert plan.decision.kind == "NO_TRADE"
    assert "missing or stale evidence" in plan.rejection_reasons
    missing = replace(p, features=Payload.of({**p.features.plain(), "opening_rv": None}))
    assert not cohort_evidence(missing, rows)["sufficient"]
