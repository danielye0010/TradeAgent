"""Reviewed catalog upgrade and unfiltered provenance; no real broker calls."""

import copy
import json
from importlib.resources import files

import pytest
from test_broker_contract import NOW, RawReadFake

from tradeagent.broker import Broker
from tradeagent.execution_policy import (
    READ_ONLY_LEGACY_SCHEMA_HASHES,
    VERSION,
    check_compatibility,
)
from tradeagent.model import Config, Halt, Risk, digest
from tradeagent.options import normalize_order
from tradeagent.schema import Contracts


def order(initiated=True, identity="fixture-173-order", side="buy", state="filled"):
    return {
        "id": identity,
        "ref_id": "fixture-173-ref-" + identity,
        "instrument_id": "fixture-173-instrument",
        "symbol": "SPY",
        "side": side,
        "type": "market",
        "state": state,
        "quantity": "0.5",
        "cumulative_quantity": "0.5",
        "price": None,
        "stop_price": None,
        "average_price": "100",
        "fees": "0.01",
        "dollar_based_amount": {"amount": "50", "currency_code": "USD"} if side == "buy" else None,
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
        "trigger": "immediate",
        "placed_agent": "customer",  # Customer approval does not establish this agent's ownership.
        "agent_initiated": initiated,
        "created_at": NOW.isoformat(),
        "last_transaction_at": NOW.isoformat(),
        "executions": [
            {
                "id": "fill-" + identity,
                "quantity": "0.5",
                "price": "100",
                "fees": "0.01",
                "timestamp": NOW.isoformat(),
            }
        ],
    }


def test_exact_173_delta_retains_every_other_structural_contract():
    old = json.loads(files("tradeagent").joinpath("contracts/official-1.7.0.json").read_text())
    current = Contracts()
    assert current.manifest["server_version"] == "1.7.3"
    new = copy.deepcopy(current.tools)
    for name in ("get_equity_orders", "get_option_orders"):
        assert new[name]["inputSchema"]["properties"].pop("agent_initiated") == {
            "type": ["null", "boolean"]
        }
    for name in (
        "get_equity_orders",
        "get_option_orders",
        "place_equity_order",
        "place_option_order",
    ):
        props = new[name]["outputSchema"]["properties"]["data"]["properties"]
        schema = props["orders"]["items"] if name.startswith("get") else props["order"]
        assert schema["properties"].pop("agent_initiated") == {"type": "boolean"}
        schema["required"].remove("agent_initiated")
    assert new == old["tools"]
    current.check_current(current.tools, "1.7.3")


def test_required_new_output_is_not_silently_ignored():
    raw = order()
    Contracts().validate(
        "get_equity_orders", {"data": {"orders": [raw]}, "guide": "fixture"}, "outputSchema"
    )
    old = copy.deepcopy(raw)
    del old["agent_initiated"]
    with pytest.raises(Halt, match="outputSchema"):
        Contracts().validate(
            "get_equity_orders", {"data": {"orders": [old]}, "guide": "fixture"}, "outputSchema"
        )
    before, after = normalize_order(old), normalize_order(raw)
    assert after.pop("agent_initiated") is True
    assert before == after


@pytest.mark.parametrize("bad", [None, "true", 1])
def test_agent_provenance_requires_boolean(bad):
    raw = order()
    raw["agent_initiated"] = bad
    with pytest.raises(Halt, match="provenance"):
        normalize_order(raw)


def test_paginated_all_account_orders_and_fees_are_kept_without_agent_filter():
    raw = RawReadFake()
    original = raw.read
    arguments = []

    def read(name, args=None):
        if name != "get_equity_orders":
            return original(name, args)
        args = args or {}
        arguments.append(args)
        assert set(args) <= {"account_number", "cursor"}
        result = order(True) if not args.get("cursor") else order(False, "manual", "sell")
        return {
            "data": {"orders": [result], "next": "fixture-next" if not args.get("cursor") else ""}
        }

    raw.read = read
    snapshot = Broker(raw, Config(allowed_symbols=["SPY"]), Risk()).snapshot(NOW.timestamp())
    assert len(arguments) == 2 and len(snapshot.orders) == 2
    assert {o["agent_initiated"] for o in snapshot.orders} == {True, False}
    assert len(snapshot.fills) == 2 and snapshot.daily_turnover == 100
    assert {o["fees"] for o in snapshot.orders} == {"0.01"}


