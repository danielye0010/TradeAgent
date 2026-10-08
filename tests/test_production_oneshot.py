"""Production controller, local owner configuration and real wire encoding with HTTP replaced."""

import io
import json
import time

import pytest

from tradeagent.execution_policy import load_live_config
from tradeagent.model import Halt
from tradeagent.oneshot import paper_snapshot, run_live
from tradeagent.schema import Contracts
from tradeagent.simulator import SimClock, SimulatedMCP
from tradeagent.standalone_mcp import ReadOnlyMCP, StandaloneMCP, TransientReadFailure


def owner_mock_runtime(
    tmp_path,
    monkeypatch,
    scenario="full_fill",
    approval=False,
    review_approval=False,
    exit_scenario="full_fill",
):
    """Test-only account/configuration. Every HTTPS connection is replaced before invocation."""
    clock = SimClock()
    monkeypatch.setattr(time, "time", clock)
    monkeypatch.setattr("tradeagent.oneshot.wait_for_poll", clock.advance)
    target, trust = tmp_path / "live-state", tmp_path / "tradeagent.toml"
    trust.write_text(f'''[live]
enabled = true
symbols = ["QQQ", "IWM"]
state_dir = "{target}"
max_notional = "25"
[broker]
oauth_helper = "{tmp_path / "TEST_ONLY_HELPER"}"
[risk]
[exit]
hold_seconds = 0
polls = 2
''')
    trust.chmod(0o600)
    sim = SimulatedMCP(tmp_path / "mock-broker", clock, scenario, paper_snapshot(clock))
    calls = []
    pins = Contracts()

    class Response:
        status = 200

        def __init__(self, value, status=200):
            self.status = status
            self.stream = io.BytesIO(json.dumps(value).encode())

        def getheader(self, name, default=None):
            return "application/json" if name == "Content-Type" else default

        def read(self, n):
            return self.stream.read(n)

    class MockHTTPS:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, body, headers):
            self.message = json.loads(body)
            calls.append(self.message)

        def getresponse(self):
            message = self.message
            if message["method"] == "notifications/initialized":
                return Response({}, 202)
            if message["method"] == "initialize":
                result = {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"version": pins.manifest["server_version"]},
                }
            elif message["method"] == "tools/list":
                result = {"tools": [{"name": n, **v} for n, v in pins.tools.items()]}
            else:
                name, args = message["params"]["name"], message["params"]["arguments"]
                if name == "get_trade_approval_setting":
                    output = {
                        "data": {
                            "setting": {
                                "account_number": sim.account["account_number"],
                                "human_must_approve_trades": approval,
                            }
                        },
                        "guide": "TEST ONLY",
                    }
                else:
                    if name == "place_equity_order" and args["side"] == "sell":
                        sim.scenario = exit_scenario
                    output = sim.invoke(name, args)
                    if name == "review_equity_order" and review_approval:
                        output["data"]["customer_approval_required"] = True
                result = {"structuredContent": output, "content": [], "isError": False}
            return Response({"jsonrpc": "2.0", "id": message.get("id"), "result": result})

        def close(self):
            pass

    monkeypatch.setattr("tradeagent.standalone_mcp.http.client.HTTPSConnection", MockHTTPS)
    monkeypatch.setattr("tradeagent.oneshot.ExternalOAuthToken", lambda *args: None, raising=False)
    monkeypatch.setattr(
        "tradeagent.standalone_mcp.ExternalOAuthToken", lambda *args: lambda: "TEST_ONLY_TOKEN"
    )

    def mock_reads(bridge, config, risk):
        sim.bridge = bridge
        return sim

    monkeypatch.setattr("tradeagent.broker.Broker", mock_reads)

    def invoke():
        return run_live(load_live_config(trust))

    return invoke, sim, calls, target, trust


