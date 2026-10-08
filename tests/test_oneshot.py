"""One-shot CLI/controller correctness with local simulated broker state only."""

import sqlite3
from pathlib import Path

import pytest

from tradeagent.cli import main
from tradeagent.model import Config, Halt, Risk, dec
from tradeagent.oneshot import PaperRun, choose_entry, paper_snapshot, run_paper
from tradeagent.oneshot_cli import live_check
from tradeagent.simulator import SimClock, SimulatedMCP
from tradeagent.state import State


def orders(directory):
    with sqlite3.connect(directory / "broker/state.sqlite3") as db:
        return db.execute("SELECT COUNT(*) FROM simulated_orders").fetchone()[0]


def test_complete_autonomous_paper_round_trip_and_no_reentry(tmp_path):
    result = run_paper(tmp_path)
    assert result["status"] == "COMPLETED"
    assert result["mode"] == "PAPER" and result["synthetic_fixture"]
    assert result["excluded_from_strategy_performance"]
    assert result["bought"] == result["sold"] == "2"
    assert result["final_positions"] == {}
    assert result["initial_cash"] == "1000"
    assert result["final_cash"] == result["expected_final_cash"] == "999.96"
    assert result["realized_pnl"] == "-0.04"
    assert result["known_fees"] == "0"
    assert result["real_broker_calls"] == 0
    assert len(result["orders"]) == orders(tmp_path) == 2
    assert (
        result["orders"][0]["executions"][0]["timestamp"]
        != result["orders"][1]["executions"][0]["timestamp"]
    )
    repeated = run_paper(tmp_path)
    assert repeated["duplicate_suppressed"]
    assert repeated["status"] == "COMPLETED" and orders(tmp_path) == 2
    assert repeated["simulated_calls_this_process"] == []


def test_partial_entry_cancel_then_exit_only_confirmed_owned_quantity(tmp_path):
    result = run_paper(tmp_path, entry_scenario="partial_fill")
    assert result["status"] == "COMPLETED"
    assert result["bought"] == result["sold"] == "1"
    assert result["orders"][0]["state"] == "partially_filled_rest_cancelled"
    assert result["orders"][1]["quantity"] == "1"
    assert orders(tmp_path) == 2
    assert (
        sum(c["tool"] == "cancel_equity_order" for c in result["simulated_calls_this_process"]) == 1
    )


@pytest.mark.parametrize(
    "scenario", ["accepted", "rejected", "lost_ack", "timeout_after_acceptance", "delayed_ack"]
)
def test_nonfill_and_lost_ack_never_resubmit(tmp_path, scenario):
    result = run_paper(tmp_path, entry_scenario=scenario)
    assert result["status"] == "NO_TRADE"
    assert result["flat_bot_position"] and result["cash_reconciled"]
    assert orders(tmp_path) == 1
    run_paper(tmp_path, entry_scenario=scenario)
    assert orders(tmp_path) == 1


def test_unknown_absent_order_remains_incident_on_restart(tmp_path):
    first = run_paper(tmp_path, entry_scenario="timeout_before_ack")
    second = run_paper(tmp_path, entry_scenario="timeout_before_ack")
    assert first["status"] == second["status"] == "HALTED"
    assert first["outstanding_incident"] and second["outstanding_incident"]
    assert orders(tmp_path) == 0
    assert not any(
        c["tool"] == "place_equity_order" for c in second["simulated_calls_this_process"]
    )


def test_failed_partial_exit_keeps_residual_visible_and_does_not_retry(tmp_path):
    result = run_paper(tmp_path, exit_scenario="partial_fill")
    assert result["status"] == "HALTED" and result["outstanding_incident"]
    assert dec(result["residual_positions"]["IWM"]) == 1
    assert result["bought"] == "2" and result["sold"] == "1"
    assert result["cash_reconciled"] and not result["flat_bot_position"]
    assert result["realized_pnl"] is None
    assert len(result["observed_account"]["orders"]) == 2
    repeated = run_paper(tmp_path, exit_scenario="partial_fill")
    assert repeated["status"] == "HALTED" and orders(tmp_path) == 2
    assert not any(
        c["tool"] == "place_equity_order" for c in repeated["simulated_calls_this_process"]
    )