@pytest.mark.parametrize(
    "state", ["queued", "confirmed", "partially_filled", "cancelled", "rejected"]
)
def test_fractional_partial_and_terminal_states_normalize_without_guessed_quantity(state):
    raw = order(state=state)
    result = normalize_order(raw)
    assert result["cumulative_quantity"] == "0.5" and result["ref_id"] == raw["ref_id"]


@pytest.mark.parametrize("change", ["missing_fills", "quantity", "fees", "state"])
def test_ambiguous_order_economics_fail_closed(change):
    raw = RawReadFake()
    record = order()
    if change == "missing_fills":
        record["executions"] = []
    if change == "quantity":
        record["cumulative_quantity"] = "0.6"
    if change == "fees":
        record["fees"] = "0.02"
    if change == "state":
        record["state"] = "unreviewed_new_state"
    raw.payloads["get_equity_orders"]["orders"] = [record]
    with pytest.raises(Halt):
        Broker(raw, Config(allowed_symbols=["SPY"]), Risk()).snapshot(NOW.timestamp())


def test_legacy_journal_hash_only_allows_read_only_inspection():
    old = json.loads(files("tradeagent").joinpath("contracts/official-1.7.0.json").read_text())
    legacy = digest(old["tools"])
    assert READ_ONLY_LEGACY_SCHEMA_HASHES == {legacy}
    policy = {"version": VERSION, "schema_hash": legacy}
    check_compatibility(policy, reconciliation_only=True)
    with pytest.raises(Halt, match="broker contracts incompatible"):
        check_compatibility(policy)
    with pytest.raises(Halt, match="broker contracts incompatible"):
        check_compatibility({**policy, "schema_hash": "unknown"}, reconciliation_only=True)


def test_legacy_lifecycle_live_check_reconciles_without_mutating_or_rearming(tmp_path, monkeypatch):
    from test_production_oneshot import owner_mock_runtime

    from tradeagent.execution_policy import load_live_config
    from tradeagent.oneshot_cli import live_check

    invoke, sim, calls, target, trust = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        invoke()
        marker = target / "live-run.json"
        artifact = json.loads(marker.read_text())
        artifact["policy"]["schema_hash"] = next(iter(READ_ONLY_LEGACY_SCHEMA_HASHES))
        marker.write_text(json.dumps(artifact))
        settings = load_live_config(trust)
        settings.receipt.write_text(json.dumps({"marker_hash": digest(artifact)}))
        import hashlib

        before = {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in target.rglob("*")
            if p.is_file()
        }
        calls.clear()
        monkeypatch.setattr(
            "tradeagent.oneshot_cli.ExternalOAuthToken", lambda *args: lambda: "TEST_ONLY_TOKEN"
        )
        monkeypatch.setattr("tradeagent.oneshot_cli.Broker", lambda bridge, config, risk: sim)
        result = live_check(settings=settings)
        assert result["status"] == "LIVE BLOCKED"
        assert result["authenticated"] and result["snapshot_normalized"]
        assert result["lifecycle_reconciliation"]["cash_reconciled"]
        assert result["lifecycle_reconciliation"]["flat_bot_position"]
        assert any("legacy broker contracts" in b for b in result["blockers"])
        after = {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in target.rglob("*")
            if p.is_file()
        }
        assert before == after
        assert all(
            m.get("params", {}).get("name")
            not in {"review_equity_order", "place_equity_order", "cancel_equity_order"}
            for m in calls
        )
        with pytest.raises(Halt, match="broker contracts incompatible"):
            invoke()
        # Only an explicitly invoked, reconciled archive can start a fresh lifecycle.
        from tradeagent.oneshot import new_live_run

        calls.clear()
        archived = new_live_run(settings)
        assert archived["status"] == "NEW_RUN_READY"
        from pathlib import Path

        archived_db = Path(archived["archived_run"]) / "agent/state.sqlite3"
        assert (
            hashlib.sha256(archived_db.read_bytes()).hexdigest()
            == before[str(target / "agent/state.sqlite3")]
        )
        assert all(
            m.get("params", {}).get("name")
            not in {"review_equity_order", "place_equity_order", "cancel_equity_order"}
            for m in calls
        )
    finally:
        sim.close()
