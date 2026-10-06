import copy
import json
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from test_release_policy import artifact, facts, histories, tiny_snapshot

from tradebot.canary import CanarySelector
from tradebot.cli import main
from tradebot.model import Config, Halt, Risk, digest
from tradebot.policy import GrantContext, PolicyGuard, PolicyLimits, PolicySignature
from tradebot.release import require_real_release
from tradebot.simulator import SimClock, SimulatedMCP
from tradebot.standalone import run_policy_once
from tradebot.standalone_mcp import ExternalOAuthToken, StandaloneMCP
from tradebot.standalone_reference import STATUS_URL, action_rows
from tradebot.state import State
from tradebot.supervised import OfficialExecutionAdapter


@contextmanager
def runtime(tmp_path, monkeypatch, scenario="full_fill", phase="CANARY"):
    clock = SimClock()
    monkeypatch.setattr(time, "time", clock)
    sim = SimulatedMCP(tmp_path / "broker", clock, scenario, tiny_snapshot(clock))
    state = State(tmp_path / "agent")
    config = Config(
        mode="SUPERVISED",
        supervised_enabled=True,
        allowed_symbols=["TINY"],
        state_dir=str(tmp_path / "agent"),
        strategy_version="infrastructure-canary-v1",
    )
    risk, limits, selector = Risk(), PolicyLimits(), CanarySelector()
    fs = facts(clock)
    adapter = OfficialExecutionAdapter(sim, sim, clock)
    ctx = GrantContext(
        adapter.account_digest,
        "synthetic-v1",
        "b" * 64,
        config.strategy_version,
        "c" * 64,
        digest(asdict(config)),
        digest(asdict(risk)),
        adapter.contracts.hash,
        selector.hash,
        digest({k: asdict(v) for k, v in fs.items()}),
    )
    art = artifact(clock, ctx, limits, phase)
    guard = PolicyGuard(art, ctx, limits, state, config, risk, True, clock, lambda: ctx)

    def run(action="canary-buy", **kwargs):
        return run_policy_once(
            action,
            state,
            adapter,
            config,
            risk,
            guard,
            fs,
            selector,
            lambda: histories(clock),
            clock,
            **kwargs,
        )

    try:
        yield run, sim, state, clock, guard
    finally:
        state.close()
        sim.close()


def placements(sim):
    return [c for c in sim.calls if c["tool"] == "place_equity_order"]


def test_standalone_buy_exit_next_session_existing_lifecycle(tmp_path, monkeypatch):
    with runtime(tmp_path, monkeypatch) as (run, sim, state, clock, guard):
        buy = run()
        assert buy["status"] == "filled" and buy["account_after"]["cash"] == "97.68"
        assert run("canary-exit")["status"] == "halted"
        assert len(placements(sim)) == 1
        clock.advance(84600)
        sell = run("canary-exit")
        assert sell["status"] == "filled" and sell["account_after"]["positions"] == {}
        assert sell["account_after"]["cash"] == "99.99"
        assert len(placements(sim)) == 2
        assert state.db.execute("SELECT COUNT(*) FROM policy_decisions").fetchone()[0] == 2
        assert state.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert [
            json.loads(line) for line in (state.directory / "events.jsonl").read_text().splitlines()
        ]
        with state.lock(60):
            pass
        assert (
            state.db.execute(
                "SELECT COUNT(*) FROM events WHERE kind='startup_reconciliation'"
            ).fetchone()[0]
            >= 2
        )
        assert (
            state.db.execute(
                "SELECT COUNT(*) FROM events WHERE kind='fill_reconciliation'"
            ).fetchone()[0]
            == 2
        )


@pytest.mark.parametrize(
    "scenario,status",
    [
        ("accepted", "pending"),
        ("partial_fill", "pending"),
        ("rejected", "rejected"),
        ("lost_ack", "pending"),
        ("timeout_after_acceptance", "pending"),
        ("timeout_before_ack", "halted"),
    ],
)
def test_standalone_uncertain_and_pending_never_replay(tmp_path, monkeypatch, scenario, status):
    with runtime(tmp_path, monkeypatch, scenario) as (run, sim, _s, _c, _g):
        assert run()["status"] == status
        assert len(placements(sim)) == 1
        again = run()
        assert again["status"] in {"halted", "reconciled_only"}
        assert len(placements(sim)) == 1
        assert not any("cancel" in c["tool"] for c in sim.calls)


def test_duplicate_runner_cannot_acquire_lock_or_submit(tmp_path, monkeypatch):
    with runtime(tmp_path, monkeypatch) as (run, sim, state, _c, _g):
        with state.lock(60):
            with pytest.raises(Halt):
                run()
        assert placements(sim) == []
        assert run()["status"] == "filled"
        assert run()["status"] == "halted"
        assert len(placements(sim)) == 1


