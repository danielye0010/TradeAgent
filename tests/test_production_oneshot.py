"""Production controller, signed owner setup and real wire encoding with HTTP replaced."""

import hashlib
import io
import json
import subprocess
import time

import pytest

from tradeagent.execution_policy import install_authorization, live_config, request_policy
from tradeagent.model import Halt, Risk
from tradeagent.oneshot import paper_snapshot, run_live
from tradeagent.schema import Contracts
from tradeagent.simulator import SimClock, SimulatedMCP
from tradeagent.standalone_mcp import ReadOnlyMCP, StandaloneMCP, TransientReadFailure
from tradeagent.state import dumps


def signed_mock_runtime(tmp_path, monkeypatch, scenario="full_fill", approval=False):
    """Test-only key/account. Every HTTPS connection is replaced before invocation."""
    clock = SimClock()
    monkeypatch.setattr(time, "time", clock)
    monkeypatch.setattr("tradeagent.oneshot.wait_for_poll", clock.advance)
    target, trust = tmp_path / "live-state", tmp_path / "owner-authorization"
    options = {"max_notional": "25", "hold_seconds": 0, "polls": 2}
    config = live_config(target, ["QQQ", "IWM"])
    sim = SimulatedMCP(tmp_path / "mock-broker", clock, scenario, paper_snapshot(clock))
    request, private, public, signature = (
        tmp_path / n
        for n in ("request", "test-only-private.pem", "test-only-public.pem", "signature")
    )
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
        check=True,
        capture_output=True,
    )
    request.write_text(
        dumps(
            request_policy(
                config,
                Risk(),
                __import__("tradeagent.model", fromlist=["digest"]).digest(
                    sim.account["account_number"]
                ),
                options,
                clock,
            )
        )
    )
    subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-sign",
            "-inkey",
            str(private),
            "-rawin",
            "-in",
            str(request),
            "-out",
            str(signature),
        ],
        check=True,
        capture_output=True,
    )
    for path in (request, public, signature):
        path.chmod(0o600)
    install_authorization(
        request, public, hashlib.sha256(public.read_bytes()).hexdigest(), signature, trust
    )
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
                        sim.scenario = "full_fill"
                    output = sim.invoke(name, args)
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
        return run_live(target, trust, tmp_path / "TEST_ONLY_HELPER", tmp_path)

    return invoke, sim, calls, target, trust


@pytest.mark.parametrize(
    "scenario", ["full_fill", "partial_fill", "lost_ack", "timeout_before_ack"]
)
def test_same_live_controller_and_direct_wire_non_live(tmp_path, monkeypatch, scenario):
    invoke, sim, calls, target, trust = signed_mock_runtime(tmp_path, monkeypatch, scenario)
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
    invoke, sim, calls, target, trust = signed_mock_runtime(
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


def test_owner_signature_and_broker_approval_not_bypassed(tmp_path, monkeypatch):
    invoke, sim, calls, target, trust = signed_mock_runtime(tmp_path, monkeypatch, approval=True)
    try:
        assert invoke()["status"] == "HALTED"
        assert not any(
            m["method"] == "tools/call" and m["params"]["name"].startswith(("review_", "place_"))
            for m in calls
        )
        artifact = json.loads((trust / "authorization.json").read_text())
        artifact["policy"]["options"]["max_notional"] = "1000"
        (trust / "authorization.json").write_text(dumps(artifact))
        before = len(calls)
        with pytest.raises(Halt, match="signature|authorization/configuration"):
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
    invoke, sim, calls, target, trust = signed_mock_runtime(tmp_path, monkeypatch)
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
    invoke, sim, calls, target, trust = signed_mock_runtime(
        tmp_path, monkeypatch, "cancel_rejected"
    )
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


def test_expired_signed_grant_stops_before_broker_connection(tmp_path, monkeypatch):
    invoke, sim, calls, target, trust = signed_mock_runtime(tmp_path, monkeypatch)
    try:
        sim.clock.advance(86400)
        with pytest.raises(Halt, match="expired"):
            invoke()
        assert calls == []
    finally:
        sim.close()


def test_grant_requires_authorized_time_for_the_exit_before_entry(tmp_path, monkeypatch):
    invoke, sim, calls, target, trust = signed_mock_runtime(tmp_path, monkeypatch)
    try:
        sim.clock.advance(86400 - 100)
        result = invoke()
        assert result["status"] == "HALTED"
        assert "insufficient time" in result["reason"]
        assert not any(
            m["method"] == "tools/call" and m["params"]["name"].startswith(("review_", "place_"))
            for m in calls
        )
    finally:
        sim.close()


def test_kill_arriving_after_review_blocks_production_submission(tmp_path, monkeypatch):
    from tradeagent.execution_policy import StandingLifecycle

    invoke, sim, calls, target, trust = signed_mock_runtime(tmp_path, monkeypatch)
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
