"""Focused saved-evidence reporting regressions; no market, Codex or broker calls."""

import copy
import json
import sqlite3
from datetime import datetime

import pytest

from tradeagent.prospective import daily
from tradeagent.prospective.daily_reporting import (
    NY,
    SavedCaptures,
    cost_details,
    evaluate,
    ranking_quality,
    regenerate,
    render,
)

PROTOCOL = "daily-news-v2"
NOW = datetime(2026, 10, 14, 10, 30, tzinfo=NY).timestamp()


class Records:
    def __init__(self, values):
        self.values = values

    def records(self, kind):
        return self.values.get(kind, [])


def fixture():
    side = 0.00021
    returns = {
        s: {
            "base_net": net,
            "gross": (1 + net) * (1 + side) / (1 - side) - 1,
            "entry_price": 100,
            "exit_price": 100 * (1 + net),
            "entry_time": NOW + 300,
            "exit_time": NOW + 3900,
        }
        for s, net in (("A", 0.01), ("B", 0.02), ("C", -0.01), ("SPY", 0))
    }
    comparisons = {
        n: {"symbol": s, "shadow_direction": 1 if s else 0, "status": "VALID"}
        for n, s in (
            ("quant_only", "A"),
            ("codex_price", "A"),
            ("codex_news", "B"),
            ("quant_codex_news", "B"),
            ("seeded_random", None),
        )
    }
    candidates = [
        {
            "symbol": s,
            "predictions": [{"context": {"benchmark": "SPY"}}],
            "outcome_costs": {"base_side_cost": side, "observed_spread_bps": 2},
            "scanner": {
                "observation_time": NOW,
                "market_evidence": {
                    "quote": {"bid": 99.99, "ask": 100.01, "asof": NOW, "observed_at": NOW}
                },
            },
        }
        for s in returns
    ]
    decision = {
        "decision_id": "d",
        "scan_id": "s",
        "research_protocol": PROTOCOL,
        "evidence_kind": "synthetic",
        "source": "synthetic",
        "decision_time": NOW,
        "market_observed_at": NOW,
        "outcome_entry_time": NOW + 300,
        "outcome_exit_time": NOW + 3900,
        "session_open": NOW - 3600,
        "research_universe": list(returns),
        "comparison_universe": ["A", "B", "C"],
        "ranked_candidates": candidates[:3],
        "quantitative_observations": candidates,
        "assessment_status": "VALID",
        "news_assessment_status": "VALID",
        "comparisons": comparisons,
    }
    assessments = [
        {
            "scan_id": "s",
            "symbol": s,
            "assessor": tag,
            "rank": i + 1,
            "stance": "long" if i == 0 else "watch",
        }
        for tag, order in (("price", ["A", "B", "C"]), ("news", ["B", "A", "C"]))
        for i, s in enumerate(order)
    ]
    outcome = {
        "decision_id": "d",
        "evidence_kind": "synthetic",
        "resolved_at": NOW + 5400,
        "candidate_outcomes": returns,
        "comparisons": {
            n: returns[a["symbol"]] if a["symbol"] else {"gross": 0, "base_net": 0}
            for n, a in comparisons.items()
        },
    }
    capture = {
        "decision_id": "d",
        "protocol": PROTOCOL,
        "session_date": "2026-10-14",
        "phase": "FINAL",
        "status": "COMPLETED",
        "recorded_at": NOW,
        "codex_news": {"web_searches": 2},
    }
    return Records(
        {
            "decisions": [decision],
            "assessments": assessments,
            "outcomes": [outcome],
            "sessions": [capture],
        }
    )