@pytest.mark.parametrize("fault", ["expired", "context", "signature", "kill", "quantity"])
def test_runtime_policy_halts_without_write(tmp_path, monkeypatch, fault):
    with runtime(tmp_path, monkeypatch) as (run, sim, state, clock, guard):
        if fault == "expired":
            guard.artifact["policy"]["expires_at"] = clock()
        elif fault == "context":
            guard.context_reader = lambda: replace(guard.context, risk_hash="changed")
        elif fault == "signature":
            guard.artifact["actor"] = "INVALID"
        elif fault == "kill":
            guard.halt_entries("test")
        else:
            guard.artifact["policy"]["limits"]["max_order_notional"] = "0.01"
        try:
            result = run()
            assert result["status"] == "halted"
        except Halt:
            pass
        assert placements(sim) == []


def test_production_missing_key_cli_halts_before_auth_and_db(tmp_path, capsys):
    root = Path(__file__).resolve().parents[1]
    for action in ("canary-buy", "canary-exit"):
        assert (
            main(
                [
                    action,
                    "--config",
                    str(root / "config/canary.example.json"),
                    "--risk",
                    str(root / "config/risk.example.json"),
                ]
            )
            == 2
        )
        assert "release closed" in capsys.readouterr().err
    with pytest.raises(Halt):
        require_real_release()
    with pytest.raises(Halt):
        PolicySignature().verify({"policy": {}, "signature": "bad", "simulation": False}, False)


def test_invalid_envelope_signature_drift_and_no_simulated_real_capability(tmp_path, monkeypatch):
    with runtime(tmp_path, monkeypatch) as (run, sim, _s, _c, guard):
        with pytest.raises(Halt):
            require_real_release(guard)
        assert placements(sim) == []
        art = copy.deepcopy(guard.artifact)
        art["policy"]["no_leverage"] = False
        guard.artifact = art
        with pytest.raises(Halt):
            run()


def test_fresh_reference_unknown_no_trade_and_positive_action_halts(tmp_path, monkeypatch):
    with runtime(tmp_path, monkeypatch) as (run, sim, _s, clock, _g):
        assert run(fresh_facts_reader=lambda: {})["status"] == "NO_TRADE"
        bad = {"TINY": replace(facts(clock)["TINY"], corporate_action_clear=False)}
        assert run(fresh_facts_reader=lambda: bad)["status"] == "NO_TRADE"
        assert placements(sim) == []


def test_limited_run_once_does_not_force_positive_signal(tmp_path, monkeypatch):
    with runtime(tmp_path, monkeypatch, phase="LIMITED_EQUITY") as (run, sim, _s, _c, _g):
        result = run("run-once")
        assert result["status"] == "NO_TRADE"
        assert placements(sim) == []


def test_direct_transport_never_redirects_retries_or_calls_codex(monkeypatch):
    import tradebot.standalone_mcp as module

    sends = []

    class Connection:
        def __init__(self, host, timeout):
            assert host == "agent.robinhood.com"

        def request(self, *args, **kwargs):
            sends.append((args, kwargs))

        def getresponse(self):
            class Response:
                status = 302

            return Response()

        def close(self):
            pass

    monkeypatch.setattr(module.http.client, "HTTPSConnection", Connection)
    mcp = StandaloneMCP(lambda: "SYNTHETIC_TOKEN")
    with pytest.raises(Halt, match="no retry or redirect"):
        mcp.rpc("initialize", {})
    assert len(sends) == 1
    with pytest.raises(Halt, match="boundary"):
        mcp.rpc("tools/call", {"name": "place_equity_order"})
    assert len(sends) == 1
    assert "codex" not in Path(module.__file__).read_text().lower().split("import")[1]


def test_before_send_runs_after_token_and_can_stop_network(monkeypatch):
    import tradebot.standalone_mcp as module

    order = []

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            order.append("network")

        def close(self):
            pass

    monkeypatch.setattr(module.http.client, "HTTPSConnection", Connection)

    def token():
        order.append("token")
        return "SYNTHETIC_TOKEN"

    from tradebot.standalone_mcp import WireAuthorization

    permit = WireAuthorization(
        None, None, "synthetic", "place_equity_order", digest({}), None, None, None
    )

    def gate(self):
        order.append("gate")
        raise Halt("expired at send")

    monkeypatch.setattr(WireAuthorization, "__call__", gate)
    monkeypatch.setattr(WireAuthorization, "validate_request", lambda self, params: None)

    with pytest.raises(Halt, match="expired at send"):
        StandaloneMCP(token).rpc(
            "tools/call", {"name": "place_equity_order", "arguments": {}}, before_send=permit
        )
    assert order == ["token", "gate"]


