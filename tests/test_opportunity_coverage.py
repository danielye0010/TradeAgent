"""Offline full-universe evidence and local LIVE ranking; no broker or owner state."""

import copy
import json
from types import SimpleNamespace

import pytest
from test_evidence_cohorts import peers, target
from test_opportunities import NOW, assessment, market, store_scan

from tradeagent import opportunity_cli as cli
from tradeagent.research.evidence_cohorts import cohort_evidence
from tradeagent.research.opportunities import UNIVERSE, scan
from tradeagent.research.opportunity_workflow import DailyResearch


def entire_market():
    data = market()
    template = [b for b in data["bars"] if b["symbol"] == "QQQ"]
    data["bars"] = [b for b in data["bars"] if b["symbol"] == "SPY"]
    for symbol in UNIVERSE:
        if symbol != "SPY":
            data["bars"].extend(dict(b, symbol=symbol) for b in template)
        data["quotes"][symbol] = dict(data["quotes"]["QQQ"])
        data["references"][symbol] = dict(data["references"]["QQQ"])
    data["symbols"] = list(UNIVERSE)
    return data


def test_full_31_symbol_forecasts_independent_of_shortlist_and_assessment(tmp_path):
    data = entire_market()
    report = scan(data, now=NOW, candidate_count=3)
    store = DailyResearch(tmp_path)
    try:
        store.save("scans", report, report["scan_id"], NOW)
        result = store.decide(report, data, now=NOW, holding_seconds=1800)
        coverage = result["quantitative_observations"]
        assert len(coverage) == 31
        assert sum(len(c["predictions"]) for c in coverage) == 93
        assert len(result["ranked_candidates"]) == 3
        assert all(c["research"] is None for c in coverage)
        assert all(c["predictions"] for c in coverage)
        assert all(
            p["created_at"] == p["decision_time"] == NOW for c in coverage for p in c["predictions"]
        )
        assert result["selected_plan"] is None and result["orders_submitted"] == 0
        assert store.decide(report, data, now=NOW, holding_seconds=1800) == result
        assert len(store.records("decisions")) == 1
        bad = copy.deepcopy(data)
        del bad["quotes"]["MSFT"]
        later = store.decide(report, bad, now=NOW + 1, holding_seconds=1800)
        missing = next(c for c in later["quantitative_observations"] if c["symbol"] == "MSFT")
        assert missing["predictions"] == [] and missing["limitations"]
    finally:
        store.close()


def test_independent_symbol_resolution_keeps_missing_path_pending_and_is_idempotent(tmp_path):
    store, report = store_scan(tmp_path, market())
    try:
        decision = store.decide(report, market(), now=NOW, holding_seconds=1800)
        later = market(end=NOW + 2100)
        missing = copy.deepcopy(later)
        missing["bars"] = [
            b for b in missing["bars"] if not (b["symbol"] == "QQQ" and b["start"] == NOW + 300)
        ]
        first = store.resolve(missing, NOW + 2100)
        assert {o["symbol"] for o in first["symbol_resolved"]} == {"IWM"}
        # SPY's fixed QQQ benchmark also waits for the missing QQQ path.
        assert first["resolved"] == [] and first["pending"]
        assert len(store.prior_rows()) == 3
        assert store.resolve(missing, NOW + 2101)["symbol_resolved"] == []
        second = store.resolve(later, NOW + 2102)
        assert {o["symbol"] for o in second["symbol_resolved"]} == {"QQQ", "SPY"}
        assert len(second["resolved"]) == 1
        assert len(store.prior_rows()) == 9
        assert store.resolve(later, NOW + 2103)["symbol_resolved"] == []
        assert store.decision(decision["decision_id"]) == decision
        assert store.performance()["actual_execution_records"] == 0
        assert all(
            "no broker fills" in o["modeled_outcome"]["price_model"]
            for o in store.records("symbol_outcomes")
        )
    finally:
        store.close()


