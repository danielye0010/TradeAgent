"""Offline prospective automation: no broker transport, no real orders."""

import copy
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError
from test_opportunities import NOW, market
from test_opportunity_coverage import entire_market

from tradeagent.calendar import session_bounds
from tradeagent.prospective import daily
from tradeagent.prospective.assessor import assess
from tradeagent.research.alpha_signals import (
    ALPHA_VERSION,
    LEGACY_ALPHA_VERSIONS,
    SEMANTICS,
    semantic_version,
)
from tradeagent.research.domain import identity
from tradeagent.research.opportunity_workflow import selected_execution


def data_for(day, end, missing=False):
    opening, closing = session_bounds(day)
    data = entire_market()
    shift = end - NOW
    for bar in data["bars"]:
        for name in ("start", "end", "available_at"):
            bar[name] += shift
    # Genuine completed signal path begins at session open in synthetic fixture.
    if end - opening > 1800:
        template = copy.deepcopy(data["bars"])
        for bar in template:
            bar["start"] -= 1800
            bar["end"] -= 1800
        data["bars"] = template + data["bars"]
    data.update(
        source="robinhood-opportunity-minute-v1",
        session_open=opening,
        session_close=closing,
        observed_at=end,
        requested_at=end - 1,
    )
    for q in data["quotes"].values():
        q.update(asof=end, observed_at=end)
    for ref in data["references"].values():
        ref["previous_close_time"] = opening - 86400
    if missing:
        data["bars"] = [b for b in data["bars"] if not (b["symbol"] == "QQQ" and b["end"] == end)]
    return data


def valid_assessment(report):
    return {
        "assessments": [
            {
                "symbol": c["symbol"],
                "stance": "long",
                "rank": i + 1,
                "thesis": "Synthetic observed momentum",
                "catalyst": "Price action only",
                "priced_in": "No news provided",
                "contradictions": ["Unvalidated"],
                "invalidation": "Momentum reversal",
            }
            for i, c in enumerate(report["candidates"])
        ]
    }, {"model": "synthetic-model", "tool_calls": 0}


def make_pipeline(tmp_path, day=date(2026, 10, 12), assessor=valid_assessment):
    clock = [daily.window(day)[0]]
    calls = []

    def collector(symbols, provider, helper, *, session=None):
        calls.append((list(symbols), session))
        return data_for(session or day, clock[0])

    pipeline = daily.Pipeline(
        tmp_path,
        collector=collector,
        assessor=assessor,
        clock=lambda: clock[0],
        evidence_kind="synthetic",
    )
    return pipeline, clock, calls


def test_two_end_to_end_sessions_idempotency_and_delayed_resolution(tmp_path):
    pipeline, clock, calls = make_pipeline(tmp_path)
    try:
        first = pipeline.tick()
        assert first["run"]["status"] == "COMPLETED"
        original = pipeline.store.decision()
        assert set(original["comparisons"]) == {
            "quant_only",
            "codex_only",
            "quant_codex",
            "seeded_random",
        }
        assert (
            original["selected_plan"] is None and original["freeze_time"] < original["entry_after"]
        )
        assert all(a["plan"] is None for a in original["comparisons"].values())
        assert original["comparison_universe"] == [
            c["symbol"] for c in original["ranked_candidates"]
        ]
        assert len(original["quantitative_observations"]) == 31
        pipeline.tick()
        assert len(calls) == 1 and len(pipeline.store.records("decisions")) == 1
        with pytest.raises(ValueError, match="cannot authorize"):
            selected_execution(original)
        # Delayed path availability resolves later without issuing a new forecast.
        clock[0] = original["outcome_exit_time"]
        pipeline.tick()
        assert len(pipeline.store.records("symbol_outcomes")) == 31
        assert len(pipeline.store.records("outcomes")) == 1
        clock[0] = daily.window(date(2026, 10, 13))[0]
        # Supply next day's genuine-calendar synthetic fixture.
        pipeline.collector = lambda symbols, provider, helper, session=None: data_for(
            session or date(2026, 10, 13), clock[0]
        )
        pipeline.tick()
        second = pipeline.store.decision()
        assert second["random_seed"] != original["random_seed"]
        clock[0] = second["outcome_exit_time"]
        result = pipeline.tick()
        summary = result["comparison"]["evidence_pools"]["synthetic"]
        assert summary["valid_paired_days"] == summary["resolved_sessions"] == 2
        assert summary["resolved_symbol_counterfactuals"] == 62
        assert summary["arms"]["quant_only"]["paired_mean_base_net"] is not None
        assert pipeline.store.decision(original["decision_id"]) == original
        assert pipeline.store.records("executions") == []
        assert result["orders_submitted"] == 0
        artifact = tmp_path / "synthetic-e2e.json"
        artifact.write_text(json.dumps(result, indent=2))
    finally:
        pipeline.store.close()


