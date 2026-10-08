"""Production owner recovery; all broker traffic replaced by deterministic test replies."""

import hashlib
import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_broker_contract import NOW, RawReadFake
from test_fractional_live import fractional_runtime
from test_production_oneshot import owner_mock_runtime

from tradeagent.broker import Broker
from tradeagent.execution_policy import active_settings, load_live_config
from tradeagent.model import Config, Halt, Risk, dec, digest
from tradeagent.oneshot import (
    choose_entry,
    new_live_run,
    reconcile_live,
    run_live,
    validated_plan_entry,
)
from tradeagent.oneshot_cli import main, progress
from tradeagent.research.expressions import TradePlan
from tradeagent.risk import check_state
from tradeagent.state import State


@pytest.mark.parametrize("scenario", ["partial_fill", "rejected"])
def test_explicit_recover_exits_only_verified_remaining_owned_quantity(
    tmp_path, monkeypatch, scenario
):
    invoke, sim, calls, target, path = owner_mock_runtime(
        tmp_path, monkeypatch, exit_scenario=scenario
    )
    try:
        first = invoke()
        assert first["status"] == "HALTED" and not first["flat_bot_position"]
        remaining = dec(first["bot_owned_residual"])
        original = sim._place

        def filled(name, args):
            sim.scenario = "full_fill"
            return original(name, args)

        monkeypatch.setattr(sim, "_place", filled)
        recovered = run_live(load_live_config(path), recover=True)
        assert recovered["flat_bot_position"] and recovered["cash_reconciled"], recovered
        orders = [
            m["params"]["arguments"]
            for m in calls
            if m.get("params", {}).get("name") == "place_equity_order"
        ]
        assert [o["side"] for o in orders] == ["buy", "sell", "sell"]
        assert dec(orders[-1]["quantity"]) == remaining
        assert len({o["ref_id"] for o in orders}) == 3
        assert run_live(load_live_config(path), recover=True)["duplicate_suppressed"]
        assert sum(m.get("params", {}).get("name") == "place_equity_order" for m in calls) == 3
        assert new_live_run(load_live_config(path))["status"] == "NEW_RUN_READY"
    finally:
        sim.close()


def test_recover_cannot_initiate_entry_without_existing_lifecycle(tmp_path, monkeypatch):
    invoke, sim, calls, target, path = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        with pytest.raises(Halt, match="existing"):
            run_live(load_live_config(path), recover=True)
        assert calls == [] and not target.exists()
    finally:
        sim.close()


def test_semantic_toml_and_active_parameters_survive_upgrade(tmp_path, monkeypatch):
    invoke, sim, calls, target, path = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        initial = load_live_config(path)
        path.write_text(path.read_text() + "\n# harmless formatting\n")
        assert load_live_config(path).file_hash == initial.file_hash
        assert invoke()["status"] == "COMPLETED"
        path.write_text(path.read_text().replace('max_notional = "25"', 'max_notional = "1000"'))
        monkeypatch.setattr(
            "tradeagent.execution_policy.package_hash", lambda: "UNRELATED_APPLICATION_UPGRADE"
        )
        assert active_settings(load_live_config(path)).options["max_notional"] == "25"
        assert invoke()["duplicate_suppressed"]
        assert new_live_run(load_live_config(path))["status"] == "NEW_RUN_READY"
        assert active_settings(load_live_config(path)).options["max_notional"] == "1000"
    finally:
        sim.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_protocol_version", 999),
        ("state_schema_version", 999),
        ("schema_hash", "unknown"),
    ],
)
def test_incompatible_context_fails_before_broker_without_reset(
    tmp_path, monkeypatch, field, value
):
    invoke, sim, calls, target, path = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        assert invoke()["status"] == "COMPLETED"
        marker = target / "live-run.json"
        artifact = json.loads(marker.read_text())
        artifact["policy"][field] = value
        marker.write_text(json.dumps(artifact))
        database = target / "agent/state.sqlite3"
        before = hashlib.sha256(database.read_bytes()).hexdigest(), len(calls)
        with pytest.raises(Halt, match="incompatible"):
            invoke()
        assert before == (hashlib.sha256(database.read_bytes()).hexdigest(), len(calls))
    finally:
        sim.close()