def test_first_frozen_minute_slot_not_repeats_or_best_outcome_enters_evidence(tmp_path):
    store, report = store_scan(tmp_path, market())
    try:
        first = store.decide(report, market(), now=NOW, holding_seconds=1800)
        second = store.decide(report, market(), now=NOW + 1, holding_seconds=1800)
        # This path covers only the SECOND forecast's exact rounded endpoints.
        future = market(end=NOW + 2160)
        result = store.resolve(future, NOW + 2160)
        assert result["symbol_resolved"]
        assert store.prior_rows() == []  # unresolved first slot cannot be replaced
        older = market(end=NOW + 2100)
        store.resolve(older, NOW + 2161)
        rows = store.prior_rows()
        original_ids = {
            p["prediction_id"] for c in first["quantitative_observations"] for p in c["predictions"]
        }
        assert {r["prediction_id"] for r in rows} == original_ids
        assert len(rows) == 9
        assert first["decision_id"] != second["decision_id"]
    finally:
        store.close()


def test_live_shortlist_uses_existing_allowlist_but_records_broad_opportunities(
    tmp_path, monkeypatch
):
    data = entire_market()
    report = scan(data, now=NOW, candidate_count=3)
    # Three strong broad-market opportunities outrank QQQ/IWM in this fixture.
    for c in report["candidate_pool"]:
        if c["symbol"] in {"UNH", "GS", "AAPL"}:
            c["ranking_score"] = 100
    report["candidates"] = [
        c for c in report["candidate_pool"] if c["symbol"] in {"UNH", "GS", "AAPL"}
    ]
    config = tmp_path / "owner.toml"
    config.write_text("explicit offline config sentinel")
    before = config.read_bytes()
    monkeypatch.setattr(
        "tradeagent.execution_policy.load_live_config",
        lambda path: SimpleNamespace(config=SimpleNamespace(allowed_symbols=["QQQ", "IWM"])),
    )
    aligned = cli.align_live_universe(report, config)
    assert {c["symbol"] for c in aligned["candidates"]} == {"QQQ", "IWM"}
    assert {c["symbol"] for c in aligned["research_candidates"]} == {"UNH", "GS", "AAPL"}
    assert {c["symbol"] for c in aligned["excluded_live_opportunities"]} >= {"UNH", "GS", "AAPL"}
    store = DailyResearch(tmp_path / "research")
    try:
        store.save("scans", aligned, aligned["scan_id"], NOW)
        store.assessment(assessment(aligned), NOW)
        result = store.decide(aligned, data, now=NOW, holding_seconds=1800)
        assert len(result["quantitative_observations"]) == 31
        assert {c["symbol"] for c in result["ranked_candidates"]} == {"QQQ", "IWM"}
        assert result["comparisons"]["codex_only"]["symbol"] == "QQQ"
        assert result["comparisons"]["quant_codex"]["decision"] == "NO_TRADE"
        assert result["execution_universe"] == ["QQQ", "IWM"]
        assert config.read_bytes() == before
    finally:
        store.close()


def test_cli_next_input_scan_automatically_resolves_without_network(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "capture", lambda *a, **k: pytest.fail("network access"))
    monkeypatch.setattr(cli.time, "time", lambda: NOW)
    prefix = ["--state-dir", str(tmp_path / "daily")]
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(market()))
    assert cli.main([*prefix, "scan", "--input", str(path), "--candidates", "1"]) == 0
    capsys.readouterr()
    assert cli.main([*prefix, "decide", "--hold-seconds", "1800"]) == 0
    original = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(cli.time, "time", lambda: NOW + 2100)
    path.write_text(json.dumps(market(end=NOW + 2100)))
    assert cli.main([*prefix, "scan", "--input", str(path)]) == 0
    next_scan = json.loads(capsys.readouterr().out)
    assert next_scan["automatic_resolution"]["resolved_symbols"] == 3
    assert next_scan["automatic_resolution"]["resolved_decisions"] == 1
    store = DailyResearch(tmp_path / "daily")
    try:
        assert len(store.prior_rows()) == 9
        assert store.decision(original["decision_id"]) == original
    finally:
        store.close()


