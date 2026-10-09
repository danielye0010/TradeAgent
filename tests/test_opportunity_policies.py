"""Offline policy/window tests. All signals and owner files are explicit fixtures."""

import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from test_opportunities import NOW, assessment, market, store_scan
from test_opportunity_coverage import entire_market

from tradeagent import opportunity_cli as cli
from tradeagent import opportunity_live as live
from tradeagent.calendar import session_bounds
from tradeagent.model import Config, Halt, Risk
from tradeagent.research.opportunities import scan
from tradeagent.research.opportunity_workflow import DailyResearch, selected_execution
from tradeagent.research.tradeplan import (
    EVIDENCE_GATED,
    EXPERIMENTAL,
    plan_dict,
    to_execution_intent,
    validate_execution_plan,
)


def prospective_fixture():
    # A real calendar label with invented bars, isolated in tmp_path; no HTTP.
    now = datetime(2026, 10, 9, 14, tzinfo=timezone.utc).timestamp()
    d = market("prospective")
    shift = now - NOW
    d["observed_at"] = now
    d["session_open"], d["session_close"] = session_bounds(datetime(2026, 10, 9).date())
    for b in d["bars"]:
        for k in ("start", "end", "available_at"):
            b[k] += shift
    for q in d["quotes"].values():
        q.update(asof=now, observed_at=now)
    for r in d["references"].values():
        r["previous_close_time"] += shift
    return d, now


def freeze_experimental(tmp_path, *, stance="watch", signal=True):
    d, now = prospective_fixture()
    if not signal:
        d["references"] = {}
    report = scan(d, now=now, candidate_count=3)
    report["execution_universe"] = ["QQQ", "IWM"]
    store = DailyResearch(tmp_path)
    store.save("scans", report, report["scan_id"], now)
    a = assessment(report, stance=stance)
    a.update(observed_at=now, sources=[])
    store.assessment(a, now)
    result = store.decide(report, d, now=now, holding_seconds=1800, execution_policy=EXPERIMENTAL)
    return store, result


@pytest.mark.parametrize("extra", [-1, 0])
def test_late_forecast_and_rounded_endpoint_not_created(tmp_path, extra):
    d = market()
    d["session_close"] = NOW + 2100 + extra
    store, report = store_scan(tmp_path, d)
    try:
        # 1 second after a minute needs the following endpoint: no shortening.
        now = NOW + (1 if extra == 0 else 0)
        result = store.decide(report, d, now=now, holding_seconds=1800)
        assert result["final_decision"] == "NO_TRADE"
        assert result["selected_plan"] is None and result["outcome_exit_time"] is None
        assert not result["research_window"]["valid"]
        assert all(not c["predictions"] for c in result["quantitative_observations"])
        failures = store.terminal_windows(now)
        assert failures[0]["status"] == "NO_FORECAST_SESSION_WINDOW"
        assert store.pending_sessions(NOW + 86400) == []
        assert store.performance()["pending_decisions"] == 0
    finally:
        store.close()


def test_last_realizable_endpoint_at_close_resolves_exact_frozen_horizon(tmp_path):
    d = market()
    d["session_close"] = NOW + 2100
    store, report = store_scan(tmp_path, d)
    try:
        result = store.decide(report, d, now=NOW, holding_seconds=1800)
        assert result["research_window"]["valid"]
        assert all(
            p["horizon"] == 2100
            for c in result["quantitative_observations"]
            for p in c["predictions"]
        )
        resolved = store.resolve(market(end=NOW + 2100), NOW + 2100)
        assert len(resolved["symbol_resolved"]) == 3
        assert resolved["resolved"] and not resolved["terminal_unresolvable"]
    finally:
        store.close()


def test_legacy_impossible_forecast_gets_append_only_terminal_evidence(tmp_path):
    store, report = store_scan(tmp_path, market())
    try:
        original = store.decide(report, market(), now=NOW, holding_seconds=1800)
        old = copy.deepcopy(original)
        old["decision_id"] = "legacy-late-record"
        old["session_close"] = NOW + 2099
        old.pop("research_window")
        store.save("decisions", old, old["decision_id"], NOW)
        encoded = json.dumps(old, sort_keys=True)
        first = store.terminal_windows(NOW + 2100)
        assert len(first) == 1 and first[0]["status"] == "UNRESOLVABLE_SESSION_WINDOW"
        assert "not missing market data" in first[0]["reason"]
        assert store.terminal_windows(NOW + 2200) == first
        assert json.dumps(store.decision(old["decision_id"]), sort_keys=True) == encoded
        result = store.resolve(market(end=NOW + 2100), NOW + 2100)
        assert all(o["decision_id"] != old["decision_id"] for o in result["resolved"])
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.db.execute("DELETE FROM opportunity_resolution_failures")
    finally:
        store.close()