def test_equal_weight_candidate_baseline_and_daily_disagreements():
    store = fixture()
    result = evaluate(store, PROTOCOL, NOW + 5400)
    row = result["today"]
    assert row["basket"]["net_return"] == pytest.approx(0.02 / 3)
    assert row["arms"]["codex_news"]["excess_over_candidate_basket"] == pytest.approx(
        0.02 - 0.02 / 3
    )
    assert row["arms"]["seeded_random"]["excess_over_candidate_basket"] == pytest.approx(-0.02 / 3)
    assert row["arms"]["codex_news"]["pnl_dollars"] == 20
    assert row["coverage"] == {
        "scanned": 4,
        "eligible": 3,
        "complete_eligible": 3,
        "complete_scanned": 4,
        "LONG": 4,
        "CASH": 1,
        "UNAVAILABLE": 0,
    }
    assert row["ranking_quality"]["price"]["spearman"] == pytest.approx(0.5)
    assert row["ranking_quality"]["price"]["pairwise_accuracy"] == pytest.approx(2 / 3)
    assert row["ranking_quality"]["news"]["spearman"] == 1
    assert row["ranking_quality"]["news"]["pairwise_accuracy"] == 1
    m = result["evidence_pools"]["synthetic"]["disagreements"]
    assert m["price_vs_news_complete_ranking"]["disagreement_days"] == 1
    assert m["price_vs_news_top_rank"]["percent"] == 100
    assert m["quant_vs_price_selection"]["percent"] == 0
    assert m["quant_vs_news_selection"]["percent"] == 100
    assert result["evidence_pools"]["synthetic"]["news_minus_price"]["days"] == 1
    assert row["news"]["matched_net_difference"] == pytest.approx(0.01)


@pytest.mark.parametrize("wrong_window", [False, True])
def test_incomplete_candidates_do_not_become_partial_basket_or_ranking(wrong_window):
    store = fixture()
    observed = store.records("outcomes")[0]["candidate_outcomes"]
    if wrong_window:
        observed["C"]["exit_time"] += 60
    else:
        del observed["C"]
    row = evaluate(store, PROTOCOL, NOW + 5400)["today"]
    assert row["coverage"]["complete_eligible"] == 2
    assert row["basket"]["status"] == "PENDING" and row["basket"]["net_return"] is None
    assert row["basket"]["missing_symbols"] == ["C"]
    assert row["ranking_quality"]["news"]["status"] == "PENDING"
    assert row["arms"]["codex_news"]["net_return"] == 0.02
    assert row["arms"]["codex_news"]["excess_over_candidate_basket"] is None
    assert row["missing_outcomes"][0]["symbol"] == "C"


def test_cash_failed_assessment_and_pending_outcome_are_distinct():
    store = fixture()
    decision = store.records("decisions")[0]
    decision["assessment_status"] = "UNAVAILABLE"
    decision["comparisons"]["codex_price"].update(
        status="UNAVAILABLE", symbol=None, shadow_direction=0
    )
    store.records("outcomes")[0]["comparisons"]["codex_price"] = None
    result = evaluate(store, PROTOCOL, NOW + 5400)
    arms = result["today"]["arms"]
    assert arms["codex_price"]["decision"] == arms["codex_price"]["status"] == "UNAVAILABLE"
    assert arms["codex_price"]["net_return"] is None
    assert arms["seeded_random"]["decision"] == "CASH"
    assert arms["seeded_random"]["net_return"] == 0
    m = result["evidence_pools"]["synthetic"]["disagreements"]["quant_vs_price_selection"]
    assert (
        m["valid_days"] == 0 and m["percent"] is None and m["unavailable_dates"] == ["2026-10-14"]
    )
    store.values["outcomes"] = []
    arms = evaluate(store, PROTOCOL, NOW + 5400)["today"]["arms"]
    assert arms["quant_only"]["status"] == "PENDING" and arms["quant_only"]["pnl_dollars"] is None
    assert (
        arms["seeded_random"]["decision"] == "CASH" and arms["seeded_random"]["net_return"] is None
    )
    later = evaluate(store, PROTOCOL, NOW + 86400)
    assert later["today"] is None
    assert "No prior-day substitution" in render(later, {"run": store.records("sessions")[0]})


