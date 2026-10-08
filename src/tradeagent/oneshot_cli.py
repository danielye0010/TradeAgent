"""Packaged one-shot paper execution and strictly read-only LIVE readiness check."""

import argparse
import json
import os
import time
from pathlib import Path

from .broker import Broker
from .model import Config, Halt, Risk, digest
from .oneshot import atomic_json, run_live, run_paper
from .risk import check_state
from .schema import Contracts, structural
from .simulator import SCENARIOS
from .standalone_mcp import READ_TOOLS, ExternalOAuthToken, ReadOnlyMCP

ReadOnlyPreflightMCP = ReadOnlyMCP


def live_blockers():
    return [
        "Owner-operated prepare-once, offline signature and setup-once are required; no standing authorization was supplied to this read-only check."
    ]


def live_check(oauth_helper=None, root=None, timeout=10):
    """No State object, execution adapter, review, placement or cancellation."""
    result = {
        "status": "LIVE BLOCKED",
        "live_ready": False,
        "armed": False,
        "authenticated": False,
        "account_verified": False,
        "broker_calls": [],
        "real_review_place_cancel_calls": 0,
        "blockers": live_blockers(),
        "timestamp": time.time(),
    }
    if oauth_helper is None:
        result["connectivity_blocker"] = (
            "Set --oauth-helper to the existing external noninteractive OAuth helper; credentials stay outside the package."
        )
        return result
    bridge = None
    try:
        tokens = ExternalOAuthToken(oauth_helper, root or Path.cwd())
        bridge = ReadOnlyPreflightMCP(tokens, timeout)
        with bridge:
            result.update(
                authenticated=True,
                server_version=bridge.server_info.get("version"),
                read_schema_hash=bridge.contracts.hash,
                transport="direct official MCP HTTP; no Codex",
            )
            full = Contracts()
            drift = [
                name
                for name, expected in full.tools.items()
                if name not in READ_TOOLS
                and structural({k: bridge.tools.get(name, {}).get(k) for k in expected}) != expected
            ]
            result["write_contract_drift"] = drift
            result["observed_write_input_fields"] = {
                name: sorted(
                    bridge.tools.get(name, {}).get("inputSchema", {}).get("properties", {})
                )
                for name in drift
            }
            if drift:
                result["blockers"].append(
                    "Authenticated write catalog differs from frozen schemas: " + ", ".join(drift)
                )
            # Broker exposes read operations only. StandaloneMCP.read independently
            # enforces READ_TOOLS before network; never instantiate a write transport.
            config = Config(allowed_symbols=["QQQ", "IWM"])
            result["snapshot_attempts"] = []
            for attempt in range(2):
                try:
                    config_broker = Broker(bridge, config, Risk())
                    snapshot = config_broker.snapshot()
                    result["snapshot_attempts"].append(
                        {"attempt": attempt + 1, "status": "normalized"}
                    )
                    break
                except Halt as exc:
                    result["snapshot_attempts"].append(
                        {"attempt": attempt + 1, "status": "rejected", "reason": str(exc)}
                    )
                    if str(exc) != "quote/depth disagreement; refresh required" or attempt == 1:
                        raise
            result.update(
                snapshot_normalized=True,
                regular_session=snapshot.regular_session,
            )
            check_state(snapshot, Risk(), time.time())
            result["account_verified"] = True
            setting = bridge.read(
                "get_trade_approval_setting",
                {"account_number": config_broker.account["account_number"]},
            )["data"]["setting"]
            if setting["account_number"] != config_broker.account["account_number"]:
                raise Halt("broker approval setting account mismatch")
            result["broker_approval_setting_verified"] = True
            if setting["human_must_approve_trades"]:
                result["blockers"].append(
                    "Broker trade approvals enabled; owner configuration required"
                )
            result["equity_submission_fields"] = sorted(
                bridge.tools["place_equity_order"]["inputSchema"]["properties"]
            )
            result["broker_permissions"] = (
                "read connectivity verified; submission approval behavior not exercised"
            )
    except (Halt, OSError, ValueError, KeyError, TypeError) as exc:
        result["connectivity_blocker"] = (
            str(exc)
            if isinstance(exc, Halt)
            else "read-only preflight failed; inspect helper permissions and current broker contracts"
        )
    finally:
        if bridge:
            result["broker_calls"] = bridge.calls
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser(
        "live-check", help="read-only connectivity and readiness; never orders"
    )
    check.add_argument("--oauth-helper", type=Path)
    check.add_argument("--root", type=Path, default=Path.cwd())
    check.add_argument("--output", type=Path, default=Path("work/live-check/report.json"))
    check.add_argument("--timeout", type=int, default=10)
    once = commands.add_parser(
        "run-once", help="one isolated owner-authorized entry and automatic exit"
    )
    modes = once.add_mutually_exclusive_group(required=True)
    modes.add_argument("--paper", action="store_true")
    modes.add_argument(
        "--live",
        action="store_true",
        help="owner-launched real one-shot; signed setup and broker permissions required",
    )
    once.add_argument("--state-dir", type=Path)
    once.add_argument("--authorization-dir", type=Path)
    once.add_argument("--oauth-helper", type=Path)
    once.add_argument("--root", type=Path, default=Path.cwd())
    once.add_argument("--max-notional", default="25")
    once.add_argument("--hold-seconds", type=int, default=3600)
    once.add_argument("--polls", type=int, default=3)
    once.add_argument("--entry-scenario", choices=sorted(SCENARIOS), default="full_fill")
    once.add_argument("--exit-scenario", choices=sorted(SCENARIOS), default="full_fill")
    once.add_argument("--kill-switch", type=Path)
    prepare = commands.add_parser(
        "prepare-once", help="read-only unsigned owner authorization request"
    )
    prepare.add_argument("--state-dir", type=Path, required=True)
    prepare.add_argument("--oauth-helper", type=Path, required=True)
    prepare.add_argument("--root", type=Path, default=Path.cwd())
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--max-notional", default="25")
    prepare.add_argument("--hold-seconds", type=int, default=3600)
    prepare.add_argument("--polls", type=int, default=3)
    setup = commands.add_parser(
        "setup-once", help="owner installs an externally signed standing grant"
    )
    setup.add_argument("--request", type=Path, required=True)
    setup.add_argument("--signature", type=Path, required=True)
    setup.add_argument("--public-key", type=Path, required=True)
    setup.add_argument("--public-key-sha256", required=True)
    setup.add_argument("--authorization-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "live-check":
            if not 1 <= args.timeout <= 60:
                raise Halt("read-only timeout must be 1..60 seconds")
            helper = args.oauth_helper or os.getenv("TRADEAGENT_OAUTH_HELPER")
            result = live_check(helper, args.root, args.timeout)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(args.output, result)
            code = 2  # Connectivity success is not LIVE readiness.
        elif args.command == "prepare-once":
            from .execution_policy import live_config, request_policy
            from .model import dec
            from .state import dumps

            if (
                not 0 < dec(args.max_notional) <= 1000
                or not 0 <= args.hold_seconds <= 21600
                or not 1 <= args.polls <= 30
            ):
                raise Halt("invalid one-shot owner limits")
            config, risk = live_config(args.state_dir, ["QQQ", "IWM"]), Risk()
            options = {
                "max_notional": str(dec(args.max_notional)),
                "hold_seconds": args.hold_seconds,
                "polls": args.polls,
            }
            with ReadOnlyPreflightMCP(
                ExternalOAuthToken(args.oauth_helper, args.root), 20
            ) as bridge:
                Contracts().check_current(bridge.tools, bridge.server_info["version"])
                broker = Broker(bridge, config, risk)
                snapshot = broker.snapshot()
                check_state(snapshot, risk, time.time())
                policy = request_policy(
                    config,
                    risk,
                    digest(broker.account["account_number"]),
                    options,
                )
            os.umask(0o077)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x") as stream:
                stream.write(dumps(policy))  # exact canonical bytes for offline signing
            result = {
                "status": "UNSIGNED_OWNER_REQUEST",
                "armed": False,
                "request": str(args.output),
            }
            code = 0
        elif args.command == "setup-once":
            from .execution_policy import install_authorization

            result = install_authorization(
                args.request,
                args.public_key,
                args.public_key_sha256,
                args.signature,
                args.authorization_dir,
            )
            code = 0
        elif args.live:
            result = run_live(
                args.state_dir,
                args.authorization_dir,
                args.oauth_helper or os.getenv("TRADEAGENT_OAUTH_HELPER"),
                args.root,
            )
            result = {
                k: result[k]
                for k in (
                    "mode",
                    "status",
                    "outstanding_incident",
                    "cash_reconciled",
                    "flat_bot_position",
                )
                if k in result
            }
            code = 0 if result["status"] in {"COMPLETED", "NO_TRADE"} else 2
        else:
            result = run_paper(
                args.state_dir or Path("work/one-shot-paper"),
                args.max_notional,
                args.hold_seconds,
                args.polls,
                args.entry_scenario,
                args.exit_scenario,
                args.kill_switch,
            )
            code = 0 if result["status"] in {"COMPLETED", "NO_TRADE"} else 2
        print(json.dumps(result, indent=2, default=str, allow_nan=False))
        return code
    except (Halt, OSError, ValueError, KeyError, TypeError) as exc:
        print(
            json.dumps(
                {
                    "status": "LIVE BLOCKED"
                    if args.command == "run-once" and args.live
                    else "HALTED",
                    "reason": str(exc)
                    if isinstance(exc, Halt)
                    else "invalid local input/runtime; no submission retry",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