def test_reference_malformed_and_positive_records_fail_closed():
    header = (
        "<tr>"
        + "".join(
            "<th>" + x + "</th>"
            for x in [
                "Effective Date",
                "Symbol",
                "Company Name",
                "Issue Event",
                "Downgrade Reason",
                "Old Financial Status",
                "New Financial Status",
            ]
        )
        + "</tr>"
    )
    row = "<tr><td>2026-10-06</td><td>TINY</td><td>Tiny Common Stock</td><td>Split</td><td></td><td></td><td></td></tr>"
    assert action_rows("<table>" + header + row + "</table>", STATUS_URL)[0]["Symbol"] == "TINY"
    for text in (
        "<html>unavailable</html>",
        "<table>" + header + "</table>",
        "<table>" + header + row.replace("<td>Split</td>", "") + "</table>",
    ):
        with pytest.raises(Halt):
            action_rows(text, STATUS_URL)


def test_token_helper_must_be_outside_repository_owner_only(tmp_path):
    helper = tmp_path / "helper"
    helper.write_text("#!/bin/sh\nexit 1\n")
    helper.chmod(0o700)
    with pytest.raises(Halt, match="outside"):
        ExternalOAuthToken(helper, tmp_path)
    helper.chmod(0o755)
    with pytest.raises(Halt, match="owner-only"):
        ExternalOAuthToken(helper, tmp_path / "repository")


@pytest.mark.parametrize("fault", ["expired", "key", "phase", "context", "quantity", "health"])
def test_real_signed_boundary_fails_closed_with_synthetic_key(tmp_path, monkeypatch, fault):
    # Keys exist only in temporary test storage. No real enrollment or HTTP send.
    import base64
    import hashlib
    import subprocess

    import tradebot.policy as module
    from tradebot.model import Intent, dec
    from tradebot.state import dumps

    with runtime(tmp_path, monkeypatch) as (_run, sim, state, clock, simulated):
        private, public, msg, sig = (
            tmp_path / n for n in ("private.pem", "public.pem", "message", "signature")
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
        monkeypatch.setattr(
            module, "AUTONOMOUS_PUBLIC_KEY_SHA256", hashlib.sha256(public.read_bytes()).hexdigest()
        )
        policy = copy.deepcopy(simulated.artifact["policy"])
        if fault == "expired":
            policy["expires_at"] = clock()
        msg.write_text(dumps(policy))
        subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-sign",
                "-inkey",
                str(private),
                "-rawin",
                "-in",
                str(msg),
                "-out",
                str(sig),
            ],
            check=True,
            capture_output=True,
        )
        art = {
            "policy": policy,
            "simulation": False,
            "signature": base64.b64encode(sig.read_bytes()).decode(),
        }
        guard = PolicyGuard(
            art,
            simulated.context,
            simulated.limits,
            state,
            simulated.config,
            simulated.risk,
            False,
            clock,
            simulated.context_reader,
            PolicySignature(public),
        )
        intent = Intent("TINY", "buy", dec(1), dec("2.32"))
        snapshot = sim.snapshot()
        if fault != "expired":
            require_real_release(guard, intent, snapshot, 100)
        if fault == "key":
            monkeypatch.setattr(module, "AUTONOMOUS_PUBLIC_KEY_SHA256", None)
        if fault == "phase":
            monkeypatch.setattr(module, "AUTONOMOUS_DEPLOYMENT_PHASE", "LIMITED_EQUITY")
        if fault == "context":
            guard.context_reader = lambda: replace(guard.context, schema_hash="drift")
        if fault == "quantity":
            intent = replace(intent, quantity=dec(2))
        if fault == "health":
            guard.halt_entries("test external kill")
        with pytest.raises(Halt):
            require_real_release(guard, intent, snapshot, 100)
        assert placements(sim) == []
        private.unlink()


