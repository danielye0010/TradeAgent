"""Offline daily research, frozen comparisons and the existing owner sizing boundary."""

import json
import sqlite3
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

import pytest
from test_tradeplan_engine import evidence, forecast, snapshot

from tradeagent.model import Config, Halt, Risk
from tradeagent.oneshot import paper_snapshot
from tradeagent.opportunity_cli import main
from tradeagent.research.domain import Bar, Payload
from tradeagent.research.opportunities import research_snapshot, scan
from tradeagent.research.opportunity_workflow import DailyResearch, selected_execution
from tradeagent.research.tradeplan import (
    build_plan,
    economic_evidence,
    execution_handoff,
    plan_dict,
    plan_from_dict,
    to_execution_intent,
)
from tradeagent.simulator import SimClock

NOW = 1788793200.0


def market(kind="synthetic", end=NOW):
    bars = []
    for symbol, opening, increment in (("QQQ", 100, 0.04), ("IWM", 100, 0), ("SPY", 200, 0.01)):
        for i in range(30):
            value = opening + i * increment
            bars.append(
                asdict(
                    Bar(
                        symbol,
                        end - 1800 + i * 60,
                        end - 1740 + i * 60,
                        end,
                        value,
                        value + increment + 0.01,
                        value - 0.01,
                        value + increment,
                        1000 if i < 15 else 3000,
                    )
                )
            )
    return {
        "schema_version": 1,
        "source": "explicit-synthetic-minute-v1",
        "evidence_kind": kind,
        "observed_at": end,
        "session_open": NOW - 1800,
        "session_close": NOW + 14400,
        "interval_seconds": 60,
        "symbols": ["QQQ", "IWM", "SPY"],
        "bars": bars,
        "quotes": {
            s: {"bid": 101.28, "ask": 101.3, "asof": end, "observed_at": end}
            for s in ("QQQ", "IWM", "SPY")
        },
        "references": {
            s: {"previous_close": 99, "previous_close_time": NOW - 86400}
            for s in ("QQQ", "IWM", "SPY")
        },
        "limitations": [],
    }


def assessment(report, *, stance="long", published=NOW - 60):
    return {
        "scan_id": report["scan_id"],
        "symbol": "QQQ",
        "observed_at": NOW,
        "stance": stance,
        "rank": 1,
        "thesis": "Measured relative strength merits investigation",
        "catalyst": "Explicit synthetic test catalyst",
        "priced_in": "Known headline may be reflected",
        "contradictions": ["Uncalibrated qualitative evidence"],
        "invalidation": "Fresh ask exceeds original cap or benchmark-relative strength fades",
        "sources": [
            {
                "url": "https://example.com/synthetic",
                "title": "Synthetic fixture",
                "published_at": published,
                "observed_at": NOW,
                "claim": "Synthetic test fact",
                "role": "supporting",
            }
        ],
        "model": "explicit-test-fixture",
    }


def store_scan(tmp_path, dataset):
    store = DailyResearch(tmp_path)
    report = scan(dataset, now=NOW, candidate_count=3)
    store.save("scans", report, report["scan_id"], NOW)
    return store, report


def test_scanner_retains_pool_and_synchronized_observations():
    report = scan(market(), now=NOW, window_minutes=30, candidate_count=1)
    assert len(report["candidate_pool"]) == 3 and len(report["candidates"]) == 1
    qqq = next(r for r in report["candidate_pool"] if r["symbol"] == "QQQ")
    assert qqq["features"]["relative_strength"] > 0.005
    assert qqq["features"]["volume_ratio"] == 3
    assert "unusual_volume" in qqq["categories"]
    assert "not expected trading return" in report["interpretation"]
    assert report["status"] == "RESEARCH_ONLY"


def test_missing_benchmark_and_volume_are_omitted_not_fabricated():
    dataset = market()
    dataset["bars"] = [b for b in dataset["bars"] if b["symbol"] != "SPY"]
    for b in dataset["bars"]:
        b["volume"] = 0
    row = scan(dataset, now=NOW)["candidates"][0]
    assert "relative_strength" not in row["features"] and "volume_ratio" not in row["features"]
    assert any("SPY" in text for text in row["limitations"])


def test_future_overlap_and_partial_signal_paths_are_rejected():
    dataset = market()
    dataset["bars"][0]["available_at"] += 1
    with pytest.raises(ValueError, match="future"):
        scan(dataset, now=NOW)
    dataset = market()
    dataset["bars"].append(dataset["bars"][0])
    with pytest.raises(ValueError, match="overlapping"):
        scan(dataset, now=NOW)
    dataset = market()
    dataset["bars"].pop(4)
    with pytest.raises(ValueError, match="incomplete"):
        research_snapshot(dataset, "QQQ", NOW)