def test_cancel_rejection_stays_halted(tmp_path):
    result = run_paper(tmp_path, entry_scenario="cancel_rejected")
    assert result["status"] == "HALTED" and result["outstanding_incident"]
    assert orders(tmp_path) == 1


def test_restart_after_fill_before_ack_exits_without_rebuy(tmp_path, monkeypatch):
    original = SimulatedMCP._place

    def crash(self, name, arguments):
        result = original(self, name, arguments)
        if arguments.get("side") == "buy":
            raise SystemExit("test process interruption after simulated fill before acknowledgment")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(SimulatedMCP, "_place", crash)
        with pytest.raises(SystemExit):
            run_paper(tmp_path)
    assert orders(tmp_path) == 1
    result = run_paper(tmp_path)
    assert result["status"] == "COMPLETED"
    assert orders(tmp_path) == 2 and result["final_positions"] == {}
    assert (
        sum(c["tool"] == "place_equity_order" for c in result["simulated_calls_this_process"]) == 1
    )
    assert result["simulated_calls_this_process"][-1]["arguments"]["side"] == "sell"


def test_kill_blocks_entry_but_allows_recovered_position_exit(tmp_path, monkeypatch):
    kill = tmp_path / "KILL"
    kill.touch()
    blocked = run_paper(tmp_path / "blocked", kill_switch=kill)
    assert blocked["status"] == "NO_TRADE" and orders(tmp_path / "blocked") == 0
    kill.unlink()
    original = PaperRun.advance

    def interrupt(self, seconds):
        if seconds >= 3600:
            raise SystemExit("test interruption while holding paper position")
        return original(self, seconds)

    with monkeypatch.context() as patch:
        patch.setattr(PaperRun, "advance", interrupt)
        with pytest.raises(SystemExit):
            run_paper(tmp_path / "held", kill_switch=kill)
    kill.touch()
    recovered = run_paper(tmp_path / "held", kill_switch=kill)
    assert recovered["status"] == "COMPLETED"
    assert recovered["exit_reason"] == "kill_switch_risk_reduction"
    assert orders(tmp_path / "held") == 2


def test_existing_holdings_preserved_and_sizing_does_not_expand_capital(tmp_path):
    clock = SimClock()
    initial = paper_snapshot(clock)
    initial.positions = {"QQQ": dec(3)}
    initial.available = {"QQQ": dec(3)}
    initial.nav += 3 * initial.prices["QQQ"]
    result = run_paper(tmp_path, initial=initial)
    assert result["status"] == "COMPLETED"
    assert result["final_positions"] == {"QQQ": dec(3)}
    assert all(o["symbol"] == "IWM" for o in result["orders"])
    no_trade = run_paper(tmp_path / "too-small", max_notional="1")
    assert no_trade["status"] == "NO_TRADE" and orders(tmp_path / "too-small") == 0


def test_stale_market_data_rejected_without_orders():
    clock = SimClock()
    initial = paper_snapshot(clock)
    initial.quote_times["IWM"] = clock() - 121
    with pytest.raises(Halt, match="stale market"):
        choose_entry(initial, Config(allowed_symbols=["QQQ", "IWM"]), Risk(), clock(), "25")


def test_foreign_directory_configuration_drift_and_duplicate_process_rejected(tmp_path):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "experience.sqlite3").touch()
    with pytest.raises(Halt, match="fresh directory"):
        run_paper(foreign)
    directory = tmp_path / "paper"
    run_paper(directory)
    with pytest.raises(Halt, match="configuration changed"):
        run_paper(directory, max_notional="20")
    state = State(directory / "agent")
    try:
        with state.lock(1200), pytest.raises(Halt, match="process lock"):
            run_paper(directory)
    finally:
        state.close()