def test_unknown_recovery_and_status_never_duplicate_entry(tmp_path, monkeypatch):
    invoke, sim, requested, target, path = fractional_runtime(tmp_path, monkeypatch, mode="unknown")
    try:
        assert invoke()["submission_status"] == "SUBMISSION_UNKNOWN"
        before = (target / "agent/state.sqlite3").read_bytes()
        status = reconcile_live(load_live_config(path), persist=False)
        assert status["submission_status"] == "SUBMISSION_UNKNOWN"
        assert (target / "agent/state.sqlite3").read_bytes() == before
        assert (
            run_live(load_live_config(path), recover=True)["submission_status"]
            == "SUBMISSION_UNKNOWN"
        )
        assert len(requested) == 1
        with pytest.raises(Halt):
            new_live_run(load_live_config(path))
    finally:
        sim.close()


def test_multiple_accounts_selected_and_unrelated_assets_accounted():
    raw = RawReadFake()
    second = deepcopy(raw.payloads["get_accounts"]["accounts"][0])
    second["account_number"] = "SECOND_TEST_ONLY"
    raw.payloads["get_accounts"]["accounts"].append(second)
    with pytest.raises(Halt, match="select one"):
        Broker(raw, Config(mode="LIVE", allowed_symbols=["SPY"]), Risk()).accounts()
    raw.payloads["get_portfolio"].update(
        crypto_value="100", options_value="50", total_value="10150"
    )
    broker = Broker(
        raw,
        Config(
            mode="LIVE", allowed_symbols=["SPY"], account_selector=digest(second["account_number"])
        ),
        Risk(),
    )
    snapshot = broker.snapshot(NOW.timestamp())
    assert broker.account["account_number"] == second["account_number"]
    assert snapshot.other_asset_values == {"crypto_value": dec(100), "options_value": dec(50)}
    check_state(snapshot, Risk(), NOW.timestamp())
    raw.payloads["get_portfolio"]["crypto_value"] = None
    with pytest.raises(Halt):
        broker.snapshot(NOW.timestamp())


def test_unrelated_same_symbol_holding_not_sold(tmp_path, monkeypatch):
    invoke, sim, requested, target, path = fractional_runtime(tmp_path, monkeypatch)
    sim.initial.positions = {"AAPL": dec(".02")}
    sim.initial.available = {"AAPL": dec(".02")}
    sim.initial.nav += 10
    try:
        result = invoke()
        assert result["status"] == "COMPLETED", result
        assert result["final_positions"] == {"AAPL": dec("0.02")}
        assert dec(requested[-1]["quantity"]) == dec(".01")
        assert result["flat_bot_position"]
    finally:
        sim.close()


def test_owner_limits_without_development_ceiling(tmp_path, monkeypatch):
    invoke, sim, calls, target, path = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        path.write_text(
            path.read_text()
            .replace('max_notional = "25"', 'max_notional = "1000"')
            .replace(
                "[risk]",
                '[risk]\nmax_positions = 20\nmin_cash_fraction = "0"\nmax_daily_turnover_fraction = "5"',
            )
            .replace("hold_seconds = 0", "hold_seconds = 90000")
            .replace("polls = 2", "polls = 100")
        )
        settings = load_live_config(path)
        assert settings.risk.max_positions == 20 and settings.options["polls"] == 100
        assert settings.options["hold_seconds"] == 90000
        assert settings.risk.min_cash_fraction == "0"
    finally:
        sim.close()