def test_real_historical_quotes_cannot_authorize_a_current_snapshot():
    with pytest.raises(ValueError, match="historical"):
        research_snapshot(market("historical_market"), "QQQ", NOW)
    with pytest.raises(ValueError):
        research_snapshot(market("prospective"), "QQQ", NOW + 121)


def test_verified_events_can_shortlist_without_price_anomaly():
    dataset = market()
    dataset["references"] = {}
    for b in dataset["bars"]:
        if b["symbol"] == "IWM":
            b["volume"] = 1000
    event = {
        "symbol": "IWM",
        "url": "https://example.com/fixture",
        "claim": "Verified fixture event",
        "published_at": NOW - 10,
        "observed_at": NOW,
        "verified": True,
    }
    result = scan(dataset, now=NOW, events=[event], candidate_count=3)
    assert (
        "verified_event"
        in next(c for c in result["candidates"] if c["symbol"] == "IWM")["categories"]
    )
    with pytest.raises(ValueError, match="fresh"):
        scan(dataset, now=NOW, events=[dict(event, published_at=NOW - 86401)])


def test_assessment_schema_timestamps_and_append_only_history(tmp_path):
    store, report = store_scan(tmp_path, market())
    try:
        item = assessment(report)
        key = store.assessment(item, NOW)
        assert store.assessment(item, NOW + 1) == key
        with pytest.raises(ValueError, match="frozen"):
            store.assessment(dict(item, thesis="Rewrite losing history"), NOW)
        with pytest.raises(ValueError, match="sizing"):
            store.assessment(dict(item, dollar_amount=500), NOW)
        with pytest.raises(ValueError, match="future publication"):
            store.assessment(assessment(report, published=NOW + 1), NOW)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.db.execute("DELETE FROM opportunity_assessments")
    finally:
        store.close()


def test_plan_roundtrip_delayed_fractional_and_dollar_entries_require_actual_quote():
    snap = snapshot()
    p = forecast(snap)
    p = replace(p, features=Payload.of({**p.features.plain(), "entry_delay_seconds": 300}))
    plan = build_plan(p, snap, evidence(p))
    restored = plan_from_dict(json.loads(json.dumps(plan_dict(plan))))
    assert restored == plan and restored.plan_id == plan.plan_id
    quote = {
        "asof": plan.entry_after,
        "observed_at": plan.entry_after,
        "ask": plan.decision.entry_limit,
    }
    assert to_execution_intent(
        plan, ".5", plan.entry_after, order_type="market", quote=quote
    ).quantity == Decimal(".5")
    assert (
        to_execution_intent(
            plan, None, plan.entry_after, order_type="market", dollar_amount="5", quote=quote
        ).dollar_amount
        == 5
    )
    with pytest.raises(Halt, match="fresh quote"):
        to_execution_intent(plan, 1, plan.entry_after)
    with pytest.raises(Halt, match="price condition"):
        to_execution_intent(
            plan,
            None,
            plan.entry_after,
            order_type="market",
            dollar_amount="5",
            quote=dict(quote, ask=plan.decision.entry_limit + 1),
        )


def test_delayed_handoff_uses_existing_owner_sizing_and_risk_without_broker():
    snap = snapshot()
    p = forecast(snap)
    p = replace(p, features=Payload.of({**p.features.plain(), "entry_delay_seconds": 300}))
    plan = build_plan(p, snap, evidence(p))
    now = plan.entry_after
    account = paper_snapshot(SimClock(now))
    account.fractional_tradable = {"QQQ": True}
    result = execution_handoff(
        plan,
        p,
        account,
        Config(),
        Risk(),
        now,
        "25",
        {"order_type": "market", "dollar_amount": "5"},
    )
    assert result["orders_submitted"] == 0
    assert result["intent"]["dollar_amount"] == "5"
    assert result["provenance"]["decision_time"] == p.decision_time
    with pytest.raises(Halt):
        execution_handoff(
            plan,
            p,
            account,
            Config(),
            Risk(),
            now,
            "25",
            {"order_type": "market", "dollar_amount": "500"},
        )
    account.quote_times["QQQ"] = now - 121
    with pytest.raises(Halt):
        execution_handoff(
            plan,
            p,
            account,
            Config(),
            Risk(),
            now,
            "25",
            {"order_type": "market", "dollar_amount": "5"},
        )