def test_missing_data_is_pending_not_terminal(tmp_path):
    store, report = store_scan(tmp_path, market())
    try:
        store.decide(report, market(), now=NOW, holding_seconds=1800)
        later = market(end=NOW + 2100)
        later["bars"] = [b for b in later["bars"] if b["symbol"] != "QQQ"]
        r = store.resolve(later, NOW + 2100)
        assert r["pending"] and not r["terminal_unresolvable"]
    finally:
        store.close()


def test_wider_owner_allowlist_uses_same_universe_and_does_not_write_config(tmp_path, monkeypatch):
    d = entire_market()
    path = tmp_path / "owner.toml"
    path.write_text('[live]\nsymbols = ["AAPL", "MSFT", "QQQ", "IWM"]\n')
    original = path.read_bytes()
    monkeypatch.setattr(
        "tradeagent.execution_policy.load_live_config",
        lambda p: SimpleNamespace(
            config=SimpleNamespace(allowed_symbols=["AAPL", "MSFT", "QQQ", "IWM"])
        ),
    )
    report = cli.align_live_universe(scan(d, now=NOW, candidate_count=3), path)
    assert {c["symbol"] for c in report["candidates"]} <= {"AAPL", "MSFT", "QQQ", "IWM"}
    assert "AAPL" not in {c["symbol"] for c in report["excluded_live_opportunities"]}
    store = DailyResearch(tmp_path / "research")
    try:
        store.save("scans", report, report["scan_id"], NOW)
        result = store.decide(report, d, now=NOW)
        assert len(result["quantitative_observations"]) == 31
        assert path.read_bytes() == original
    finally:
        store.close()


def test_experimental_is_explicit_frozen_signal_not_codex_confidence(tmp_path):
    store, d = freeze_experimental(tmp_path)
    try:
        assert d["execution_policy"] == EXPERIMENTAL
        assert d["selected_plan"] and d["final_decision"] == "UNDERLYING"
        assert all(a["plan"] is None for a in d["comparisons"].values())
        assert d["experimental"]["symbol"] == "QQQ"
        plan, prediction = selected_execution(d, "experimental")
        assert prediction.direction == 1
        assert plan.economics.value["days"] == 0
        assert plan.economics.value["profitability_established"] is False
        validate_execution_plan(plan, plan.entry_after)
        with pytest.raises(ValueError, match="mode must match"):
            selected_execution(d)
        with pytest.raises(Halt, match="mode must match"):
            live.execute_opportunity(store, d["decision_id"], "unused", live=True)
        with pytest.raises(Halt, match="explicit owner LIVE"):
            live.execute_opportunity(store, d["decision_id"], "unused", mode="experimental")
        # The same sparse economics remain rejected under the unchanged policy.
        ordinary = store.decide(
            store.scan(), prospective_fixture()[0], now=d["decision_time"], holding_seconds=1800
        )
        assert ordinary["execution_policy"] == EVIDENCE_GATED
        assert ordinary["selected_plan"] is None
    finally:
        store.close()


def test_experimental_without_signal_abstains_even_with_long_narrative(tmp_path):
    store, d = freeze_experimental(tmp_path, stance="long", signal=False)
    try:
        assert d["experimental"]["plan"] is None and d["final_decision"] == "NO_TRADE"
    finally:
        store.close()