def test_explicit_selection_no_cheapest_or_unrequested_fallback(tmp_path, monkeypatch):
    invoke, sim, calls, target, path = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        settings = load_live_config(path)
        snapshot = sim.snapshot()
        config = replace(settings.config, allowed_symbols=["QQQ", "IWM"])
        candidate, reasons = choose_entry(snapshot, config, settings.risk, sim.clock(), "25")
        assert candidate.symbol == "QQQ"
        snapshot.tradable["QQQ"] = False
        candidate, reasons = choose_entry(snapshot, config, settings.risk, sim.clock(), "25")
        assert candidate is None and reasons[0].startswith("QQQ:")
        candidate, reasons = choose_entry(
            snapshot,
            config,
            settings.risk,
            sim.clock(),
            "25",
            {"preferred_symbols": ["QQQ", "IWM"], "order_type": "market", "quantity": "1"},
        )
        assert candidate.symbol == "IWM" and reasons[0].startswith("QQQ:")
    finally:
        sim.close()


def test_validated_plan_boundary_preserves_provenance(tmp_path, monkeypatch):
    invoke, sim, calls, target, path = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        settings = load_live_config(path)
        prediction = SimpleNamespace(
            prediction_id="test-prediction",
            symbol="IWM",
            decision_time=sim.clock(),
            direction=1,
            strategy_id="test-strategy",
            version="v1",
            snapshot_id="test-snapshot",
        )
        plan = TradePlan(
            prediction.prediction_id, "UNDERLYING", "IWM", None, "validated test decision"
        )
        intent, provenance = validated_plan_entry(
            plan,
            prediction,
            sim.snapshot(),
            settings.config,
            settings.risk,
            sim.clock(),
            "25",
            {"order_type": "market", "quantity": "1"},
        )
        assert intent.symbol == "IWM" and provenance["strategy_id"] == "test-strategy"
        prediction.decision_time -= 1000
        with pytest.raises(Halt, match="stale"):
            validated_plan_entry(
                plan,
                prediction,
                sim.snapshot(),
                settings.config,
                settings.risk,
                sim.clock(),
                "25",
                {"order_type": "market", "quantity": "1"},
            )
    finally:
        sim.close()


def test_cli_status_read_only_and_progress_actual_full_lifecycle(tmp_path, monkeypatch, capsys):
    invoke, sim, calls, target, path = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        result = run_live(load_live_config(path), observer=progress)
        assert result["status"] == "COMPLETED"
        emitted = capsys.readouterr().err
        for stage in (
            "BROKER_CONNECTED",
            "REVIEWED",
            "SUBMITTED",
            "ENTRY_FILLED",
            "EXIT_DUE",
            "EXIT_SUBMITTED",
            "EXIT_FILLED",
            "RECONCILED",
        ):
            assert stage in emitted, emitted
        before = (target / "agent/state.sqlite3").read_bytes()
        count = len(calls)
        assert main(["status", "--config", str(path)]) == 0
        status = json.loads(capsys.readouterr().out)
        assert status["reconciliation_status"] == "RECONCILED" and status["flat_bot_position"]
        assert (target / "agent/state.sqlite3").read_bytes() == before
        assert not any(
            m.get("params", {}).get("name", "").startswith(("review_", "place_", "cancel_"))
            for m in calls[count:]
        )
    finally:
        sim.close()


def test_local_owner_lock_ignores_historical_lease_without_deleting_it(tmp_path, monkeypatch):
    state = State(tmp_path / "state")
    try:
        monkeypatch.setattr(
            "tradeagent.state.run_lock.acquire",
            lambda *a, **k: pytest.fail("owner must not acquire a second lease"),
        )
        with state.lock(120, local_owner=True):
            with pytest.raises(Halt, match="process lock"):
                with state.lock(120, local_owner=True):
                    pass
    finally:
        state.close()


def test_incompatible_sqlite_version_never_mutates_evidence(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir()
    database = directory / "state.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA user_version=999")
    before = database.read_bytes()
    with pytest.raises(Halt, match="incompatible"):
        State(directory)
    assert database.read_bytes() == before