def test_next_day_real_capture_path_requests_only_matured_original_sessions(tmp_path, monkeypatch):
    data = market("prospective")
    store, report = store_scan(tmp_path, data)
    try:
        original = store.decide(report, data, now=NOW, holding_seconds=1800)
        monkeypatch.setattr(cli.time, "time", lambda: NOW + 86400)
        calls = []

        def captured(symbols, provider, helper, *, session):
            calls.append((symbols, provider, session))
            later = market("historical_market", end=NOW + 2100)
            return later

        monkeypatch.setattr(cli, "capture", captured)
        today = market("prospective", end=NOW + 86400)
        today["session_open"] += 86400
        today["session_close"] += 86400
        args = SimpleNamespace(
            input=None, provider="robinhood", oauth_helper=None, state_dir=tmp_path
        )
        result = cli.auto_resolve(store, today, args)
        assert len(calls) == 1 and set(calls[0][0]) == {"QQQ", "IWM", "SPY"}
        assert result["resolved_symbols"] == 3 and result["pending_sessions"] == []
        assert store.decision(original["decision_id"]) == original
        assert all(
            o["evidence_kind"] == "prospective"
            and o["observation_evidence_kind"] == "historical_market"
            for o in store.records("symbol_outcomes")
        )
        assert list((tmp_path / "captures").glob("*.json"))
    finally:
        store.close()


@pytest.mark.parametrize("change", [{"source": "other"}, {"evidence_kind": "synthetic"}])
def test_incompatible_later_capture_cannot_resolve_prospective_forecasts(tmp_path, change):
    data = market("prospective")
    store, report = store_scan(tmp_path, data)
    try:
        store.decide(report, data, now=NOW, holding_seconds=1800)
        later = dict(market("prospective", end=NOW + 2100), **change)
        result = store.resolve(later, NOW + 2100)
        assert result["symbol_resolved"] == result["resolved"] == []
        assert store.prior_rows() == []
    finally:
        store.close()


def test_diagnostics_explain_counts_without_changing_economic_gates():
    p = target()
    rows = peers(p)
    foreign = [
        dict(r, prediction_id="foreign-" + r["prediction_id"], evidence_kind="historical_market")
        for r in rows
    ]
    result = cohort_evidence(p, rows + foreign)
    assert result["total_resolved_days"] == result["matching_cohort_days"] == 20
    assert result["same_pool_resolved_days"] == 20 and result["target_days"] == 5
    assert result["resolved_days_by_pool"] == {"historical_market": 20, "prospective": 20}
    assert result["exclusion_categories"] == {"evidence_pool": 25}
    sparse = cohort_evidence(p, foreign)
    assert sparse["total_resolved_days"] == 20
    assert sparse["same_pool_resolved_days"] == sparse["days"] == sparse["target_days"] == 0
    assert not sparse["sufficient"]


def test_benchmark_identity_cannot_be_pooled_and_forecasts_keep_provenance(tmp_path):
    p = target()
    rows = peers(p)
    contaminated = [
        dict(r, benchmark="QQQ", prediction_id="different-benchmark-" + r["prediction_id"])
        for r in rows
    ]
    result = cohort_evidence(p, contaminated)
    assert result["days"] == 0 and result["exclusion_categories"] == {"benchmark": 25}
    store, report = store_scan(tmp_path, market())
    try:
        decision = store.decide(report, market(), now=NOW, holding_seconds=1800)
        forecasts = {c["symbol"]: c["predictions"] for c in decision["quantitative_observations"]}
        assert all(p["context"]["benchmark"] == "QQQ" for p in forecasts["SPY"])
        assert all(p["context"]["benchmark"] == "SPY" for p in forecasts["QQQ"])
        store.resolve(market(end=NOW + 2100), NOW + 2100)
        spy = next(o for o in store.records("symbol_outcomes") if o["symbol"] == "SPY")
        assert spy["modeled_outcome"]["benchmark"] == "QQQ"
        assert spy["modeled_outcome"]["spy_gross"] is None
        assert all(r["benchmark"] == "QQQ" for r in spy["economic_rows"])
    finally:
        store.close()