@pytest.mark.parametrize(
    "scenario", ["full_fill", "partial_fill", "lost_ack", "timeout_before_ack"]
)
def test_same_live_controller_and_direct_wire_non_live(tmp_path, monkeypatch, scenario):
    invoke, sim, calls, target, trust = owner_mock_runtime(tmp_path, monkeypatch, scenario)
    try:
        result = invoke()
        assert result["mode"] == "LIVE"  # production branch; all HTTP replaced
        expected = (
            "COMPLETED"
            if scenario in {"full_fill", "partial_fill"}
            else "NO_TRADE"
            if scenario == "lost_ack"
            else "HALTED"
        )
        assert result["status"] == expected
        again = invoke()
        assert again["status"] == expected
        writes = [
            m
            for m in calls
            if m["method"] == "tools/call" and m["params"]["name"] == "place_equity_order"
        ]
        assert len(writes) == (2 if expected == "COMPLETED" else 1)
        assert len({m["params"]["arguments"]["ref_id"] for m in writes}) == len(writes)
        if expected == "COMPLETED":
            assert result["flat_bot_position"] and result["cash_reconciled"]
            assert result["final_positions"] == {}
        if scenario == "partial_fill":
            assert result["bought"] == result["sold"] == "1"
            cancels = [
                m
                for m in calls
                if m["method"] == "tools/call" and m["params"]["name"] == "cancel_equity_order"
            ]
            assert len(cancels) == 1
    finally:
        sim.close()


def test_live_controller_restart_after_accepted_before_ack(tmp_path, monkeypatch):
    invoke, sim, calls, target, trust = owner_mock_runtime(
        tmp_path, monkeypatch, "crash_after_acceptance"
    )
    try:
        with pytest.raises(SystemExit):
            invoke()
        result = invoke()
        assert result["status"] == "NO_TRADE"
        assert (
            sum(
                m["method"] == "tools/call" and m["params"]["name"] == "place_equity_order"
                for m in calls
            )
            == 1
        )
    finally:
        sim.close()


def test_local_configuration_and_broker_approval_not_bypassed(tmp_path, monkeypatch):
    invoke, sim, calls, target, trust = owner_mock_runtime(tmp_path, monkeypatch, approval=True)
    try:
        with pytest.raises(Halt, match="broker trade approvals"):
            invoke()
        assert not any(
            m["method"] == "tools/call" and m["params"]["name"].startswith(("review_", "place_"))
            for m in calls
        )
        trust.write_text(trust.read_text().replace('max_notional = "25"', 'max_notional = "1000"'))
        before = len(calls)
        with pytest.raises(Halt, match="configuration changed"):
            invoke()
        assert len(calls) == before
    finally:
        sim.close()


def test_idempotent_reads_retry_only_classified_transients(monkeypatch):
    bridge = StandaloneMCP(lambda: "TEST_ONLY_TOKEN")
    bridge.tools = bridge.contracts.tools
    calls = []

    def transient(name, args):
        calls.append(name)
        if len(calls) < 3:
            raise TransientReadFailure("TEST transient")
        return {"ok": True}

    monkeypatch.setattr(bridge, "_call", transient)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    assert bridge.read("get_accounts") == {"ok": True}
    assert len(calls) == 3
    calls.clear()
    monkeypatch.setattr(
        bridge, "_call", lambda *args: (_ for _ in ()).throw(Halt("malformed or stale"))
    )
    with pytest.raises(Halt, match="malformed"):
        bridge.read("get_accounts")
    for name in ("review_equity_order", "place_equity_order", "cancel_equity_order"):
        with pytest.raises(Halt, match="rejects writes"):
            bridge.read(name)


def test_read_only_contract_validation_ignores_write_drift_only():
    bridge = ReadOnlyMCP(lambda: "TEST_ONLY_TOKEN")
    actual = Contracts().tools
    actual["place_equity_order"]["inputSchema"] = {"type": "INVALID_TEST_WRITE"}
    bridge.contracts.check_current(actual, "1.7.0")
    actual["get_equity_quotes"]["annotations"] = {"readOnlyHint": False}
    with pytest.raises(Halt, match="schema drift"):
        bridge.contracts.check_current(actual, "1.7.0")


def test_live_controller_recovers_confirmed_fill_before_ack_without_rebuy(tmp_path, monkeypatch):
    invoke, sim, calls, target, trust = owner_mock_runtime(tmp_path, monkeypatch)
    original = sim._place

    def crash_after_fill(name, arguments):
        result = original(name, arguments)
        if arguments["side"] == "buy":
            raise SystemExit("TEST interruption after controlled broker fill")
        return result

    try:
        with monkeypatch.context() as patch:
            patch.setattr(sim, "_place", crash_after_fill)
            with pytest.raises(SystemExit):
                invoke()
        result = invoke()
        assert result["status"] == "COMPLETED" and result["final_positions"] == {}
        writes = [
            m["params"]["arguments"]
            for m in calls
            if m["method"] == "tools/call" and m["params"]["name"] == "place_equity_order"
        ]
        assert [m["side"] for m in writes] == ["buy", "sell"]
    finally:
        sim.close()


