"""Packaged one-shot paper execution and strictly read-only LIVE readiness check."""

import argparse
import json
import os
import time
from pathlib import Path

from .broker import Broker
from .execution_policy import active_settings, check_run_state, load_live_config
from .model import Config, Halt, Risk, digest
from .oneshot import atomic_json, choose_entry, new_live_run, reconcile_live, run_live, run_paper
from .risk import check_state
from .schema import Contracts, structural
from .simulator import SCENARIOS
from .standalone_mcp import READ_TOOLS, ExternalOAuthToken, ReadOnlyMCP

ReadOnlyPreflightMCP = ReadOnlyMCP


def live_check(oauth_helper=None, root=None, timeout=10, settings=None):
    """No State, execution adapter, review, placement or cancellation."""
    result = {
        "status": "LIVE BLOCKED",
        "live_ready": False,
        "armed": False,
        "authenticated": False,
        "account_verified": False,
        "broker_calls": [],
        "real_review_place_cancel_calls": 0,
        "blockers": [] if settings else ["Explicit local LIVE TOML configuration required"],
        "timestamp": time.time(),
    }
    if settings:
        oauth_helper = settings.oauth_helper
    if oauth_helper is None:
        result["connectivity_blocker"] = (
            "Set --oauth-helper to the existing external noninteractive OAuth helper; credentials stay outside the package."
        )
        return result
    bridge = None
    try:
        if settings:
            settings.validate()
            check_run_state(settings)
            settings = active_settings(settings)
            oauth_helper, timeout = settings.oauth_helper, settings.timeout
            root = Path(__file__).parent
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
            config = settings.config if settings else Config(allowed_symbols=["QQQ", "IWM"])
            risk = settings.risk if settings else Risk()
            result["snapshot_attempts"] = []
            for attempt in range(2):
                try:
                    config_broker = Broker(bridge, config, risk)
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
            check_state(snapshot, risk, time.time())
            account_digest = digest(config_broker.account["account_number"])
            if settings and settings.account_sha256 and settings.account_sha256 != account_digest:
                raise Halt("authenticated account differs from configured account pin")
            if settings and (settings.directory / "live-run.json").exists():
                artifact = json.loads((settings.directory / "live-run.json").read_text())
                if artifact["policy"]["account_digest"] != account_digest:
                    raise Halt("authenticated account differs from existing owner run")
            result["account_verified"] = True
            result["account_sha256"] = account_digest
            setting = bridge.read(
                "get_trade_approval_setting",
                {"account_number": config_broker.account["account_number"]},
            )["data"]["setting"]
            if setting["account_number"] != config_broker.account["account_number"]:
                raise Halt("broker approval setting account mismatch")
            result["broker_approval_setting_verified"] = True
            if setting["human_must_approve_trades"] is not False:
                result["blockers"].append(
                    "Broker trade approvals enabled; owner configuration required"
                )
            result["equity_submission_fields"] = sorted(
                bridge.tools["place_equity_order"]["inputSchema"]["properties"]
            )
            if settings:
                from datetime import datetime
                from zoneinfo import ZoneInfo

                from .calendar import session_bounds

                bounds = session_bounds(
                    datetime.fromtimestamp(time.time(), ZoneInfo("America/New_York")).date()
                )
                if not bounds or time.time() >= bounds[1] - settings.options.get(
                    "session_buffer_seconds", 600
                ) - max(60, settings.options["polls"] * 2):
                    result["blockers"].append("Insufficient regular-session exit window")
                result["will_resume_existing_run"] = (settings.directory / "live-run.json").exists()
                if result["will_resume_existing_run"]:
                    result["blockers"].append(
                        "Existing lifecycle: inspect tradeagent status --config; use recover for unfinished exposure or new-run after verified closure"
                    )
                if not snapshot.regular_session:
                    result["blockers"].append("Regular trading session is closed")
                intent, reasons = choose_entry(
                    snapshot,
                    config,
                    risk,
                    time.time(),
                    settings.options["max_notional"],
                    settings.options.get("entry"),
                )
                result["entry_feasible"] = intent is not None
                result["entry_blockers"] = reasons if intent is None else []
                result["configured_max_notional"] = settings.options["max_notional"]
                result["configured_entry"] = settings.options.get("entry")
                result["candidate_payload"] = intent.payload() if intent else None
                result["symbol_eligibility"] = {
                    s: {
                        "tradable": snapshot.tradable.get(s),
                        "fractional_tradable": snapshot.fractional_tradable.get(s),
                        "country": snapshot.countries.get(s),
                    }
                    for s in config.allowed_symbols
                }
                if intent is None:
                    result["blockers"].extend(reasons)
                if (settings.directory / "KILL").exists():
                    result["blockers"].append("KILL prevents new exposure")
                if not result["blockers"]:
                    result.update(status="READ_ONLY_READY", live_ready=True)
            result["broker_permissions"] = (
                "read connectivity verified; submission approval behavior not exercised"
            )
    except (Halt, OSError, ValueError, KeyError, TypeError) as exc:
        result.update(status="LIVE BLOCKED", live_ready=False)
        result["connectivity_blocker"] = (
            str(exc)
            if isinstance(exc, Halt)
            else "read-only preflight failed; inspect helper permissions and current broker contracts"
        )
    finally:
        if bridge:
            result["broker_calls"] = bridge.calls
    return result


