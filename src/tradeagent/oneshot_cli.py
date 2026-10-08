"""Packaged one-shot paper execution and strictly read-only LIVE readiness check."""

import argparse
import json
import os
import time
from pathlib import Path

from .broker import Broker
from .legacy.policy import require_autonomous_release
from .model import Config, Halt, Risk, digest
from .oneshot import atomic_json, run_paper
from .risk import check_state
from .schema import Contracts, structural
from .simulator import SCENARIOS
from .standalone_mcp import READ_TOOLS, ExternalOAuthToken, StandaloneMCP


class ReadOnlyPreflightMCP(StandaloneMCP):
    """Read pins only; reject every write before token refresh or HTTP."""

    def __init__(self, token_source, timeout=10):
        super().__init__(token_source, timeout)
        self.contracts.tools = {
            name: value for name, value in self.contracts.tools.items() if name in READ_TOOLS
        }
        self.contracts.hash = digest(self.contracts.tools)

    def rpc(self, method, params, *, notification=False, before_send=None):
        if before_send is not None or (
            method == "tools/call" and params.get("name") not in READ_TOOLS
        ):
            raise Halt("read-only preflight cannot review, place, cancel or authorize writes")
        return super().rpc(method, params, notification=notification)


def live_blockers():
    blockers = []
    try:
        Config(mode="LIVE", live_enabled=True).validate()
    except Halt as exc:
        blockers.append(str(exc))
    try:
        require_autonomous_release()
    except Halt as exc:
        blockers.append(str(exc))
    blockers.extend(
        [
            "The production one-shot timed exit/cancellation policy is not authorized or verified; legacy canary requires a later-session exit and forbids cancellation.",
            "Owner/account-bound standing authorization and production recovery/exit capability have not been verified.",
        ]
    )
    return blockers


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
                    snapshot = Broker(bridge, config, Risk()).snapshot()
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
                account_key=snapshot.account_key,
                cash=str(snapshot.cash),
                buying_power=str(snapshot.buying_power),
                nav=str(snapshot.nav),
                positions_count=len(snapshot.positions),
                orders_count=len(snapshot.orders),
                regular_session=snapshot.regular_session,
            )
            check_state(snapshot, Risk(), time.time())
            result["account_verified"] = True
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
    once = commands.add_parser("run-once", help="one isolated paper entry and automatic exit")
    modes = once.add_mutually_exclusive_group(required=True)
    modes.add_argument("--paper", action="store_true")
    modes.add_argument(
        "--live",
        action="store_true",
        help="reports unmet production prerequisites; does not submit",
    )
    once.add_argument("--state-dir", type=Path, default=Path("work/one-shot-paper"))
    once.add_argument("--max-notional", default="25")
    once.add_argument("--hold-seconds", type=int, default=3600)
    once.add_argument("--polls", type=int, default=3)
    once.add_argument("--entry-scenario", choices=sorted(SCENARIOS), default="full_fill")
    once.add_argument("--exit-scenario", choices=sorted(SCENARIOS), default="full_fill")
    once.add_argument("--kill-switch", type=Path)
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
        elif args.live:
            result = {
                "status": "LIVE BLOCKED",
                "armed": False,
                "real_broker_calls": 0,
                "blockers": live_blockers(),
            }
            code = 2
        else:
            result = run_paper(
                args.state_dir,
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
                    "status": "HALTED",
                    "reason": str(exc)
                    if isinstance(exc, Halt)
                    else "invalid local input/runtime; no submission retry",
                    "real_broker_calls": 0,
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