@pytest.mark.parametrize(
    "change", ["stale_quote", "price_cap", "expired", "version", "session", "short", "historical"]
)
def test_experimental_still_checks_quote_signal_window_and_identity(tmp_path, change):
    store, d = freeze_experimental(tmp_path)
    try:
        plan, _ = selected_execution(d, "experimental")
        now = plan.entry_after
        quote = dict(asof=now, observed_at=now, ask=plan.decision.entry_limit)
        if change == "stale_quote":
            quote.update(asof=now - 121, observed_at=now)
        if change == "price_cap":
            quote["ask"] += 1
        if change == "expired":
            now = plan.entry_deadline
        if change in {"version", "session", "short"}:
            from tradeagent.research.domain import Payload

            f = plan.forecast.plain()
            f.update(
                {"version": "unknown"}
                if change == "version"
                else {"session_close": f["session_close"] + 60}
                if change == "session"
                else {"direction": -1}
            )
            plan = replace(plan, forecast=Payload.of(f))
        if change == "historical":
            plan = replace(plan, research_only=True)
        with pytest.raises(Halt):
            to_execution_intent(
                plan, None, now, order_type="market", dollar_amount="5", quote=quote
            )
    finally:
        store.close()


def test_day_reservation_is_atomic_durable_and_owner_scoped(tmp_path):
    store, d = freeze_experimental(tmp_path / "research")
    try:
        plan, _ = selected_execution(d, "experimental")
        settings = SimpleNamespace(
            directory=tmp_path / "owner", options={"entry": {"dollar_amount": "5"}}
        )

        def reserve(_):
            try:
                return live.reserve_experimental_day(settings, d, plan, plan.entry_after)
            except Halt:
                return None

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(reserve, range(2)))
        assert sum(r is not None for r in results) == 1
        receipt = json.loads(
            next(
                settings.directory.with_name(settings.directory.name + ".experimental-days").glob(
                    "*.json"
                )
            ).read_text()
        )
        assert receipt["decision_id"] == d["decision_id"]
        # A different research state / decision still shares the owner allowance.
        with pytest.raises(Halt, match="already reserved"):
            live.reserve_experimental_day(
                settings, dict(d, decision_id="another"), plan, plan.entry_after
            )
        settings.options["entry"]["dollar_amount"] = "6"
        with pytest.raises(Halt, match=r"existing owner \$5"):
            live.reserve_experimental_day(settings, d, plan, plan.entry_after)
    finally:
        store.close()


def test_experimental_uses_existing_engine_and_limits_new_attempts(tmp_path, monkeypatch):
    store, d = freeze_experimental(tmp_path / "research")
    try:
        plan, _ = selected_execution(d, "experimental")
        settings = SimpleNamespace(
            directory=tmp_path / "owner",
            config=Config(allowed_symbols=["QQQ", "IWM"]),
            risk=Risk(),
            account_sha256=None,
            options={"entry": {"dollar_amount": "5", "order_type": "market"}, "max_notional": "25"},
            validate=lambda: None,
        )
        monkeypatch.setattr(live, "load_live_config", lambda p: settings)
        monkeypatch.setattr(live, "check_run_state", lambda s: None)
        monkeypatch.setattr(live.time, "time", lambda: plan.entry_after)
        calls = []

        def engine(s, **kw):
            calls.append(kw)
            assert s is settings and kw["plan"].execution_policy == EXPERIMENTAL
            return {
                "status": "NO_TRADE",
                "submission_status": "NOT_SUBMITTED",
                "broker_order_count": 0,
            }

        monkeypatch.setattr(live, "run_live", engine)
        result = live.execute_opportunity(
            store,
            d["decision_id"],
            "offline-only",
            live=True,
            mode="experimental",
            emit=lambda *a: None,
        )
        assert result["status"] == "NO_TRADE" and len(calls) == 1
        assert result["experimental_day_receipt"]
        # Same attempt does not replay. Existing reservation is never reset on failure.
        again = live.execute_opportunity(
            store,
            d["decision_id"],
            "offline-only",
            live=True,
            mode="experimental",
            emit=lambda *a: None,
        )
        assert again["status"] == "HALTED" and len(calls) == 1
        assert store.performance()["actual_performance"][0]["execution_policy"] == EXPERIMENTAL
    finally:
        store.close()


def test_reservation_survives_whole_owner_lifecycle_archival(tmp_path):
    store, d = freeze_experimental(tmp_path / "research")
    try:
        plan, _ = selected_execution(d, "experimental")
        owner = tmp_path / "owner"
        owner.mkdir()
        settings = SimpleNamespace(directory=owner, options={"entry": {"dollar_amount": "5"}})
        path = live.reserve_experimental_day(settings, d, plan, plan.entry_after)
        # Simulate the documented existing new_live_run whole-directory move.
        owner.rename(tmp_path / "closed-history")
        owner.mkdir()
        assert __import__("pathlib").Path(path).exists()
        with pytest.raises(Halt, match="already reserved"):
            live.reserve_experimental_day(
                settings, dict(d, decision_id="new"), plan, plan.entry_after
            )
    finally:
        store.close()


