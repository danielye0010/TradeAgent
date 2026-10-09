"""Focused manual LIVE integration; all HTTP and account data are offline fixtures."""

import copy
import json
from pathlib import Path

import pytest
from test_fractional_live import fractional_runtime
from test_opportunities import NOW, assessment, market
from test_tradeplan_engine import evidence

from tradeagent.model import Halt
from tradeagent.opportunity_cli import main
from tradeagent.opportunity_live import execute_opportunity, execution_result
from tradeagent.research.evidence_cohorts import comparison_context
from tradeagent.research.opportunity_workflow import DailyResearch, prediction_from_dict


def research_fixture(tmp_path, clock):
    """Invented evidence, labelled explicitly; no real market/owner data."""
    dataset = market("prospective")
    offset = clock() - NOW
    for field in ("observed_at", "session_open", "session_close"):
        dataset[field] += offset
    for bar in dataset["bars"]:
        for field in ("start", "end", "available_at"):
            bar[field] += offset
        factor = {"QQQ": 500 / 101.2, "IWM": 5, "SPY": 500 / 200.3}[bar["symbol"]]
        for field in ("open", "high", "low", "close"):
            bar[field] *= factor
        if bar["symbol"] == "QQQ":
            bar["symbol"] = "AAPL"
    for quote in dataset["quotes"].values():
        quote.update(bid=499.99, ask=500.01, asof=clock(), observed_at=clock())
    for symbol, ref in dataset["references"].items():
        ref["previous_close_time"] += offset
        ref["previous_close"] *= {"QQQ": 500 / 101.2, "IWM": 5, "SPY": 500 / 200.3}[symbol]
    dataset["symbols"] = ["AAPL" if s == "QQQ" else s for s in dataset["symbols"]]
    for key in ("quotes", "references"):
        dataset[key]["AAPL"] = dataset[key].pop("QQQ")
    return dataset


def freeze_research(store, dataset, now, *, support=True, prior=True):
    from tradeagent.research.opportunities import scan

    scan_result = scan(dataset, now=now, candidate_count=3)
    store.save("scans", scan_result, scan_result["scan_id"], now)
    item = assessment(scan_result, stance="long" if support else "watch")
    item.update(symbol="AAPL", observed_at=now, sources=[])
    store.assessment(item, now)
    initial = store.decide(scan_result, dataset, now=now, holding_seconds=1800, delay_seconds=300)
    if prior:
        rows = [
            dict(
                row,
                prediction_id=raw["prediction_id"] + row["prediction_id"],
                comparison_context=comparison_context(raw["features"]),
            )
            for candidate in initial["ranked_candidates"]
            if candidate["symbol"] == "AAPL"
            for raw in candidate["predictions"]
            for row in evidence(prediction_from_dict(raw), gross=0.008)
        ]
        store.save(
            "outcomes",
            {
                "economic_rows": rows,
                "decision_id": initial["decision_id"],
                "evidence_kind": "prospective",
                "explicit_offline_fixture": True,
                "comparisons": {name: {"base_net": 0} for name in initial["comparisons"]},
            },
            "explicit-offline-prior-fixture-" + scan_result["scan_id"],
            now - 1,
        )
        decision = store.decide(
            scan_result, dataset, now=now, holding_seconds=1800, delay_seconds=300
        )
    else:
        decision = initial
    return decision


@pytest.fixture
def runtime(tmp_path, monkeypatch, request):
    _, sim, _, target, config_file = fractional_runtime(
        tmp_path, monkeypatch, fees="0.01", exit_mode=getattr(request, "param", "filled")
    )
    monkeypatch.setattr("tradeagent.opportunity_live.time.sleep", sim.clock.advance)
    calls = []
    cls = __import__("http.client", fromlist=["HTTPSConnection"]).HTTPSConnection
    original_request = cls.request

    def record_request(self, method, path, body, headers):
        calls.append(json.loads(body))
        return original_request(self, method, path, body, headers)

    monkeypatch.setattr(cls, "request", record_request)
    # Only the existing generated fractional fixture is used; no owner TOML access.
    store = DailyResearch(tmp_path / "research")
    decision = freeze_research(store, research_fixture(tmp_path, sim.clock), sim.clock())
    original_config = config_file.read_bytes()
    yield store, decision, sim, calls, target, config_file, original_config
    assert config_file.read_bytes() == original_config
    sim.close()
    store.close()