def test_cancel_failure_is_not_replayed_and_state_cannot_be_reset(tmp_path, monkeypatch):
    invoke, sim, calls, target, trust = owner_mock_runtime(tmp_path, monkeypatch, "cancel_rejected")
    try:
        assert invoke()["status"] == "HALTED"
        assert invoke()["status"] == "HALTED"
        assert (
            sum(
                m["method"] == "tools/call" and m["params"]["name"] == "cancel_equity_order"
                for m in calls
            )
            == 1
        )
        (target / "live-run.json").unlink()  # simulate lost authoritative state marker
        before = len(calls)
        with pytest.raises(Halt, match="cannot be reset"):
            invoke()
        assert len(calls) == before
    finally:
        sim.close()


def test_disabled_config_stops_before_broker_connection(tmp_path, monkeypatch):
    invoke, sim, calls, target, trust = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        trust.write_text(trust.read_text().replace("enabled = true", "enabled = false"))
        with pytest.raises(Halt, match="live.enabled"):
            invoke()
        assert calls == []
    finally:
        sim.close()


def test_missing_journal_stops_before_broker_connection(tmp_path, monkeypatch):
    invoke, sim, calls, target, trust = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        assert invoke()["status"] == "COMPLETED"
        (target / "agent/state.sqlite3").unlink()
        before = len(calls)
        with pytest.raises(Halt, match="cannot be reset"):
            invoke()
        assert len(calls) == before
    finally:
        sim.close()


def test_kill_arriving_after_review_blocks_production_submission(tmp_path, monkeypatch):
    from tradeagent.execution_policy import StandingLifecycle

    invoke, sim, calls, target, trust = owner_mock_runtime(tmp_path, monkeypatch)
    original = StandingLifecycle.execute

    def stop_before_execute(engine, key, intent):
        (target / "KILL").touch()
        return original(engine, key, intent)

    monkeypatch.setattr(StandingLifecycle, "execute", stop_before_execute)
    try:
        result = invoke()
        assert result["status"] == "HALTED"
        assert not any(
            m["method"] == "tools/call" and m["params"]["name"] == "place_equity_order"
            for m in calls
        )
    finally:
        sim.close()


@pytest.mark.parametrize(
    "replacement",
    [
        ('max_notional = "25"', 'max_notional = "1001"'),
        ("hold_seconds = 0", "hold_seconds = true"),
        ("polls = 2", "polls = 0"),
        ("[risk]", "[risk]\nmax_positions = 6"),
        ("[risk]", '[risk]\nmin_cash_fraction = "0.10"'),
        ("[broker]", '[broker]\naccess_token = "DO_NOT_ACCEPT_CREDENTIALS"'),
        ('symbols = ["QQQ", "IWM"]', 'symbols = ["TQQQ"]'),
    ],
)
def test_invalid_local_policy_stops_before_network(tmp_path, monkeypatch, replacement):
    invoke, sim, calls, target, config_path = owner_mock_runtime(tmp_path, monkeypatch)
    try:
        config_path.write_text(config_path.read_text().replace(*replacement))
        with pytest.raises(Halt):
            invoke()
        assert calls == [] and not target.exists()
    finally:
        sim.close()


def test_config_changes_after_review_stop_before_submit(tmp_path, monkeypatch):
    from tradeagent.execution_policy import StandingLifecycle

    invoke, sim, calls, target, config_path = owner_mock_runtime(tmp_path, monkeypatch)
    original = StandingLifecycle.execute

    def edit_then_submit(engine, key, intent):
        config_path.write_text(config_path.read_text() + "\n# owner changed configuration\n")
        return original(engine, key, intent)

    monkeypatch.setattr(StandingLifecycle, "execute", edit_then_submit)
    try:
        assert invoke()["status"] == "HALTED"
        assert not any(
            m["method"] == "tools/call" and m["params"]["name"] == "place_equity_order"
            for m in calls
        )
    finally:
        sim.close()