def test_live_and_missing_helper_do_not_open_broker_or_database(tmp_path, capsys, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("live attempt must be blocked before broker construction")

    monkeypatch.setattr("tradeagent.oneshot_cli.ReadOnlyPreflightMCP", forbidden)
    assert main(["run-once", "--live", "--state-dir", str(tmp_path / "live")]) == 2
    assert "LIVE BLOCKED" in capsys.readouterr().out
    assert not (tmp_path / "live").exists()
    result = live_check()
    assert not result["authenticated"] and result["broker_calls"] == []
    assert not result["armed"] and not result["live_ready"]


def test_no_codex_or_other_llm_process_is_started(tmp_path, monkeypatch):
    import subprocess

    original = subprocess.run

    def guarded(command, *args, **kwargs):
        # Check the actual child executable, not a test directory name that
        # happens to contain the word "codex".
        assert Path(command[0]).name == "findmnt"
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)
    assert run_paper(tmp_path)["status"] == "COMPLETED"


def test_legacy_preflight_checks_actual_package_root_after_move(monkeypatch):
    from dataclasses import asdict

    import tradeagent.legacy.standalone_cli as module
    from tradeagent.model import digest

    config, risk = Config(), Risk()
    root = Path(module.__file__).resolve().parents[3]
    monkeypatch.setattr(module, "require_autonomous_release", lambda phase: None)
    monkeypatch.setattr(module.PolicySignature, "verify", lambda *args: None)
    monkeypatch.setattr(module, "deployment_hash", lambda root: "frozen-test")
    with pytest.raises(Halt, match="expired/not active"):
        module.local_policy_preflight(
            {
                "policy": {
                    "phase": "CANARY",
                    "issued_at": 0,
                    "expires_at": 1,
                    "context": {
                        "deployment_hash": "frozen-test",
                        "config_hash": digest(asdict(config)),
                        "risk_hash": digest(asdict(risk)),
                    },
                }
            },
            None,
            root,
            config,
            risk,
            {},
            now=lambda: 10,
        )


def test_read_only_transport_rejects_every_write_before_authentication():
    from tradeagent.oneshot_cli import ReadOnlyPreflightMCP

    def token():
        pytest.fail("a rejected write must not even refresh authentication")

    bridge = ReadOnlyPreflightMCP(token)
    for name in ("review_equity_order", "place_equity_order", "cancel_equity_order"):
        with pytest.raises(Halt, match="read-only preflight"):
            bridge.rpc("tools/call", {"name": name, "arguments": {}})
    assert bridge.calls == []
    assert "place_equity_order" not in bridge.contracts.tools


def test_session_close_bounds_long_holding_period(tmp_path):
    result = run_paper(tmp_path, hold_seconds=21600)
    assert result["status"] == "COMPLETED"
    assert result["exit_reason"] == "regular_session_exit_deadline"
    assert result["orders"][1]["executions"][0]["timestamp"] == "2026-10-05T19:50:00+00:00"


@pytest.mark.parametrize("quote_failures", [0, 1, 2])
def test_current_read_contract_success_does_not_imply_live_readiness(monkeypatch, quote_failures):
    import time

    from tradeagent.schema import Contracts

    now = time.time()
    snapshot = paper_snapshot(SimClock(now))

    class ReadFake:
        def __init__(self, *args):
            self.calls = []
            self.contracts = Contracts()
            self.server_info = {"version": "1.7.0"}
            self.tools = self.contracts.tools

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("tradeagent.oneshot_cli.ExternalOAuthToken", lambda *args: object())
    monkeypatch.setattr("tradeagent.oneshot_cli.ReadOnlyPreflightMCP", ReadFake)
    attempts = []

    def current_snapshot(*args):
        attempts.append(1)
        if len(attempts) <= quote_failures:
            raise Halt("quote/depth disagreement; refresh required")
        return snapshot

    monkeypatch.setattr("tradeagent.oneshot_cli.Broker.snapshot", current_snapshot)
    result = live_check("unused-test-helper")
    assert result["authenticated"]
    assert result["account_verified"] is (quote_failures < 2)
    assert len(attempts) == min(quote_failures + 1, 2)
    assert len(result["snapshot_attempts"]) == len(attempts)
    assert result["real_review_place_cancel_calls"] == 0
    assert result["status"] == "LIVE BLOCKED" and not result["live_ready"]