def test_experimental_handoff_retains_owner_risk_and_authorized_symbols(tmp_path):
    from tradeagent.oneshot import paper_snapshot
    from tradeagent.research.tradeplan import execution_handoff
    from tradeagent.simulator import SimClock

    store, d = freeze_experimental(tmp_path)
    try:
        plan, p = selected_execution(d, "experimental")
        now = plan.entry_after
        account = paper_snapshot(SimClock(now))
        account.fractional_tradable = {"QQQ": True}
        entry = {"order_type": "market", "dollar_amount": "5"}
        allowed = Config(allowed_symbols=["QQQ", "IWM"])
        result = execution_handoff(plan, p, account, allowed, Risk(), now, "25", entry)
        assert result["intent"]["dollar_amount"] == "5"
        assert result["provenance"]["economic_plan"]["execution_policy"] == EXPERIMENTAL
        with pytest.raises(Halt, match="rejected"):
            execution_handoff(plan, p, account, allowed, Risk(), now, "1", entry)
        with pytest.raises(Halt):
            execution_handoff(
                plan, p, account, Config(allowed_symbols=["IWM"]), Risk(), now, "25", entry
            )
    finally:
        store.close()


def test_experimental_outcomes_are_separate_and_wait_for_selected_symbol(tmp_path):
    d, now = prospective_fixture()
    report = scan(d, now=now, candidate_count=3)
    report["execution_universe"] = ["QQQ", "IWM"]
    # Scanner shortlist lacks QQQ; experimental quantitative selection is independent.
    report["candidates"] = [c for c in report["candidate_pool"] if c["symbol"] == "IWM"]
    store = DailyResearch(tmp_path)
    try:
        store.save("scans", report, report["scan_id"], now)
        decision = store.decide(
            report, d, now=now, holding_seconds=1800, execution_policy=EXPERIMENTAL
        )
        assert decision["experimental"]["symbol"] == "QQQ"
        assert {c["symbol"] for c in decision["ranked_candidates"]} == {"IWM"}
        plan, _ = selected_execution(decision, "experimental")
        validate_execution_plan(plan, plan.entry_after)
        later = market("prospective", end=NOW + 2100)
        shift = now - NOW
        later["observed_at"] += shift
        later["session_open"], later["session_close"] = d["session_open"], d["session_close"]
        for b in later["bars"]:
            for k in ("start", "end", "available_at"):
                b[k] += shift
        missing = copy.deepcopy(later)
        missing["bars"] = [b for b in missing["bars"] if b["symbol"] != "QQQ"]
        assert store.resolve(missing, now + 2100)["resolved"] == []
        resolved = store.resolve(later, now + 2100)
        assert resolved["resolved"][0]["experimental"]["entry_time"] == plan.entry_after
        perf = store.performance()
        assert perf["evidence_pools"] == {}  # no experimental mixing into v1 paired estimates
        assert perf["experimental_modeled_outcomes"][0]["decision_id"] == decision["decision_id"]
        assert perf["actual_performance_by_policy"] == {EVIDENCE_GATED: [], EXPERIMENTAL: []}
    finally:
        store.close()


def test_legacy_evidence_gated_plan_roundtrip_and_identity_are_preserved(tmp_path):
    from dataclasses import asdict

    from tradeagent.research.domain import identity
    from tradeagent.research.tradeplan import plan_from_dict

    store, d = freeze_experimental(tmp_path)
    try:
        legacy_raw = d["ranked_candidates"][0]["economic_plans"][0]
        assert "execution_policy" not in legacy_raw
        plan = plan_from_dict(legacy_raw)
        assert plan_dict(plan) == legacy_raw and plan.execution_policy == EVIDENCE_GATED
        old_identity = asdict(plan)
        old_identity.pop("execution_policy")
        assert plan.plan_id == identity(old_identity)
    finally:
        store.close()