def test_entry_delay_evidence_cannot_cross_delayed_hypotheses():
    p = forecast(snapshot())
    p = replace(p, features=Payload.of({**p.features.plain(), "entry_delay_seconds": 300}))
    rows = evidence(p)
    assert economic_evidence(p, rows)["days"] == 25
    assert economic_evidence(p, [dict(r, source="different-provider") for r in rows])["days"] == 0
    assert economic_evidence(p, [dict(r, entry_delay_seconds=0) for r in rows])["days"] == 0


@pytest.mark.parametrize("published", [None, NOW - 86401])
def test_unproven_news_does_not_support_explicit_event_hypothesis(tmp_path, published):
    store, report = store_scan(tmp_path, market())
    try:
        store.assessment(
            dict(assessment(report, published=published), hypothesis_type="event_driven"), NOW
        )
        result = store.decide(report, market(), now=NOW)
        assert result["final_decision"] == "NO_TRADE"
        assert result["comparisons"]["codex_only"]["shadow_direction"] == 0
        assert any("24 hours" in r for r in result["ranked_candidates"][0]["rejection_reasons"])
        with pytest.raises(ValueError, match="cannot authorize"):
            selected_execution(result, "codex_only")
    finally:
        store.close()


def test_outcomes_use_frozen_common_prices_costs_and_resolve_only_once(tmp_path):
    store, report = store_scan(tmp_path, market())
    try:
        store.assessment(assessment(report), NOW)
        decision = store.decide(report, market(), now=NOW, holding_seconds=1800, delay_seconds=300)
        future = market(end=NOW + 2100)
        # Fill the common full horizon; all symbols and SPY have identical grids.
        for b in list(future["bars"]):
            before = dict(b)
            before["start"] -= 1800
            before["end"] -= 1800
            future["bars"].append(before)
        result = store.resolve(future, NOW + 2100)
        assert len(result["resolved"]) == 1, result
        outcome = result["resolved"][0]
        ai = outcome["comparisons"]["codex_only"]
        assert ai["entry_time"] == NOW + 300 and ai["exit_time"] == NOW + 2100
        assert ai["base_net"] < ai["gross"] and "spy_gross" in ai
        assert outcome["comparisons"]["quant_only"]["base_net"] == 0
        assert store.resolve(future, NOW + 2200)["resolved"] == []
        assert store.decision(decision["decision_id"]) == decision
        assert "paired_mean_net_minus_quant" in store.performance()["evidence_pools"]["synthetic"]
        assert all(r["resolved_at"] > r["decision_time"] for r in store.prior_rows())
    finally:
        store.close()


def test_missing_exact_outcome_endpoint_stays_pending(tmp_path):
    store, report = store_scan(tmp_path, market())
    try:
        store.decide(report, market(), now=NOW, holding_seconds=1800, delay_seconds=300)
        future = market(end=NOW + 2100)
        future["bars"] = [
            b for b in future["bars"] if not (b["symbol"] == "QQQ" and b["start"] == NOW + 300)
        ]
        result = store.resolve(future, NOW + 2100)
        assert result["pending"] and not result["resolved"]
        assert store.records("outcomes") == []
    finally:
        store.close()