def placements(calls):
    return [
        m
        for m in calls
        if m["method"] == "tools/call" and m["params"]["name"] == "place_equity_order"
    ]


def test_explicit_live_required_and_codex_only_never_reaches_config_or_engine(runtime):
    store, decision, _, calls, _, config, _ = runtime
    with pytest.raises(Halt, match="explicit owner LIVE"):
        execute_opportunity(store, decision["decision_id"], config)
    with pytest.raises(Halt, match="cannot authorize"):
        execute_opportunity(store, decision["decision_id"], config, live=True, mode="codex_only")
    assert calls == []


def test_cli_waits_displays_owner_plan_before_wire_then_records_confirmed_exit(
    runtime, monkeypatch, capsys
):
    store, decision, sim, calls, _, config, _ = runtime
    assert decision["final_decision"] == "UNDERLYING"
    before = sim.clock()
    cls = __import__("http.client", fromlist=["HTTPSConnection"]).HTTPSConnection
    original_request = cls.request

    def inspected_request(self, method, path, body, headers):
        message = json.loads(body)
        if message["method"] == "tools/call" and message["params"]["name"] == "place_equity_order":
            files = list(store.experience.directory.glob("live/*/*/purchase-plan-validated.json"))
            assert files
            displayed = json.loads(files[0].read_text())
            assert displayed["account_risk_validated"]
            assert displayed["sizing"] == {"dollar_amount": "5"}
            assert displayed["decision_id"] == decision["decision_id"]
            assert displayed["execution_quote_asof"] >= decision["entry_after"]
            assert displayed["maximum_entry_price"] == "500.01"
        return original_request(self, method, path, body, headers)

    monkeypatch.setattr(cls, "request", inspected_request)
    assert (
        main(
            [
                "--state-dir",
                str(store.experience.directory),
                "execute",
                "--decision-id",
                decision["decision_id"],
                "--config",
                str(config),
                "--live",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert "PLAN_CREATED" in output.err and "AWAITING_ENTRY" in output.err
    assert result["status"] == "CLOSED" and result["submission_status"] == "BROKER_CONFIRMED"
    assert result["position"]["flat_bot_position"] and result["position"]["final_positions"] == {}
    assert result["pnl"]["status"] == "CONFIRMED"
    assert result["pnl"]["entry_average_price"] and result["pnl"]["exit_average_price"]
    assert len(placements(calls)) == 2
    assert placements(calls)[0]["params"]["arguments"]["dollar_amount"] == "5"
    assert sim.clock() >= before + 2100
    report = json.loads(Path(result["execution_report_path"]).read_text())
    assert report["exit_due"] == decision["exit_at"]
    assert (
        report["decision"]["provenance"]["prediction_id"]
        == decision["selected_plan"]["decision"]["prediction_id"]
    )
    assert store.decision(decision["decision_id"]) == decision
    assert store.records("executions")[0]["report"] == report
    assert store.performance()["actual_performance"][0]["realized_pnl"] == report["realized_pnl"]
    assert store.records("executions")[0]["research_arm"] == "quant_codex"


def test_retry_after_expiry_recovers_same_lifecycle_without_another_entry(runtime):
    store, decision, _, calls, _, config, _ = runtime
    result = execute_opportunity(
        store, decision["decision_id"], config, live=True, emit=lambda *a: None
    )
    assert result["status"] == "CLOSED"
    writes = len(placements(calls))
    again = execute_opportunity(
        store, decision["decision_id"], config, live=True, emit=lambda *a: None
    )
    assert again["status"] == "CLOSED"
    assert len(placements(calls)) == writes
    performance = store.performance()
    assert performance["actual_execution_records"] == 1
    assert performance["execution_report_records"] == 2
    assert len(performance["actual_performance"]) == 1


def test_no_trade_never_falls_back_to_quant_only_or_authenticates(runtime):
    store, _, sim, calls, _, config, _ = runtime
    dataset = research_fixture(None, sim.clock)
    dataset["source"] += "-independent-abstention"
    decision = freeze_research(store, dataset, sim.clock(), support=False)
    assert decision["comparisons"]["quant_only"]["plan"]
    assert decision["comparisons"]["quant_codex"]["plan"] is None
    result = execute_opportunity(
        store, decision["decision_id"], config, live=True, emit=lambda *a: None
    )
    assert result["status"] == "NO_TRADE" and result["submission_status"] == "NOT_SUBMITTED"
    assert calls == []


def test_expired_and_research_only_plans_never_order(runtime):
    store, decision, sim, calls, _, config, _ = runtime
    sim.clock.advance(421)
    result = execute_opportunity(
        store, decision["decision_id"], config, live=True, emit=lambda *a: None
    )
    assert result["status"] == "EXPIRED" and calls == []
    blocked = copy.deepcopy(decision)
    blocked["decision_id"] = "explicit-research-only-fixture"
    blocked["comparisons"]["quant_codex"]["plan"]["research_only"] = True
    store.save("decisions", blocked, blocked["decision_id"])
    result = execute_opportunity(
        store, blocked["decision_id"], config, live=True, emit=lambda *a: None
    )
    assert result["status"] == "NO_TRADE" and calls == []


def test_wait_overrun_does_not_extend_the_plan(runtime, monkeypatch):
    store, decision, sim, calls, _, config, _ = runtime
    monkeypatch.setattr("tradeagent.opportunity_live.time.sleep", lambda _: sim.clock.advance(500))
    result = execute_opportunity(
        store, decision["decision_id"], config, live=True, emit=lambda *a: None
    )
    assert result["status"] == "EXPIRED" and calls == []
    assert store.decision(decision["decision_id"])["entry_deadline"] == decision["entry_deadline"]


def test_display_failure_before_placement_blocks_orders(runtime):
    store, decision, _, calls, _, config, _ = runtime

    def failed_display(stage, payload):
        if stage == "PLAN_CREATED" and payload["purchase_plan"]["account_risk_validated"]:
            raise OSError("explicit fixture display unavailable")

    result = execute_opportunity(
        store, decision["decision_id"], config, live=True, emit=failed_display
    )
    assert result["status"] in {"HALTED", "RECOVERY_REQUIRED"}
    assert result["submission_status"] == "NOT_SUBMITTED"
    assert not placements(calls)


def test_late_mcp_read_after_fresh_review_cannot_place_outside_plan_window(runtime, monkeypatch):
    store, decision, sim, calls, _, config, _ = runtime
    cls = __import__("http.client", fromlist=["HTTPSConnection"]).HTTPSConnection
    original_response = cls.getresponse
    reviewed = False

    def slow_response(self):
        response = original_response(self)
        if (
            reviewed
            and self.message["method"] == "tools/call"
            and self.message["params"]["name"] == "get_trade_approval_setting"
        ):
            sim.clock.advance(30)
        return response

    def delayed_display(stage, payload):
        nonlocal reviewed
        if stage == "PLAN_CREATED" and payload["purchase_plan"]["account_risk_validated"]:
            sim.clock.advance(100)
        # Mark review by inspecting the already captured OFFLINE calls.
        reviewed = any(
            m["method"] == "tools/call" and m["params"]["name"] == "review_equity_order"
            for m in calls
        )

    # Observer does not expose review events as display; detect it on the fake HTTP response.
    def marked_response(self):
        nonlocal reviewed
        response = slow_response(self)
        if (
            self.message["method"] == "tools/call"
            and self.message["params"]["name"] == "review_equity_order"
        ):
            reviewed = True
        return response

    monkeypatch.setattr(cls, "getresponse", marked_response)
    result = execute_opportunity(
        store, decision["decision_id"], config, live=True, emit=delayed_display
    )
    assert reviewed
    assert sim.clock() > decision["entry_deadline"]
    assert result["submission_status"] == "NOT_SUBMITTED" and not placements(calls)
    assert "expired" in result["reason"] or "entry window" in result["reason"]


@pytest.mark.parametrize("state", ["queued", "rejected", "partially_filled"])
def test_order_acceptance_never_fabricates_closed_position_or_realized_pnl(state):
    report = {
        "status": "RUNNING",
        "submission_status": "BROKER_CONFIRMED",
        "broker_order_count": 1,
        "orders": [
            {
                "id": "fixture-id",
                "symbol": "QQQ",
                "side": "buy",
                "state": state,
                "cumulative_quantity": "0",
            }
        ],
        "realized_pnl": "999",
    }
    result = execution_result(report)
    assert result["status"] != "CLOSED"
    assert result["pnl"]["status"] == "PENDING" and result["pnl"]["realized_pnl"] is None
    assert result["position"]["final_positions"] is None


def test_uncertain_submission_stays_recovery_required():
    result = execution_result(
        {
            "status": "HALTED",
            "submission_status": "SUBMISSION_UNKNOWN",
            "reconciliation_blocker": "unknown order identity",
        }
    )
    assert result["status"] == "RECOVERY_REQUIRED"
    assert result["broker_order_count"] is None and result["pnl"]["realized_pnl"] is None


def test_feedback_failure_cannot_interrupt_the_engine_exit(runtime, monkeypatch):
    store, decision, _, calls, _, config, _ = runtime

    def failing_save(*args):
        raise OSError("explicit fixture feedback disk failure")

    monkeypatch.setattr(store, "save", failing_save)
    result = execute_opportunity(
        store, decision["decision_id"], config, live=True, emit=lambda *a: None
    )
    assert result["status"] == "CLOSED" and result["position"]["flat_bot_position"]
    assert len(placements(calls)) == 2 and result["feedback_warnings"]
    assert Path(result["engine_report_path"]).is_file()


def test_new_daily_plan_archives_closed_history_and_prevents_old_decision_replay(runtime):
    store, decision, sim, calls, target, config, _ = runtime
    first = execute_opportunity(
        store, decision["decision_id"], config, live=True, emit=lambda *a: None
    )
    assert first["status"] == "CLOSED"
    original = (target / "agent/state.sqlite3").read_bytes()
    second_decision = freeze_research(store, research_fixture(None, sim.clock), sim.clock())
    second = execute_opportunity(
        store, second_decision["decision_id"], config, live=True, emit=lambda *a: None
    )
    assert second["status"] == "CLOSED", second
    archive = Path(second["previous_lifecycle"]["archived_run"])
    assert (archive / "agent/state.sqlite3").read_bytes() == original
    assert (archive / "report.json").is_file()
    writes = len(placements(calls))
    replay = execute_opportunity(
        store, decision["decision_id"], config, live=True, emit=lambda *a: None
    )
    assert replay["status"] == "RECOVERY_REQUIRED"
    assert "already attempted" in replay["reason"]
    assert len(placements(calls)) == writes


@pytest.mark.parametrize("runtime", ["partial"], indirect=True)
def test_unresolved_previous_exposure_blocks_an_unrelated_new_entry(runtime):
    store, decision, sim, calls, target, config, _ = runtime
    first = execute_opportunity(
        store, decision["decision_id"], config, live=True, emit=lambda *a: None
    )
    assert first["status"] == "RECOVERY_REQUIRED"
    assert first["position"]["bot_owned_residual"] != "0"
    assert first["pnl"]["realized_pnl"] is None
    original = (target / "agent/state.sqlite3").read_bytes()
    next_decision = freeze_research(store, research_fixture(None, sim.clock), sim.clock())
    writes = len(placements(calls))
    blocked = execute_opportunity(
        store, next_decision["decision_id"], config, live=True, emit=lambda *a: None
    )
    assert blocked["status"] == "RECOVERY_REQUIRED"
    assert len(placements(calls)) == writes
    assert (target / "agent/state.sqlite3").read_bytes() == original
    assert "recover" in blocked["recovery"]


def test_fresh_execution_quote_above_original_cap_rejects_without_order(runtime):
    from tradeagent.model import dec

    store, decision, sim, calls, _, config, _ = runtime
    sim.initial.asks["AAPL"] = dec("501")
    result = execute_opportunity(
        store, decision["decision_id"], config, live=True, emit=lambda *a: None
    )
    assert result["status"] == "HALTED"
    assert result["submission_status"] == "NOT_SUBMITTED"
    assert "price condition" in result["reason"] and not placements(calls)


def test_progress_display_error_after_submission_does_not_stop_exit(runtime):
    store, decision, _, calls, _, config, _ = runtime

    def display_error(stage, payload):
        if stage == "ORDER_SUBMITTED":
            raise OSError("explicit fixture progress display failure")

    result = execute_opportunity(
        store, decision["decision_id"], config, live=True, emit=display_error
    )
    assert result["status"] == "CLOSED"
    assert result["feedback_warnings"] and len(placements(calls)) == 2


def test_owner_lock_rechecks_immutable_identity_before_any_broker_call(runtime):
    from tradeagent.execution_policy import load_live_config
    from tradeagent.oneshot import run_live
    from tradeagent.research.opportunity_workflow import selected_execution

    store, decision, sim, calls, _, config, _ = runtime
    result = execute_opportunity(
        store, decision["decision_id"], config, live=True, emit=lambda *a: None
    )
    assert result["status"] == "CLOSED"
    other = freeze_research(store, research_fixture(None, sim.clock), sim.clock())
    plan, prediction = selected_execution(other)
    before = len(calls)
    with pytest.raises(Halt, match="different immutable"):
        run_live(load_live_config(config), plan=plan, prediction=prediction)
    assert len(calls) == before


def test_plan_handoff_cannot_raise_an_explicit_owner_limit_price():
    from dataclasses import replace

    from test_tradeplan_engine import forecast, snapshot

    from tradeagent.model import Config, Risk
    from tradeagent.oneshot import paper_snapshot, validated_plan_entry
    from tradeagent.research.tradeplan import build_plan
    from tradeagent.simulator import SimClock

    snap = replace(snapshot(), bid=11.99, ask=12.01)
    prediction = forecast(snap)
    plan = build_plan(prediction, snap, evidence(prediction))
    account = paper_snapshot(SimClock(snap.decision_time))
    account.countries = {"QQQ": "US", "IWM": "US"}
    config = Config(mode="LIVE", live_enabled=True)
    entry = {"order_type": "limit", "quantity": "1", "limit_price": "12.00"}
    intent, _ = validated_plan_entry(
        plan, prediction, account, config, Risk(), snap.decision_time, "25", entry
    )
    assert str(intent.limit_price) == "12.00"
    assert entry == {"order_type": "limit", "quantity": "1", "limit_price": "12.00"}


def test_known_reconciled_pnl_is_retained_even_if_engine_requires_incident_review():
    result = execution_result(
        {
            "status": "HALTED",
            "submission_status": "BROKER_CONFIRMED",
            "reconciliation_status": "RECONCILED",
            "cash_reconciled": True,
            "flat_bot_position": True,
            "outstanding_incident": True,
            "final_positions": {},
            "realized_pnl": "-0.02",
        }
    )
    assert result["status"] == "RECOVERY_REQUIRED"
    assert result["position"]["flat_bot_position"]
    assert result["pnl"]["status"] == "CONFIRMED"
    assert result["pnl"]["realized_pnl"] == "-0.02"