def test_semantic_decimal_spelling_is_equivalent(tmp_path, monkeypatch):
    invoke, sim, calls, target, path = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        before = load_live_config(path).file_hash
        path.write_text(
            path.read_text()
            .replace('max_notional = "25"', 'max_order_value = "25.00"')
            .replace("[risk]", '[risk]\nmin_cash_fraction = "0.2000"')
        )
        assert load_live_config(path).file_hash == before
    finally:
        sim.close()


def test_compatible_legacy_receipt_retained_and_archived(tmp_path, monkeypatch):
    invoke, sim, calls, target, path = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        assert invoke()["status"] == "COMPLETED"
        marker = target / "live-run.json"
        artifact = json.loads(marker.read_text())
        for field in (
            "application_version",
            "state_schema_version",
            "execution_protocol_version",
            "config",
            "risk",
        ):
            artifact["policy"].pop(field)
        artifact["policy"]["package_hash"] = "HISTORICAL_BUILD_ID"
        artifact["policy"]["owner_config_hash"] = "HISTORICAL_TOML_BYTES"
        artifact["policy"]["permissions"]["max_exits"] = 1
        marker.write_text(json.dumps(artifact))
        receipt = path.with_name(path.name + ".run.json")
        receipt.write_text(json.dumps({"marker_hash": digest(artifact)}))
        before = (
            receipt.read_bytes(),
            marker.read_bytes(),
            (target / "agent/state.sqlite3").read_bytes(),
        )
        assert (
            reconcile_live(load_live_config(path), persist=False)["reconciliation_status"]
            == "RECONCILED"
        )
        assert before == (
            receipt.read_bytes(),
            marker.read_bytes(),
            (target / "agent/state.sqlite3").read_bytes(),
        )
        assert invoke()["duplicate_suppressed"]
        archive = new_live_run(load_live_config(path))
        assert not receipt.exists()
        assert (Path(archive["archived_run"]) / "live-run.json").read_bytes() == before[1]
    finally:
        sim.close()


def test_broker_version_metadata_does_not_override_structural_contract():
    from tradeagent.schema import Contracts

    contracts = Contracts()
    contracts.check_current(contracts.tools, "compatible-new-build", require_version=False)
    changed = deepcopy(contracts.tools)
    changed["place_equity_order"]["inputSchema"]["properties"]["quantity"]["type"] = "integer"
    with pytest.raises(Halt, match="schema drift"):
        contracts.check_current(changed, "compatible-new-build", require_version=False)


def test_archive_preserves_compatible_legacy_database_bytes(tmp_path, monkeypatch):
    invoke, sim, calls, target, path = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        assert invoke()["status"] == "COMPLETED"
        database = target / "agent/state.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute("PRAGMA user_version=0")
        before = database.read_bytes()
        archive = new_live_run(load_live_config(path))
        assert (Path(archive["archived_run"]) / "agent/state.sqlite3").read_bytes() == before
    finally:
        sim.close()


def test_finished_orders_need_recover_to_finalize_interrupted_journal(tmp_path, monkeypatch):
    invoke, sim, calls, target, path = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        assert invoke()["status"] == "COMPLETED"
        database = target / "agent/state.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute("DELETE FROM one_shot_meta WHERE key='finished'")
        status = reconcile_live(load_live_config(path), persist=False)
        assert status["flat_bot_position"] and "recover --config" in status["recovery_instruction"]
        with pytest.raises(Halt, match="unfinished"):
            new_live_run(load_live_config(path))
        count = sum(m.get("params", {}).get("name") == "place_equity_order" for m in calls)
        assert run_live(load_live_config(path), recover=True)["flat_bot_position"]
        assert sum(m.get("params", {}).get("name") == "place_equity_order" for m in calls) == count
        assert new_live_run(load_live_config(path))["status"] == "NEW_RUN_READY"
    finally:
        sim.close()