def test_news_consulted_without_change_is_different_from_no_observed_search():
    store = fixture()
    for a in store.records("assessments"):
        if a["assessor"] == "news":
            a.update(
                rank={"A": 1, "B": 2, "C": 3}[a["symbol"]],
                stance="long" if a["symbol"] == "A" else "watch",
            )
    store.records("decisions")[0]["comparisons"]["codex_news"]["symbol"] = "A"
    store.records("outcomes")[0]["comparisons"]["codex_news"] = store.records("outcomes")[0][
        "comparisons"
    ]["codex_price"]
    news = evaluate(store, PROTOCOL, NOW + 5400)["today"]["news"]
    assert news["status"] == "CONSULTED" and news["changed_selection"] is False
    assert news["matched_net_difference"] == 0
    store.records("sessions")[0]["codex_news"]["web_searches"] = 0
    news = evaluate(store, PROTOCOL, NOW + 5400)["today"]["news"]
    assert news["status"] == "SEARCH_NOT_OBSERVED" and news["matched_net_difference"] is None


def test_realized_ties_do_not_invent_rank_association_or_extra_days():
    assessments = {s: {"rank": i + 1} for i, s in enumerate("ABC")}
    quality = ranking_quality(assessments, list("ABC"), {s: {"base_net": 0} for s in "ABC"})
    assert quality["spearman"] is None and quality["pairwise_accuracy"] is None
    assert quality["tied_pairs"] == 3 and quality["scored_pairs"] == 0
    assert (
        ranking_quality(
            assessments, list("ABC"), {s: {"base_net": -i} for i, s in enumerate("ABC")}
        )["spearman"]
        == 1
    )
    assert (
        ranking_quality(
            assessments, list("ABC"), {s: {"base_net": i} for i, s in enumerate("ABC")}
        )["spearman"]
        == -1
    )


def test_final_decision_quote_and_real_missing_minutes_are_reported_without_reconstruction(
    tmp_path,
):
    store = fixture()
    d = store.records("decisions")[0]
    c = d["quantitative_observations"][0]
    captures = tmp_path / "captures"
    captures.mkdir()
    d["market_observed_at"] += 1
    final_quote = {"bid": 99.9, "ask": 100.1, "asof": NOW, "observed_at": NOW + 1}
    c["outcome_costs"].update(observed_spread_bps=20, base_side_cost=0.00111)
    data = {
        "source": "synthetic",
        "evidence_kind": "synthetic",
        "observed_at": NOW + 1,
        "session_open": d["session_open"],
        "quotes": {"A": final_quote},
        "bars": [],
    }
    (captures / "final.json").write_text(json.dumps(data))
    for symbol in ("A", "SPY"):
        for stamp in range(int(d["outcome_entry_time"]), int(d["outcome_exit_time"]), 60):
            if symbol == "A" and stamp == d["outcome_entry_time"] + 120:
                continue
            data["bars"].append(
                {"symbol": symbol, "start": stamp, "end": stamp + 60, "available_at": NOW + 5400}
            )
    data["observed_at"] = NOW + 5400
    (captures / "later.json").write_text(json.dumps(data))
    saved = SavedCaptures(tmp_path, NOW + 5400)
    cost = cost_details(d, c, None, saved)
    assert cost["quote"] == final_quote and cost["cost_arithmetic_valid"] is True
    gaps = saved.missing_path(d, c)["missing_minutes"]
    assert len(gaps["A"]) == 1 and gaps["SPY"] == []
    c["outcome_costs"]["base_side_cost"] = 0.9
    assert cost_details(d, c, None, saved)["cost_arithmetic_valid"] is False