def test_direct_json_and_sse_catalog_initialization_without_codex(monkeypatch):
    import io

    import tradebot.standalone_mcp as module
    from tradebot.schema import Contracts

    calls = []
    pins = Contracts()

    class Response:
        def __init__(self, body, status=200, sse=False):
            self.status, self.sse = status, sse
            raw = json.dumps(body).encode() if body is not None else b""
            self.file = io.BytesIO(b"data: " + raw + b"\n\n" if sse else raw)

        def getheader(self, name, default=None):
            if name == "Content-Type":
                return "text/event-stream" if self.sse else "application/json"
            if name == "Mcp-Session-Id":
                return "synthetic-session"
            return default

        def read(self, n):
            return self.file.read(n)

        def readline(self, n):
            return self.file.readline(n)

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, body, headers):
            self.message = json.loads(body)
            calls.append((self.message, headers))

        def getresponse(self):
            m = self.message
            if m["method"] == "notifications/initialized":
                return Response(None, 202)
            if m["method"] == "initialize":
                result = {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": "robinhood-trading", "version": "1.6.2"},
                }
            elif m["method"] == "tools/list":
                result = {"tools": [{"name": k, **v} for k, v in pins.tools.items()]}
            else:
                raise AssertionError("no broker calls in this handshake test")
            return Response(
                {"jsonrpc": "2.0", "id": m["id"], "result": result}, sse=m["method"] == "tools/list"
            )

        def close(self):
            pass

    monkeypatch.setattr(module.http.client, "HTTPSConnection", Connection)
    with StandaloneMCP(lambda: "SYNTHETIC_TOKEN") as bridge:
        assert len(bridge.tools) == 18
        assert bridge.protocol == "2025-11-25"
        assert bridge.calls == []
    assert len(calls) == 3
    assert all(h["MCP-Protocol-Version"] == "2025-11-25" for _, h in calls[1:])
    assert all(h["Mcp-Session-Id"] == "synthetic-session" for _, h in calls[1:])


@pytest.mark.parametrize("expire_at_wire", [False, True])
def test_new_execution_boundary_with_actual_synthetic_signature_no_network(
    tmp_path, monkeypatch, expire_at_wire
):
    import base64
    import hashlib
    import subprocess

    import tradebot.policy as module
    from tradebot.standalone_mcp import StandaloneExecutionTransport
    from tradebot.state import dumps

    with runtime(tmp_path, monkeypatch) as (_run, sim, state, clock, simulated):
        private, public, msg, sig = (
            tmp_path / n for n in ("private.pem", "public.pem", "message", "signature")
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
        monkeypatch.setattr(
            module, "AUTONOMOUS_PUBLIC_KEY_SHA256", hashlib.sha256(public.read_bytes()).hexdigest()
        )
        policy = copy.deepcopy(simulated.artifact["policy"])
        policy["expires_at"] = clock() + 20
        msg.write_text(dumps(policy))
        subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-sign",
                "-inkey",
                str(private),
                "-rawin",
                "-in",
                str(msg),
                "-out",
                str(sig),
            ],
            check=True,
            capture_output=True,
        )
        art = {
            "policy": policy,
            "simulation": False,
            "signature": base64.b64encode(sig.read_bytes()).decode(),
        }
        guard = PolicyGuard(
            art,
            simulated.context,
            simulated.limits,
            state,
            simulated.config,
            simulated.risk,
            False,
            clock,
            simulated.context_reader,
            PolicySignature(public),
        )

        class SyntheticWire:
            contracts = sim.contracts
            tools = contracts.tools
            server_info = {"version": "1.6.2"}

            def _call(self, name, args, before_send):
                if expire_at_wire and name == "place_equity_order":
                    clock.advance(21)
                before_send.validate_request({"name": name, "arguments": args})
                before_send()
                return sim.invoke(name, args)

        wire = SyntheticWire()
        transport = StandaloneExecutionTransport(wire, state, sim, guard)
        adapter = OfficialExecutionAdapter(sim, transport, clock)
        result = run_policy_once(
            "canary-buy",
            state,
            adapter,
            simulated.config,
            simulated.risk,
            guard,
            facts(clock),
            CanarySelector(),
            lambda: histories(clock),
            clock,
        )
        assert result["status"] == ("halted" if expire_at_wire else "filled")
        assert len(placements(sim)) == (0 if expire_at_wire else 1)
        with pytest.raises(Halt):
            transport.invoke("cancel_equity_order", {})
        if not expire_at_wire:
            args = (
                placements(sim)[0]["arguments"]
                if "arguments" in placements(sim)[0]
                else placements(sim)[0]["args"]
            )
            with pytest.raises(Halt):
                transport.invoke("place_equity_order", args)
        private.unlink()


def test_raw_transport_callback_is_not_a_release_bypass(monkeypatch):
    import tradebot.standalone_mcp as module

    def prohibited(*args, **kwargs):
        raise AssertionError("network touched")

    monkeypatch.setattr(module.http.client, "HTTPSConnection", prohibited)
    with pytest.raises(Halt, match="boundary"):
        StandaloneMCP(lambda: "SYNTHETIC_TOKEN").rpc(
            "tools/call", {"name": "place_equity_order", "arguments": {}}, before_send=lambda: None
        )