def test_codex_failure_is_unavailable_not_cash_or_fabricated_decision(tmp_path):
    def failed(report):
        raise TimeoutError("synthetic timeout")

    pipeline, clock, _ = make_pipeline(tmp_path, assessor=failed)
    try:
        assert pipeline.tick()["run"]["failure_kind"] == "CODEX_ASSESSMENT_UNAVAILABLE"
        decision = pipeline.store.decision()
        assert pipeline.store.records("assessments") == []
        assert decision["comparisons"]["codex_only"]["status"] == "UNAVAILABLE"
        clock[0] = decision["outcome_exit_time"]
        result = pipeline.tick()
        outcome = pipeline.store.records("outcomes")[0]
        assert outcome["comparisons"]["codex_only"] is None
        assert outcome["comparisons"]["quant_codex"] is None
        summary = result["comparison"]["evidence_pools"]["synthetic"]
        assert summary["valid_paired_days"] == 0
        assert summary["arms"]["quant_only"]["observed_days"] == 1
        assert summary["arms"]["codex_only"]["unavailable_days"] == 1
    finally:
        pipeline.store.close()


def test_calendar_dst_holiday_early_close_and_missed_window(tmp_path):
    assert daily.window(date(2026, 12, 25)) is None
    assert daily.window(date(2026, 11, 27)) is not None  # 13:00 close supports horizon
    summer = datetime.fromtimestamp(daily.window(date(2026, 10, 30))[0], daily.NY)
    winter = datetime.fromtimestamp(daily.window(date(2026, 11, 2))[0], daily.NY)
    assert summer.hour == winter.hour == 10
    assert summer.utcoffset().total_seconds() == -14400
    assert winter.utcoffset().total_seconds() == -18000
    pipeline, clock, calls = make_pipeline(tmp_path)
    try:
        clock[0] += 181
        result = pipeline.tick()
        assert result["run"]["failure_kind"] == "MISSED_DECISION_WINDOW"
        pipeline.tick()
        assert calls == [] and pipeline.store.records("decisions") == []
        assert len(pipeline.store.records("sessions")) == 1
    finally:
        pipeline.store.close()


def test_interrupted_claim_is_terminal_and_never_retried(tmp_path):
    pipeline, _, calls = make_pipeline(tmp_path)
    try:
        pipeline.event(date(2026, 10, 12), "STARTED", status="STARTED")
        result = pipeline.tick()
        assert result["run"]["failure_kind"] == "INTERRUPTED"
        pipeline.tick()
        assert calls == [] and pipeline.store.records("decisions") == []
    finally:
        pipeline.store.close()


def test_stale_codex_evidence_is_rejected_without_backdating(tmp_path):
    pipeline, clock, _ = make_pipeline(tmp_path)

    def slow(report):
        clock[0] += 121
        return valid_assessment(report)

    pipeline.assessor = slow
    try:
        result = pipeline.tick()
        assert result["run"]["status"] == "INCOMPLETE"
        assert pipeline.store.records("decisions") == []
        assert "fresh" in result["run"]["reason"] or "stale" in result["run"]["reason"]
    finally:
        pipeline.store.close()