def test_cli_manual_workflow_and_skill_calls_repo_tools(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tradeagent.opportunity_cli.time.time", lambda: NOW)
    path = tmp_path / "market.json"
    path.write_text(json.dumps(market()))
    prefix = ["--state-dir", str(tmp_path / "state")]
    assert main([*prefix, "scan", "--input", str(path), "--candidates", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    path.write_text(json.dumps({"assessments": [assessment(report)]}))
    assert main([*prefix, "assess", "--input", str(path)]) == 0
    capsys.readouterr()
    assert main([*prefix, "decide"]) == 0
    decision = json.loads(capsys.readouterr().out)
    assert decision["orders_submitted"] == 0 and decision["final_decision"] == "NO_TRADE"
    assert main([*prefix, "show"]) == 0
    assert json.loads(capsys.readouterr().out)["current_status"] == "NO_TRADE"
    assert main([*prefix, "compare"]) == 0
    assert json.loads(capsys.readouterr().out)["pending_decisions"] == 1
    skill = Path(".agents/skills/trade-opportunity-analyst/SKILL.md").read_text()
    for command in (
        "opportunity scan",
        "opportunity assess",
        "opportunity decide",
        "opportunity show",
        "opportunity compare",
    ):
        assert command in skill
    assert "name: trade-opportunity-analyst" in skill


def test_decisions_do_not_accept_a_different_market_pool(tmp_path):
    store, report = store_scan(tmp_path, market())
    try:
        with pytest.raises(ValueError, match="same frozen"):
            store.decide(report, market("prospective"), now=NOW)
        assert store.records("decisions") == []
    finally:
        store.close()


def test_measured_quant_edge_and_qualitative_rank_combine_without_new_sizing(tmp_path, monkeypatch):
    from tradeagent.research.evidence_cohorts import comparison_context
    from tradeagent.research.opportunity_workflow import prediction_from_dict

    store, report = store_scan(tmp_path, market())
    try:
        store.assessment(assessment(report), NOW)
        initial = store.decide(report, market(), now=NOW)
        rows = [
            dict(r, comparison_context=comparison_context(raw["features"]))
            for c in initial["ranked_candidates"]
            for raw in c["predictions"]
            for r in evidence(prediction_from_dict(raw))
        ]
        # Prediction IDs must remain distinct across strategies in the synthetic prior fixture.
        for i, row in enumerate(rows):
            row["prediction_id"] = str(i)
        monkeypatch.setattr(store, "prior_rows", lambda: rows)
        result = store.decide(report, market(), now=NOW)
        assert result["final_decision"] == "UNDERLYING"
        assert result["comparisons"]["quant_only"]["symbol"] == "QQQ"
        assert result["comparisons"]["quant_codex"]["symbol"] == "QQQ"
        assert result["comparisons"]["codex_only"]["decision"] == "NO_TRADE"
        plan, prediction = selected_execution(result)
        assert plan.forecast.value["horizon"] == prediction.horizon == 3900
        assert plan.research_only and result["orders_submitted"] == 0
        assert "dollar_amount" not in result["selected_plan"]
    finally:
        store.close()


def test_plan_endpoint_reuses_controller_and_survives_restart_without_owner_config_change(
    tmp_path, monkeypatch
):
    from test_production_oneshot import owner_mock_runtime

    from tradeagent.execution_policy import load_live_config
    from tradeagent.oneshot import run_live

    _, sim, calls, target, config_file = owner_mock_runtime(tmp_path, monkeypatch)
    config_file.write_text(
        config_file.read_text()
        + '\n[entry]\norder_type = "limit"\nquantity = "1"\nlimit_price = "12.01"\n'
    )
    original_config = config_file.read_bytes()
    try:
        snap = snapshot()
        offset = sim.clock() - snap.decision_time

        def shifted(bars):
            return tuple(
                replace(
                    b,
                    start=b.start + offset,
                    end=b.end + offset,
                    available_at=b.available_at + offset,
                )
                for b in bars
            )

        snap = replace(
            snap,
            decision_time=sim.clock(),
            bid=11.99,
            ask=12.01,
            bars=shifted(snap.bars),
            benchmark_bars=shifted(snap.benchmark_bars),
            quote_time=sim.clock(),
            quote_available_at=sim.clock(),
            session_open_time=snap.session_open_time + offset,
            previous_close_time=snap.previous_close_time + offset,
        )
        prediction = forecast(snap)
        plan = build_plan(prediction, snap, evidence(prediction))
        settings = load_live_config(config_file)
        before = sim.clock()
        result = run_live(settings, plan=plan, prediction=prediction)
        assert result["status"] == "COMPLETED", result
        assert result["exit_due"] == plan.exit_at == before + 3600
        assert settings.options["hold_seconds"] == 0
        assert not result["execution_canary"] and not result["excluded_from_strategy_performance"]
        count = len(calls)
        recovered = run_live(settings, recover=True)
        assert recovered["duplicate_suppressed"]
        assert recovered["decision"]["provenance"]["economic_plan"]["exit_at"] == plan.exit_at
        assert not recovered["execution_canary"]
        assert not any(
            m.get("params", {}).get("name", "").startswith(("review_", "place_", "cancel_"))
            for m in calls[count:]
        )
        assert config_file.read_bytes() == original_config
        assert target.exists()  # fresh synthetic local state only; HTTP entirely mocked
    finally:
        sim.close()


def test_after_close_actual_observations_resolve_prospective_decisions_without_pool_relabel(
    tmp_path,
):
    store, report = store_scan(tmp_path, market("prospective"))
    try:
        decision = store.decide(
            report, market("prospective"), now=NOW, holding_seconds=1800, delay_seconds=300
        )
        future = market("historical_market", end=NOW + 2100)
        result = store.resolve(future, NOW + 2100)
        assert len(result["resolved"]) == 1, result
        assert result["resolved"][0]["evidence_kind"] == "prospective"
        assert result["resolved"][0]["observation_evidence_kind"] == "historical_market"
        assert store.decision(decision["decision_id"])["evidence_kind"] == "prospective"
        assert all(row["evidence_kind"] == "prospective" for row in store.prior_rows())
    finally:
        store.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_failed_scan_daily_workflow_is_terminal_and_preserves_original_error(
    tmp_path, monkeypatch, capsys, legacy
):
    import tradeagent.opportunity_cli as cli

    root = tmp_path / "daily"
    prefix = ["--state-dir", str(root)]
    error = "Robinhood market-data contract unavailable or changed: get_equity_quotes"
    monkeypatch.setattr(cli.time, "time", lambda: NOW)

    def unavailable(*args):
        raise Halt(error)

    monkeypatch.setattr(cli, "capture", unavailable)
    if not legacy:
        assert main([*prefix, "scan"]) == 2
        failure = json.loads(capsys.readouterr().out)
        scan_id = failure["scan_id"]
        report = json.loads((root / "latest-candidates.json").read_text())
        assert report["source"] is None
        assert report["evidence_kind"] == "unavailable"
    else:
        scan_id = "legacy-failed-scan"
        report = {
            "schema_version": 1,
            "scan_id": scan_id,
            "decision_time": NOW,
            "status": "INCOMPLETE",
            "candidates": [],
            "candidate_pool": [],
            "universe": ["QQQ", "IWM", "SPY"],
            "limitations": [error],
            "orders_submitted": 0,
        }
        store = DailyResearch(root)
        store.save("scans", report, scan_id, NOW)
        store.close()
        cli.write(root / "latest-candidates.json", report)

    def forbidden(*args, **kwargs):
        pytest.fail("failed scan continued into market collection or economic evaluation")

    monkeypatch.setattr(cli, "capture", forbidden)
    monkeypatch.setattr(DailyResearch, "prior_rows", forbidden)
    empty = tmp_path / "legacy-empty-assessment.json"
    empty.write_text(json.dumps({"scan_id": scan_id, "assessments": []}))
    # A stale latest plan cannot mask a newer collection failure.
    cli.write(root / "latest-plan.json", {"decision_id": "older-view"})
    previous_plan = (root / "latest-plan.json").read_bytes()
    previous_scan = (root / "latest-candidates.json").read_bytes()
    saved = None
    for command in (
        ["evidence", "--template"],
        ["decide", "--refresh"],
        ["assess", "--input", str(empty)],
        ["show"],
        ["decide", "--scan-id", scan_id],
    ):
        assert main([*prefix, *command]) == 2
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "INCOMPLETE"
        assert result["decision"] == "NO_TRADE"
        assert result["failure_kind"] == "MARKET_DATA_UNAVAILABLE"
        assert result["reason"] == error
        assert result["submission_status"] == "NOT_SUBMITTED"
        assert result["orders_submitted"] == 0 and not result["execution_invoked"]
        assert result["failure_id"] == result["invocation_id"] == scan_id
        assert "decision_id" not in result and "selected_plan" not in result
        path = Path(result["invocation_file"])
        assert json.loads(path.read_text()) == result
        if saved is None:
            saved = path.read_bytes()
        assert path.read_bytes() == saved
    assert (root / "latest-plan.json").read_bytes() == previous_plan
    assert (root / "latest-candidates.json").read_bytes() == previous_scan
    assert not (root / "captures").exists()
    assert not (root / "decisions").exists()
    store = DailyResearch(root)
    try:
        # The core API also handles older reports without source, even with no dataset.
        result = store.decide(report, None, now=NOW)
        assert result["reason"] == error and "decision_id" not in result
        assert store.records("scans") == [report]
        for kind in ("assessments", "decisions", "outcomes", "executions"):
            assert store.records(kind) == []
    finally:
        store.close()


def test_unavailable_source_is_not_an_economic_no_trade_decision(tmp_path, monkeypatch):
    store, report = store_scan(tmp_path, market())
    try:
        monkeypatch.setattr(store, "prior_rows", lambda: pytest.fail("economic evaluation"))
        for source in (None, "unavailable"):
            failed = dict(report, source=source, limitations=["market-data source unavailable"])
            result = store.decide(failed, None, now=NOW)
            assert result["status"] == "INCOMPLETE"
            assert result["reason"] == "market-data source unavailable"
            assert "comparisons" not in result and "decision_id" not in result
        assert store.records("decisions") == []
    finally:
        store.close()