def test_experimental_expired_active_lifecycle_recovers_without_new_day_claim(
    tmp_path, monkeypatch
):
    store, d = freeze_experimental(tmp_path / "research")
    try:
        plan, p = selected_execution(d, "experimental")
        owner = tmp_path / "owner"
        (owner / "agent").mkdir(parents=True)
        with sqlite3.connect(owner / "agent/state.sqlite3") as db:
            db.execute("CREATE TABLE one_shot_meta(key TEXT PRIMARY KEY, payload TEXT)")
            db.execute(
                "INSERT INTO one_shot_meta VALUES(?,?)",
                (
                    "decision",
                    json.dumps(
                        {
                            "provenance": {
                                "prediction_id": p.prediction_id,
                                "economic_plan": plan_dict(plan),
                            }
                        }
                    ),
                ),
            )
        settings = SimpleNamespace(
            directory=owner,
            config=Config(allowed_symbols=["QQQ", "IWM"]),
            risk=Risk(),
            account_sha256=None,
            options={"entry": {"dollar_amount": "5", "order_type": "market"}, "max_notional": "25"},
            validate=lambda: None,
        )
        monkeypatch.setattr(live, "load_live_config", lambda path: settings)
        monkeypatch.setattr(live, "check_run_state", lambda settings: {"active": True})
        monkeypatch.setattr(live, "active_settings", lambda settings: settings)
        monkeypatch.setattr(live.time, "time", lambda: plan.exit_at + 1)
        monkeypatch.setattr(
            live,
            "reserve_experimental_day",
            lambda *a: pytest.fail("recovery tried reserving another entry"),
        )
        calls = []

        def recover(settings, **kw):
            calls.append(kw)
            assert kw["recover"] is True
            return {
                "status": "NO_TRADE",
                "submission_status": "NOT_SUBMITTED",
                "broker_order_count": 0,
            }

        monkeypatch.setattr(live, "run_live", recover)
        result = live.execute_opportunity(
            store,
            d["decision_id"],
            "offline-only",
            live=True,
            mode="experimental",
            emit=lambda *a: None,
        )
        assert result["status"] == "NO_TRADE" and len(calls) == 1
    finally:
        store.close()


def test_real_local_toml_loader_accepts_wider_universe_without_code_or_size_changes(tmp_path):
    from tradeagent.execution_policy import load_live_config
    from tradeagent.research.opportunities import UNIVERSE

    config = tmp_path / "owner.toml"
    config.write_text(
        "[live]\nenabled = true\nsymbols = "
        + json.dumps(list(UNIVERSE))
        + "\nstate_dir = "
        + json.dumps(str(tmp_path / "owner"))
        + "\n[broker]\noauth_helper = "
        + json.dumps(str(tmp_path / "offline-helper.py"))
        + '\n[risk]\n[exit]\n[entry]\ndollar_amount = "5"\norder_type = "market"\n'
    )
    config.chmod(0o600)
    before = config.read_bytes()
    settings = load_live_config(config)
    assert settings.config.allowed_symbols == list(UNIVERSE)
    assert settings.options["entry"]["dollar_amount"] == "5"
    settings.config.validate()
    assert config.read_bytes() == before


def test_already_recorded_impossible_outcome_is_preserved_but_not_economic_evidence(tmp_path):
    store, report = store_scan(tmp_path, market())
    try:
        d = store.decide(report, market(), now=NOW, holding_seconds=1800)
        old = copy.deepcopy(d)
        old["decision_id"] = "old-impossible-already-resolved"
        old["session_close"] = NOW + 2099
        old.pop("research_window")
        store.save("decisions", old, old["decision_id"], NOW)
        outcome = {
            "decision_id": old["decision_id"],
            "evidence_kind": "synthetic",
            "comparisons": {k: {"base_net": 0.1} for k in old["comparisons"]},
            "economic_rows": [{"prediction_id": "never-use-impossible-row"}],
        }
        store.save("outcomes", outcome, old["decision_id"], NOW + 2100)
        assert store.terminal_windows(NOW + 2200)[0]["status"] == "UNRESOLVABLE_SESSION_WINDOW"
        assert store.records("outcomes")[0] == outcome
        assert store.prior_rows() == []
        assert store.performance()["evidence_pools"] == {}
        assert store.performance()["pending_decisions"] == 1  # valid original remains pending
    finally:
        store.close()