def test_broker_exceptional_review_approval_still_blocks(tmp_path, monkeypatch):
    invoke, sim, calls, target, config_path = owner_mock_runtime(
        tmp_path, monkeypatch, review_approval=True
    )
    try:
        result = invoke()
        assert result["status"] == "HALTED" and "approval" in result["reason"]
        assert not any(
            m["method"] == "tools/call" and m["params"]["name"] == "place_equity_order"
            for m in calls
        )
    finally:
        sim.close()


def test_live_partial_exit_remains_visible_without_second_exit(tmp_path, monkeypatch):
    invoke, sim, calls, target, config_path = owner_mock_runtime(
        tmp_path, monkeypatch, exit_scenario="partial_fill"
    )
    try:
        result = invoke()
        assert result["status"] == "HALTED" and result["outstanding_incident"]
        assert not result["flat_bot_position"]
        assert invoke()["status"] == "HALTED"
        placements = [
            m["params"]["arguments"]
            for m in calls
            if m["method"] == "tools/call" and m["params"]["name"] == "place_equity_order"
        ]
        assert [p["side"] for p in placements] == ["buy", "sell"]
    finally:
        sim.close()


def test_installed_entry_script_runs_local_live_and_recovers(tmp_path, monkeypatch, capsys):
    import runpy
    import sys
    from pathlib import Path

    invoke, sim, calls, target, config_path = owner_mock_runtime(tmp_path, monkeypatch)
    entry = Path(sys.executable).parent / "tradeagent"
    if not entry.exists():
        sim.close()
        pytest.skip("console script checked in isolated wheel installation")
    monkeypatch.setattr(
        sys, "argv", [str(entry), "run-once", "--live", "--config", str(config_path)]
    )
    try:
        for _ in range(2):
            with pytest.raises(SystemExit) as exit_info:
                runpy.run_path(str(entry), run_name="__main__")
            assert exit_info.value.code == 0
            result = json.loads(capsys.readouterr().out)
            assert result["mode"] == "LIVE" and result["status"] == "COMPLETED"
            assert result["flat_bot_position"] and result["cash_reconciled"]
        assert (
            sum(
                m["method"] == "tools/call" and m["params"]["name"] == "place_equity_order"
                for m in calls
            )
            == 2
        )
    finally:
        sim.close()


def test_in_memory_policy_cannot_weaken_owner_toml(tmp_path, monkeypatch):
    invoke, sim, calls, target, config_path = owner_mock_runtime(tmp_path, monkeypatch)
    settings = load_live_config(config_path)
    settings.options["max_notional"] = "1000"
    try:
        with pytest.raises(Halt, match="differs from TOML"):
            run_live(settings)
        assert calls == [] and not target.exists()
    finally:
        sim.close()


def test_configured_account_mismatch_cannot_place(tmp_path, monkeypatch):
    invoke, sim, calls, target, config_path = owner_mock_runtime(tmp_path, monkeypatch)
    config_path.write_text(
        config_path.read_text().replace("[broker]", '[broker]\naccount_sha256 = "' + "0" * 64 + '"')
    )
    try:
        with pytest.raises(Halt, match="account pin"):
            invoke()
        assert not any(
            m["method"] == "tools/call"
            and m["params"]["name"].startswith(("review_", "place_", "cancel_"))
            for m in calls
        )
        assert not target.exists()
    finally:
        sim.close()


def test_local_readiness_never_instantiates_execution_state(tmp_path, monkeypatch):
    from tradeagent.oneshot_cli import live_check

    invoke, sim, calls, target, config_path = owner_mock_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "tradeagent.oneshot_cli.ExternalOAuthToken", lambda *args: lambda: "TEST_ONLY_TOKEN"
    )

    monkeypatch.setattr("tradeagent.oneshot_cli.Broker", lambda *args: sim)
    try:
        result = live_check(settings=load_live_config(config_path))
        assert result["status"] == "READ_ONLY_READY" and not result["armed"]
        assert result["real_review_place_cancel_calls"] == 0
        assert (
            not target.exists()
            and not config_path.with_name(config_path.name + ".run.json").exists()
        )
        assert not any(
            m["method"] == "tools/call"
            and m["params"]["name"].startswith(("review_", "place_", "cancel_"))
            for m in calls
        )
    finally:
        sim.close()