def progress(kind, payload):
    """Only observed durable transitions; human-readable stderr, JSON result on stdout."""
    import sys
    from datetime import datetime, timezone

    stage = {
        "broker_connected": "BROKER_CONNECTED",
        "broker_review": "REVIEWED",
        "exit_due": "EXIT_DUE",
    }.get(kind)
    if kind == "broker_acknowledgment":
        stage = "SUBMITTED" if payload.get("side") == "buy" else "EXIT_SUBMITTED"
    elif kind == "fill_reconciliation" and payload.get("filled") != "0":
        order = payload.get("order", {})
        if payload.get("state") == "filled":
            stage = "ENTRY_FILLED" if order.get("side") == "buy" else "EXIT_FILLED"
        elif payload.get("state") in {"partially_filled", "partially_filled_rest_cancelled"}:
            stage = "ENTRY_PARTIAL" if order.get("side") == "buy" else "EXIT_PARTIAL"
    elif kind == "one_shot_final":
        stage = (
            "RECONCILED"
            if payload.get("cash_reconciled") and payload.get("flat_bot_position")
            else "HALTED"
            if payload.get("status") == "HALTED"
            else None
        )
    if stage:
        print(datetime.now(timezone.utc).isoformat() + " " + stage, file=sys.stderr, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser(
        "live-check", help="read-only local and broker readiness; never orders"
    )
    check.add_argument("--config", type=Path)
    check.add_argument("--output", type=Path)
    fresh = commands.add_parser(
        "new-run", help="archive a broker-reconciled completed run; never orders"
    )
    fresh.add_argument("--config", type=Path, required=True)
    reconcile = commands.add_parser(
        "reconcile-once", help="read-only reconciliation of an existing owner run; never orders"
    )
    reconcile.add_argument("--config", type=Path, required=True)
    status = commands.add_parser(
        "status", help="read-only selected-account execution and reconciliation status"
    )
    status.add_argument("--config", type=Path, required=True)
    recover = commands.add_parser(
        "recover", help="owner-operated existing lifecycle recovery; never a new entry"
    )
    recover.add_argument("--config", type=Path, required=True)
    once = commands.add_parser("run-once", help="one owner-launched entry and automatic exit")
    modes = once.add_mutually_exclusive_group(required=True)
    modes.add_argument("--paper", action="store_true")
    modes.add_argument(
        "--live",
        action="store_true",
        help="real one-shot; local LIVE config and broker permissions required",
    )
    once.add_argument("--config", type=Path)
    once.add_argument("--state-dir", type=Path)
    once.add_argument("--max-notional")
    once.add_argument("--hold-seconds", type=int)
    once.add_argument("--polls", type=int)
    once.add_argument("--entry-scenario", choices=sorted(SCENARIOS))
    once.add_argument("--exit-scenario", choices=sorted(SCENARIOS))
    once.add_argument("--kill-switch", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "live-check":
            if not args.config:
                raise Halt("live-check requires --config owner TOML")
            result = live_check(settings=load_live_config(args.config))
            if args.output:
                os.umask(0o077)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                atomic_json(args.output, result)
            code = 0 if result["live_ready"] else 2
        elif args.command == "status":
            result = reconcile_live(load_live_config(args.config), persist=False)
            code = (
                0
                if result["reconciliation_status"] in {"RECONCILED", "OPEN_ORDERS", "NO_LIFECYCLE"}
                else 2
            )
        elif args.command == "recover":
            result = run_live(load_live_config(args.config), recover=True, observer=progress)
            code = (
                0
                if result["status"] in {"COMPLETED", "CLOSED_PARTIAL", "NO_TRADE"}
                or not result.get("outstanding_incident", True)
                else 2
            )
        elif args.command == "reconcile-once":
            result = reconcile_live(load_live_config(args.config))
            code = 0 if result["reconciliation_status"] == "RECONCILED" else 2
        elif args.command == "new-run":
            result = new_live_run(load_live_config(args.config))
            code = 0
        elif args.live:
            if not args.config:
                raise Halt("LIVE requires --config with explicit live.enabled = true")
            if any(
                getattr(args, key) is not None
                for key in (
                    "state_dir",
                    "max_notional",
                    "hold_seconds",
                    "polls",
                    "entry_scenario",
                    "exit_scenario",
                    "kill_switch",
                )
            ):
                raise Halt("LIVE policy comes only from TOML; paper overrides are not permitted")
            result = run_live(load_live_config(args.config), observer=progress)
            code = 0 if result["status"] in {"COMPLETED", "CLOSED_PARTIAL", "NO_TRADE"} else 2
        else:
            if args.config:
                raise Halt("owner LIVE TOML is not a paper configuration")
            result = run_paper(
                args.state_dir or Path("work/one-shot-paper"),
                args.max_notional if args.max_notional is not None else "25",
                args.hold_seconds if args.hold_seconds is not None else 3600,
                args.polls if args.polls is not None else 3,
                args.entry_scenario or "full_fill",
                args.exit_scenario or "full_fill",
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