def test_data_failure_is_durable_without_agent_or_secondary_failure(tmp_path):
    pipeline, _, _ = make_pipeline(
        tmp_path, assessor=lambda r: pytest.fail("no Codex on failed scan")
    )

    def failed(*args, **kwargs):
        raise ValueError("synthetic provider unavailable")

    pipeline.collector = failed
    try:
        result = pipeline.tick()
        assert result["run"]["reason"] == "synthetic provider unavailable"
        assert result["run"]["decision_id"] is None
        assert result["orders_submitted"] == 0
        assert pipeline.store.records("decisions") == []
        assert list((tmp_path / "sessions").glob("*.json"))
    finally:
        pipeline.store.close()


def test_semantic_identity_is_stable_and_legacy_is_exact():
    assert ALPHA_VERSION == identity(SEMANTICS)[:16]
    assert all(semantic_version(v) == ALPHA_VERSION for v in LEGACY_ALPHA_VERSIONS)
    assert semantic_version("unknown-drift") == "unknown-drift"
    assert "read_text" not in Path("src/tradeagent/research/alpha_signals.py").read_text()


def test_cli_timeout_kills_group_and_never_returns_assessment(tmp_path):
    executable = tmp_path / "codex"
    executable.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n")
    executable.chmod(0o700)
    from tradeagent.research.opportunities import scan

    with pytest.raises(ValueError, match="deadline"):
        assess(scan(market(), now=NOW, candidate_count=1), executable=str(executable), timeout=0.05)


@pytest.mark.parametrize("case", ["invalid", "wrong_symbol", "tool", "cli_error"])
def test_cli_rejects_invalid_structured_output_and_tool_activity(tmp_path, case):
    from tradeagent.research.opportunities import scan

    report = scan(market(), now=NOW, candidate_count=1)
    document, _ = valid_assessment(report)
    events = []
    if case == "invalid":
        document["invented_return"] = 0.2
    if case == "wrong_symbol":
        document["assessments"][0]["symbol"] = "FAKE"
    if case == "tool":
        events = [{"item": {"type": "mcp_tool_call"}}]
    if case == "cli_error":
        events = [{"item": {"type": "error", "message": "genuine CLI failure"}}]
    executable = tmp_path / "codex"
    executable.write_text(
        "#!/usr/bin/env python3\nimport sys,json\nfrom pathlib import Path\n"
        + "Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text("
        + repr(json.dumps(document))
        + ")\n"
        + "print("
        + repr("\n".join(json.dumps(e) for e in events))
        + ")\n"
    )
    executable.chmod(0o700)
    with pytest.raises((ValueError, ValidationError)):
        assess(report, executable=str(executable))


def test_all_cash_abstention_is_valid_not_data_failure(tmp_path, monkeypatch):
    pipeline, _, _ = make_pipeline(tmp_path)
    original_scan = daily.scan

    def empty(*args, **kwargs):
        report = original_scan(*args, **kwargs)
        report["candidates"] = []
        return report

    monkeypatch.setattr(daily, "scan", empty)
    try:
        result = pipeline.tick()
        assert result["run"]["status"] == "COMPLETED"
        decision = pipeline.store.decision()
        assert all(
            a["shadow_direction"] == 0 and a["status"] == "VALID"
            for a in decision["comparisons"].values()
        )
        assert len(decision["quantitative_observations"]) == 31
    finally:
        pipeline.store.close()


