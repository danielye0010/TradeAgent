"""Regression coverage for owner review latency and durable no-send recovery; mock HTTP only."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from test_production_oneshot import owner_mock_runtime

from tradeagent.execution_policy import StandingLifecycle, load_live_config
from tradeagent.model import Halt
from tradeagent.oneshot import new_live_run, reconcile_live
from tradeagent.standalone_mcp import WireAuthorization


def placements(calls):
    return [
        m
        for m in calls
        if m["method"] == "tools/call" and m["params"]["name"] == "place_equity_order"
    ]


def durable(target):
    with sqlite3.connect(target / "agent/state.sqlite3") as db:
        return dict(db.execute("SELECT kind,COUNT(*) FROM events GROUP BY kind")), db.execute(
            "SELECT status,broker_id FROM intents"
        ).fetchall()


def test_slow_review_and_mcp_snapshots_keep_only_fresh_owner_evidence(tmp_path, monkeypatch):
    invoke, sim, calls, target, config = owner_mock_runtime(tmp_path, monkeypatch)
    snapshot, review = sim.snapshot, sim._review

    def slow_snapshot():
        sim.clock.advance(8)
        return snapshot()

    def slow_review(name, args):
        sim.clock.advance(12)
        return review(name, args)

    monkeypatch.setattr(sim, "snapshot", slow_snapshot)
    monkeypatch.setattr(sim, "_review", slow_review)
    try:
        result = invoke()
        assert result["status"] == "COMPLETED"
        assert result["submission_status"] == "BROKER_CONFIRMED"
        assert result["reconciliation_status"] == "RECONCILED"
        assert len(placements(calls)) == 2
        with sqlite3.connect(target / "agent/state.sqlite3") as db:
            plans = [json.loads(r[0]) for r in db.execute("SELECT packet FROM plans")]
            sends = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM events WHERE kind='placement_send_started'"
                )
            ]
        assert len(sends) == 2
        assert any(p["binding"]["expires"] - p["review"]["asof"] > 30 for p in plans)
        assert all(p["binding"]["expires"] <= p["review"]["asof"] + 120 for p in plans)
    finally:
        sim.close()


@pytest.mark.parametrize("phase", ["before_execute", "transport_snapshot", "wire_permission"])
def test_review_expiration_never_places_and_recovers_as_not_submitted(tmp_path, monkeypatch, phase):
    invoke, sim, calls, target, config = owner_mock_runtime(tmp_path, monkeypatch)
    original = StandingLifecycle.execute

    def expired(engine, key, intent):
        if phase == "before_execute":
            sim.clock.advance(121)
        elif phase == "transport_snapshot":
            snapshot = sim.snapshot
            reads = []

            def delay_placement_read():
                reads.append(1)
                if len(reads) == 2:
                    sim.clock.advance(121)
                return snapshot()

            monkeypatch.setattr(sim, "snapshot", delay_placement_read)
        return original(engine, key, intent)

    monkeypatch.setattr(StandingLifecycle, "execute", expired)
    if phase == "wire_permission":
        wire = WireAuthorization.__call__

        def delay_at_wire(auth):
            if auth.name == "place_equity_order":
                sim.clock.advance(121)
            return wire(auth)

        monkeypatch.setattr(WireAuthorization, "__call__", delay_at_wire)
    try:
        first = invoke()
        assert first["status"] == "HALTED"
        assert first["submission_status"] == "NOT_SUBMITTED"
        assert first["reconciliation_status"] == "RECONCILED"
        assert first["broker_order_count"] == 0 and first["final_positions"] == {}
        assert first["submission_times"]["buy"] is None
        assert not first["outstanding_incident"] and "reconciliation_blocker" not in first
        assert placements(calls) == []
        events, rows = durable(target)
        assert rows == [("abandoned", None)]
        assert "placement_send_started" not in events
        assert ("submission_not_sent" in events) is (phase != "before_execute")
        again = invoke()
        assert again["status"] == "HALTED" and again["submission_status"] == "NOT_SUBMITTED"
        assert placements(calls) == []
    finally:
        sim.close()


def test_stale_venue_review_cannot_be_refreshed_by_receipt_time(tmp_path, monkeypatch):
    invoke, sim, calls, target, config = owner_mock_runtime(tmp_path, monkeypatch)
    review = sim._review

    def stale(name, args):
        result = review(name, args)
        sim.clock.advance(121)
        return result

    monkeypatch.setattr(sim, "_review", stale)
    try:
        result = invoke()
        assert result["status"] == "HALTED" and result["submission_status"] == "NOT_SUBMITTED"
        assert "stale" in result["reason"]
        assert placements(calls) == []
    finally:
        sim.close()


def test_network_attempt_without_order_stays_unknown_and_cannot_archive(tmp_path, monkeypatch):
    invoke, sim, calls, target, config = owner_mock_runtime(
        tmp_path, monkeypatch, "timeout_before_ack"
    )
    try:
        result = invoke()
        assert result["status"] == "HALTED" and result["outstanding_incident"]
        assert result["submission_status"] == "SUBMISSION_UNKNOWN"
        assert len(placements(calls)) == 1
        events, rows = durable(target)
        assert rows == [("unknown", None)] and events["placement_send_started"] == 1
        for operation in (invoke, lambda: reconcile_live(load_live_config(config))):
            recovered = operation()
            assert recovered["submission_status"] == "SUBMISSION_UNKNOWN"
            assert "reconciliation_blocker" in recovered
        with pytest.raises(Halt, match="identity"):
            new_live_run(load_live_config(config))
        assert len(placements(calls)) == 1
    finally:
        sim.close()


def test_corrected_wheel_read_only_reconciliation_preserves_failed_evidence(tmp_path, monkeypatch):
    invoke, sim, calls, target, config = owner_mock_runtime(tmp_path, monkeypatch)
    original = StandingLifecycle.execute

    def expire(engine, key, intent):
        sim.clock.advance(121)
        return original(engine, key, intent)

    monkeypatch.setattr(StandingLifecycle, "execute", expire)
    try:
        assert invoke()["submission_status"] == "NOT_SUBMITTED"
        paths = [
            config,
            target / "live-run.json",
            target / "report.json",
            target / "agent/state.sqlite3",
            target / "agent/events.jsonl",
        ]
        hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        monkeypatch.setattr(
            "tradeagent.execution_policy.package_hash", lambda: "CORRECTED_TEST_WHEEL"
        )
        from tradeagent.execution_policy import check_run_state

        assert check_run_state(load_live_config(config)) is not None
        result = reconcile_live(load_live_config(config))
        assert result["status"] == "HALTED" and result["submission_status"] == "NOT_SUBMITTED"
        assert result["reconciliation_status"] == "RECONCILED"
        assert result["broker_order_count"] == 0
        assert not result["execution_state_modified"] and not result["outstanding_incident"]
        assert result["real_review_place_cancel_calls"] == 0
        assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths} == hashes
        archive = new_live_run(load_live_config(config))
        assert archive["status"] == "NEW_RUN_READY" and not target.exists()
        archived = Path(archive["archived_run"])
        assert (
            hashlib.sha256((archived / "report.json").read_bytes()).hexdigest()
            == hashes[target / "report.json"]
        )
        assert (
            hashlib.sha256((archived / "agent/state.sqlite3").read_bytes()).hexdigest()
            == hashes[target / "agent/state.sqlite3"]
        )
        assert placements(calls) == []
    finally:
        sim.close()


def test_not_submitted_does_not_hide_contradictory_broker_order(tmp_path, monkeypatch):
    invoke, sim, calls, target, config = owner_mock_runtime(tmp_path, monkeypatch)
    original = StandingLifecycle.execute

    def expire(engine, key, intent):
        sim.clock.advance(121)
        return original(engine, key, intent)

    monkeypatch.setattr(StandingLifecycle, "execute", expire)
    try:
        assert invoke()["submission_status"] == "NOT_SUBMITTED"
        with sqlite3.connect(target / "agent/state.sqlite3") as db:
            payload, ref = db.execute("SELECT payload,ref_id FROM intents").fetchone()
        # Synthetic broker contradiction, never a real network request.
        sim._place(
            "place_equity_order",
            {"account_number": sim.account["account_number"], **json.loads(payload), "ref_id": ref},
        )
        result = reconcile_live(load_live_config(config))
        assert result["status"] == "HALTED" and result["outstanding_incident"]
        assert "contradicts" in result["reconciliation_blocker"]
        assert placements(calls) == []
    finally:
        sim.close()


def test_human_approval_expiring_during_final_reads_is_abandoned(tmp_path, monkeypatch):
    from test_execution_lifecycle import approval, equity, harness

    with harness(tmp_path) as (engine, sim, state, clock):
        intent = equity()
        plan = engine.prepare(intent, "expiry-during-refresh")
        snapshot = engine.broker.snapshot

        def slow_snapshot(*args):
            clock.advance(31)
            return snapshot(*args)

        monkeypatch.setattr(engine.broker, "snapshot", slow_snapshot)
        with pytest.raises(Halt, match="expired"):
            engine.execute(plan["key"], intent, approval(plan))
        assert state.db.execute("SELECT status FROM intents").fetchone()[0] == "abandoned"
        assert not state.db.execute("SELECT 1 FROM approvals").fetchone()
        assert not any(c["tool"].startswith("place") for c in sim.calls)


def test_legacy_ambiguous_submission_without_new_wire_marker_stays_unknown(tmp_path, monkeypatch):
    invoke, sim, calls, target, config = owner_mock_runtime(
        tmp_path, monkeypatch, "timeout_before_ack"
    )
    try:
        assert invoke()["submission_status"] == "SUBMISSION_UNKNOWN"
        with sqlite3.connect(target / "agent/state.sqlite3") as db:
            db.execute("DELETE FROM events WHERE kind='placement_send_started'")
        result = reconcile_live(load_live_config(config))
        assert result["submission_status"] == "SUBMISSION_UNKNOWN"
        assert result["reconciliation_status"] == "BLOCKED"
        assert result["outstanding_incident"] and len(placements(calls)) == 1
    finally:
        sim.close()


@pytest.mark.parametrize("status", ["prepared", "reviewed", "abandoned", "risk_rejected"])
def test_pre_submission_states_reconcile_without_replaying_entry(tmp_path, monkeypatch, status):
    invoke, sim, calls, target, config = owner_mock_runtime(tmp_path, monkeypatch)
    original = StandingLifecycle.execute

    def expire(engine, key, intent):
        sim.clock.advance(121)
        return original(engine, key, intent)

    monkeypatch.setattr(StandingLifecycle, "execute", expire)
    try:
        assert invoke()["submission_status"] == "NOT_SUBMITTED"
        with sqlite3.connect(target / "agent/state.sqlite3") as db:
            db.execute("UPDATE intents SET status=?", (status,))
        result = reconcile_live(load_live_config(config))
        assert result["submission_status"] == "NOT_SUBMITTED"
        assert result["reconciliation_status"] == "RECONCILED"
        assert result["broker_order_count"] == 0 and placements(calls) == []
    finally:
        sim.close()


def test_packaged_cli_routes_read_only_reconciliation(tmp_path, monkeypatch, capsys):
    from tradeagent.cli import main

    invoke, sim, calls, target, config = owner_mock_runtime(tmp_path, monkeypatch)
    original = StandingLifecycle.execute

    def expire(engine, key, intent):
        sim.clock.advance(121)
        return original(engine, key, intent)

    monkeypatch.setattr(StandingLifecycle, "execute", expire)
    try:
        assert invoke()["submission_status"] == "NOT_SUBMITTED"
        assert main(["reconcile-once", "--config", str(config)]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["reconciliation_status"] == "RECONCILED"
        assert result["real_review_place_cancel_calls"] == 0 and placements(calls) == []
    finally:
        sim.close()


def test_legacy_human_plan_cannot_accept_review_older_than_thirty_seconds(tmp_path):
    from test_execution_lifecycle import approval, equity, harness

    from tradeagent.state import dumps

    with harness(tmp_path) as (engine, sim, state, clock):
        intent = equity()
        plan = engine.prepare(intent, "legacy-plan")
        # Old releases bound human expiry after preparation, so it could be later
        # than review creation + 30. Reading an old packet must not extend review age.
        row = state.db.execute("SELECT packet FROM plans WHERE key=?", (plan["key"],)).fetchone()
        packet = json.loads(row[0])
        packet["binding"]["expires"] += 20
        plan["binding"] = packet["binding"]
        with state.db:
            state.db.execute("UPDATE plans SET packet=? WHERE key=?", (dumps(packet), plan["key"]))
        clock.advance(31)
        with pytest.raises(Halt, match="expired"):
            engine.execute(plan["key"], intent, approval(plan))
        assert not any(c["tool"].startswith("place") for c in sim.calls)