def test_cli_live_alignment_filters_before_codex_and_revalidates_current_allowlist(
    tmp_path, monkeypatch, capsys
):
    data = entire_market()
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(data))
    allowed = ["QQQ", "IWM"]
    settings = SimpleNamespace(config=SimpleNamespace(allowed_symbols=allowed))
    monkeypatch.setattr("tradeagent.execution_policy.load_live_config", lambda path: settings)
    monkeypatch.setattr(cli.time, "time", lambda: NOW)
    monkeypatch.setattr(cli, "capture", lambda *a, **kw: pytest.fail("network access"))
    prefix = ["--state-dir", str(tmp_path / "research")]
    assert (
        cli.main(
            [*prefix, "scan", "--candidates", "3", "--input", str(path), "--config", "offline.toml"]
        )
        == 0
    )
    scan_result = json.loads(capsys.readouterr().out)
    assert {c["symbol"] for c in scan_result["candidates"]} == {"QQQ", "IWM"}
    assert cli.main([*prefix, "evidence", "--template"]) == 0
    assert {c["symbol"] for c in json.loads(capsys.readouterr().out)["assessments"]} == {
        "QQQ",
        "IWM",
    }
    assert cli.main([*prefix, "decide"]) == 2
    assert "same existing owner" in json.loads(capsys.readouterr().out)["reason"]
    assert cli.main([*prefix, "decide", "--config", "offline.toml"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["execution_universe"] == ["QQQ", "IWM"]
    allowed[:] = ["QQQ"]
    assert cli.main([*prefix, "decide", "--config", "offline.toml"]) == 2
    failed = json.loads(capsys.readouterr().out)
    assert "allowlist changed" in failed["reason"] and failed["orders_submitted"] == 0


def test_automatic_resolution_provider_failure_preserves_pending_forecasts(tmp_path, monkeypatch):
    store, report = store_scan(tmp_path, market("prospective"))
    try:
        original = store.decide(report, market("prospective"), now=NOW, holding_seconds=1800)
        monkeypatch.setattr(cli.time, "time", lambda: NOW + 86400)

        def unavailable(*args, **kwargs):
            raise ValueError("read-only historical provider unavailable")

        monkeypatch.setattr(cli, "capture", unavailable)
        today = market("prospective", end=NOW + 86400)
        today["session_open"] += 86400
        args = SimpleNamespace(
            input=None, provider="robinhood", oauth_helper=None, state_dir=tmp_path
        )
        result = cli.auto_resolve(store, today, args)
        assert result["resolved_symbols"] == 0
        assert result["pending_sessions"]
        assert result["limitations"][0]["reason"] == "read-only historical provider unavailable"
        assert store.decision(original["decision_id"]) == original
        assert store.prior_rows() == []
    finally:
        store.close()


def test_live_combined_economic_selection_never_uses_unauthorized_research_winner(
    tmp_path, monkeypatch
):
    from test_tradeplan_engine import evidence

    from tradeagent.research.evidence_cohorts import comparison_context
    from tradeagent.research.opportunity_workflow import prediction_from_dict

    data = entire_market()
    report = scan(data, now=NOW, candidate_count=3)
    monkeypatch.setattr(
        "tradeagent.execution_policy.load_live_config",
        lambda path: SimpleNamespace(config=SimpleNamespace(allowed_symbols=["QQQ", "IWM"])),
    )
    aligned = cli.align_live_universe(report, "offline.toml")
    store = DailyResearch(tmp_path)
    try:
        store.save("scans", aligned, aligned["scan_id"], NOW)
        store.assessment(assessment(aligned), NOW)
        initial = store.decide(aligned, data, now=NOW)
        rows = []
        for c in initial["quantitative_observations"]:
            for raw in c["predictions"]:
                p = prediction_from_dict(raw)
                for row in evidence(p, 20, gross=0.02 if c["symbol"] in {"UNH", "LLY"} else 0.005):
                    rows.append(
                        dict(
                            row,
                            prediction_id=str(len(rows)),
                            benchmark=p.context.value["benchmark"],
                            comparison_context=comparison_context(raw["features"]),
                        )
                    )
        monkeypatch.setattr(store, "prior_rows", lambda: rows)
        eligible = store.decide(aligned, data, now=NOW)
        assert eligible["final_decision"] == "UNDERLYING"
        assert eligible["comparisons"]["quant_codex"]["symbol"] == "QQQ"
        assert eligible["comparisons"]["quant_only"]["symbol"] in {"QQQ", "IWM"}
        broad = next(c for c in eligible["quantitative_observations"] if c["symbol"] == "UNH")
        assert broad["quant_plan"] is not None
        assert (
            broad["quant_plan"]["economics"]["lower_net_estimate"]
            > eligible["selected_plan"]["economics"]["lower_net_estimate"]
        )
        assert eligible["selected_plan"]["research_only"]  # synthetic fixtures never authorize LIVE
        assert eligible["orders_submitted"] == 0
    finally:
        store.close()