def test_downtime_records_missing_sessions_without_backfilling(tmp_path):
    pipeline, clock, calls = make_pipeline(tmp_path)
    try:
        clock[0] -= 60  # enroll before capture, then simulate multi-day outage
        pipeline.tick()
        clock[0] = daily.window(date(2026, 10, 14))[0] + 181
        pipeline.tick()
        failures = [r for r in pipeline.store.records("sessions") if r.get("phase") == "FINAL"]
        assert {r["session_date"] for r in failures} == {"2026-10-12", "2026-10-13", "2026-10-14"}
        assert all(r["failure_kind"] == "MISSED_DECISION_WINDOW" for r in failures)
        assert calls == []
    finally:
        pipeline.store.close()


def test_legacy_forecasts_keep_bytes_and_contribute_semantically_identical_evidence(tmp_path):
    from test_opportunities import store_scan

    store, report = store_scan(tmp_path, market())
    try:
        original = store.decide(report, market(), now=NOW, holding_seconds=1800)
        old = copy.deepcopy(original)
        old["decision_id"] = "legacy-alpha-forecast"
        legacy = next(iter(LEGACY_ALPHA_VERSIONS))
        for c in old["quantitative_observations"]:
            for p in c["predictions"]:
                p["strategy_version"] = legacy
                p["prediction_id"] = "old-" + p["prediction_id"]
                p["features"]["sampling_key"] = "old-" + p["features"]["sampling_key"]
        store.save("decisions", old, old["decision_id"], NOW)
        encoded = store.db.execute(
            "SELECT payload FROM opportunity_decisions WHERE id=?", (old["decision_id"],)
        ).fetchone()[0]
        store.resolve(market(end=NOW + 2100), NOW + 2100)
        assert {r["version"] for r in store.prior_rows()} == {ALPHA_VERSION}
        assert (
            store.db.execute(
                "SELECT payload FROM opportunity_decisions WHERE id=?", (old["decision_id"],)
            ).fetchone()[0]
            == encoded
        )
        assert {
            p["strategy_version"]
            for c in store.decision(old["decision_id"])["quantitative_observations"]
            for p in c["predictions"]
        } == {legacy}
    finally:
        store.close()


def test_legacy_active_experimental_plan_remains_valid_unknown_version_fails(tmp_path):
    from dataclasses import replace

    from test_opportunity_policies import freeze_experimental

    from tradeagent.model import Halt
    from tradeagent.research.domain import Payload
    from tradeagent.research.tradeplan import plan_from_dict, validate_execution_plan

    store, decision = freeze_experimental(tmp_path)
    try:
        plan = plan_from_dict(decision["selected_plan"])
        legacy = replace(
            plan,
            forecast=Payload.of(
                {**plan.forecast.plain(), "version": next(iter(LEGACY_ALPHA_VERSIONS))}
            ),
        )
        validate_execution_plan(legacy, legacy.entry_after)
        drift = replace(
            plan, forecast=Payload.of({**plan.forecast.plain(), "version": "unknown-drift"})
        )
        with pytest.raises(Halt, match="frozen signal"):
            validate_execution_plan(drift, drift.entry_after)
    finally:
        store.close()


def test_installer_keeps_venv_symlink_and_only_installs_shadow(tmp_path, monkeypatch):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("install_shadow", "scripts/install_shadow.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    target = tmp_path / "base-python"
    target.write_text("synthetic interpreter")
    interpreter = tmp_path / "venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(target)
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(installer.subprocess, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install_shadow.py",
            "--daily",
            "--python",
            str(interpreter),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )
    installer.main()
    units = tmp_path / ".config/systemd/user"
    service = (units / "tradeagent-daily-research.service").read_text()
    timer = (units / "tradeagent-daily-research.timer").read_text()
    assert f"ExecStart={interpreter} -m tradeagent.prospective.daily" in service
    assert "--live" not in service and "execute" not in service
    assert "America/New_York" in timer and "Persistent=true" in timer
    assert calls[-1] == [
        "systemctl",
        "--user",
        "enable",
        "--now",
        "tradeagent-daily-research.timer",
    ]