def test_report_only_uses_read_only_database_and_never_initializes_pipeline(tmp_path, monkeypatch):
    store = fixture()
    db = sqlite3.connect(tmp_path / "experience.sqlite3")
    for kind, records in store.values.items():
        db.execute("CREATE TABLE opportunity_" + kind + "(recorded_at REAL, payload TEXT)")
        db.executemany(
            "INSERT INTO opportunity_" + kind + " VALUES(?, ?)",
            [(NOW, json.dumps(r)) for r in records],
        )
    db.commit()
    db.close()
    status = {"protocol": PROTOCOL, "run": store.records("sessions")[0]}
    (tmp_path / "daily-status.json").write_text(json.dumps(status))
    original = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    monkeypatch.setattr(
        daily, "Pipeline", lambda *a, **k: pytest.fail("report-only must never create a pipeline")
    )
    result = regenerate(tmp_path, PROTOCOL, NOW + 5400)
    assert result["orders_submitted"] == 0 and result["today"]["basket"]["status"] == "AVAILABLE"
    for name, content in original.items():
        assert (tmp_path / name).read_bytes() == content
    report = (tmp_path / "daily-report.md").read_text()
    assert (
        "Exploratory selection diagnostics" in report
        and "Original frozen trading metrics" in report
    )
    assert "First choices need not match" in report
    daily.main(["--report-only", "--state-dir", str(tmp_path), "--protocol", PROTOCOL])


def test_daily_paired_statistics_keep_pools_and_sessions_independent():
    store = fixture()
    other = copy.deepcopy(store.values)
    for kind in ("decisions", "outcomes", "sessions"):
        other[kind][0]["decision_id"] = "other"
    other["decisions"][0]["evidence_kind"] = "prospective"
    other["outcomes"][0]["evidence_kind"] = "prospective"
    for kind, values in other.items():
        if kind != "assessments":
            store.values.setdefault(kind, []).extend(values)
    result = evaluate(store, PROTOCOL, NOW + 5400)
    for summary in result["evidence_pools"].values():
        assert summary["news_minus_price"]["days"] == 1
        assert summary["news_minus_price"]["standard_error"] is None
    store.values["decisions"].append(copy.deepcopy(store.values["decisions"][0]))
    with pytest.raises(ValueError, match="duplicate frozen session"):
        evaluate(store, PROTOCOL, NOW + 5400)


def test_terminal_outcome_is_unavailable_and_never_cash_or_pending(tmp_path):
    store = fixture()
    store.values["resolution_failures"] = [{"decision_id": "d"}]
    result = evaluate(store, PROTOCOL, NOW + 5400, SavedCaptures(tmp_path, NOW + 5400))
    row = result["today"]
    assert row["resolution_status"] == "UNAVAILABLE"
    assert row["basket"]["status"] == "UNAVAILABLE"
    assert all(
        a["status"] == "UNAVAILABLE" and a["net_return"] is None for a in row["arms"].values()
    )
    assert all(m["status"] == "UNAVAILABLE" for m in row["missing_outcomes"])


def test_normal_ticks_generate_reports_without_extra_model_or_market_calls(tmp_path):
    from test_daily_research import make_pipeline, valid_assessment

    assessments = []

    def assessor(report):
        assessments.append(report["scan_id"])
        return valid_assessment(report)

    pipeline, clock, calls = make_pipeline(tmp_path, assessor=assessor)
    try:
        captured = pipeline.tick()
        assert (tmp_path / "daily-report.md").is_file()
        assert captured["exploratory_selection"]["today"]["resolution_status"] == "PENDING"
        assert len(calls) == len(assessments) == 1
        clock[0] = pipeline.store.decision()["outcome_exit_time"]
        resolved = pipeline.tick()
        assert resolved["exploratory_selection"]["today"]["resolution_status"] == "RESOLVED"
        assert len(calls) == 2 and len(assessments) == 1
        assert "resolution: RESOLVED" in (tmp_path / "daily-report.md").read_text()
        assert pipeline.store.records("executions") == []
    finally:
        pipeline.store.close()
