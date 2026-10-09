"""Manual opportunity research and an explicit handoff to the established owner engine."""

import argparse
import json
import time
from pathlib import Path

from .model import Halt
from .research.opportunities import UNIVERSE, capture, scan
from .research.opportunity_workflow import DailyResearch, scan_failure, selected_execution
from .research.tradeplan import EVIDENCE_GATED, EXPERIMENTAL


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    from .oneshot import atomic_json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, value)


def saved_scan_failure(directory, report):
    failure = scan_failure(report)
    if failure is None:
        return None
    path = Path(directory) / "invocations" / (failure["invocation_id"] + ".json")
    failure["invocation_file"] = str(path.resolve())
    # Retries and legacy failed scans keep the same failure identity and evidence.
    if path.exists():
        if read(path) != failure:
            raise ValueError("saved scan failure evidence differs; refusing to overwrite")
    else:
        write(path, failure)
    return failure


def align_live_universe(report, config_path):
    """Local allowlist only; no broker calls and no changes to owner configuration."""
    if config_path is None:
        return report
    from .execution_policy import load_live_config
    from .research.domain import identity

    allowed = load_live_config(config_path).config.allowed_symbols
    report = dict(report)
    report["research_candidates"] = report["candidates"]
    report["execution_universe"] = list(allowed)
    report["research_universe"] = report["universe"]
    opportunities = sorted(
        (c for c in report["candidate_pool"] if c["status"] == "CANDIDATE"),
        key=lambda c: (-c["ranking_score"], c["symbol"]),
    )
    report["candidates"] = [c for c in opportunities if c["symbol"] in allowed][
        : report["candidate_count"]
    ]
    report["excluded_live_opportunities"] = [
        {
            "symbol": c["symbol"],
            "ranking_score": c["ranking_score"],
            "reason": "not authorized by existing owner allowlist",
        }
        for c in opportunities
        if c["symbol"] not in allowed
    ]
    report["scan_id"] = identity(report)
    return report


def auto_resolve(store, dataset, args):
    """Resolve on each invocation's reads, including older genuine session paths."""
    from datetime import date

    from .research.domain import identity, iso

    now = time.time()
    resolution = store.resolve(dataset, now)
    summary = {
        "resolved_decisions": len(resolution["resolved"]),
        "resolved_symbols": len(resolution["symbol_resolved"]),
        "limitations": [],
        "terminal_unresolvable": resolution["terminal_unresolvable"],
    }
    # Explicit input fixtures never cause implicit network access. Real captures
    # request only past sessions needed by persisted prospective forecasts.
    if not args.input:
        current_day = iso(dataset["session_open"])[:10]
        for request in resolution["pending_sessions"]:
            if request["source"] != dataset["source"] or request["session_date"] == current_day:
                continue
            try:
                later = capture(
                    request["symbols"],
                    args.provider,
                    args.oauth_helper,
                    session=date.fromisoformat(request["session_date"]),
                )
                path = args.state_dir / "captures" / (identity(later) + ".json")
                write(path, later)
                result = store.resolve(later, time.time())
                summary["resolved_decisions"] += len(result["resolved"])
                summary["resolved_symbols"] += len(result["symbol_resolved"])
            except (Halt, ValueError, OSError, KeyError, TypeError) as error:
                summary["limitations"].append({**request, "reason": str(error)})
    summary["pending_sessions"] = store.pending_sessions(time.time())
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path("data/opportunities"))
    commands = parser.add_subparsers(dest="command", required=True)
    scanner = commands.add_parser("scan", help="market-only candidate discovery, never orders")
    scanner.add_argument("--symbols", nargs="+", default=UNIVERSE)
    scanner.add_argument("--provider", choices=["robinhood", "alpaca"], default="robinhood")
    scanner.add_argument("--oauth-helper", type=Path)
    scanner.add_argument(
        "--config", type=Path, help="existing owner allowlist for LIVE research selection only"
    )
    scanner.add_argument("--input", type=Path, help="explicit timestamped captured market evidence")
    scanner.add_argument("--window-minutes", type=int, default=60)
    scanner.add_argument("--candidates", type=int, default=5)
    scanner.add_argument(
        "--events", type=Path, help="verified event evidence, independent of price ranking"
    )
    evidence = commands.add_parser(
        "evidence", help="show scanner candidates or assessment template"
    )
    evidence.add_argument("--scan-id")
    evidence.add_argument("--template", action="store_true")
    assess = commands.add_parser("assess", help="freeze interactive Codex research JSON")
    assess.add_argument("--input", type=Path, required=True)
    decide = commands.add_parser(
        "decide", help="quantitative evaluation and frozen paired proposals"
    )
    decide.add_argument("--scan-id")
    decide.add_argument(
        "--policy",
        choices=["evidence-gated", "experimental"],
        default="evidence-gated",
        help="experimental requires an explicit owner request; never a LIVE fallback",
    )
    decide.add_argument(
        "--config", type=Path, help="revalidate the frozen LIVE research allowlist locally"
    )
    decide.add_argument(
        "--input", type=Path, help="fresh capture for the same frozen candidate pool"
    )
    decide.add_argument("--refresh", action="store_true")
    decide.add_argument("--provider", choices=["robinhood", "alpaca"], default="robinhood")
    decide.add_argument("--oauth-helper", type=Path)
    decide.add_argument("--hold-seconds", type=int, default=3600)
    decide.add_argument("--delay-seconds", type=int, default=300)
    show = commands.add_parser(
        "show", help="latest plan, current validity and compact research status"
    )
    show.add_argument("--decision-id")
    resolve = commands.add_parser("resolve", help="resolve against later actual observations")
    resolve.add_argument("--input", type=Path, required=True)
    commands.add_parser(
        "resolution-status",
        help="append terminal impossible-window evidence without market/broker reads",
    )
    commands.add_parser("compare", help="paired Quant/Codex/combined performance by evidence pool")
    feedback = commands.add_parser(
        "feedback", help="link an existing reconciled owner execution report"
    )
    feedback.add_argument("--decision-id", required=True)
    feedback.add_argument("--input", type=Path, required=True)
    feedback.add_argument(
        "--mode", choices=["quant_only", "quant_codex", "experimental"], default="quant_codex"
    )
    for name in ("handoff", "execute"):
        command = commands.add_parser(
            name,
            help="fresh read-only sizing"
            if name == "handoff"
            else "explicit owner execution using existing LIVE engine",
        )
        command.add_argument("--decision-id")
        command.add_argument(
            "--mode", choices=["quant_only", "quant_codex", "experimental"], default="quant_codex"
        )
        command.add_argument("--config", type=Path, required=True)
        if name == "execute":
            command.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args(argv)
    store = None
    try:
        store = DailyResearch(args.state_dir)
        with store.experience.lock():
            now = time.time()
            if args.command in {"evidence", "decide"} or (
                args.command == "show" and not args.decision_id and store.records("scans")
            ):
                report = store.scan(getattr(args, "scan_id", None))
                failure = saved_scan_failure(args.state_dir, report)
                if failure:
                    print(json.dumps(failure, indent=2, allow_nan=False))
                    return 2
            if args.command == "scan":
                try:
                    dataset = (
                        read(args.input)
                        if args.input
                        else capture(args.symbols, args.provider, args.oauth_helper)
                    )
                    report = scan(
                        dataset,
                        now=time.time(),
                        window_minutes=args.window_minutes,
                        candidate_count=args.candidates,
                        events=read(args.events) if args.events else [],
                    )
                except (Halt, ValueError, OSError, KeyError, TypeError) as error:
                    dataset = None
                    report = {
                        "schema_version": 1,
                        "decision_time": time.time(),
                        "status": "INCOMPLETE",
                        "source": None,
                        "evidence_kind": "unavailable",
                        "requested_provider": args.provider,
                        "candidates": [],
                        "candidate_pool": [],
                        "universe": args.symbols,
                        "limitations": [str(error)],
                        "orders_submitted": 0,
                    }
                    from .research.domain import identity

                    report["scan_id"] = identity(report)
                if dataset:
                    report = align_live_universe(report, args.config)
                    report["automatic_resolution"] = auto_resolve(store, dataset, args)
                    from .research.domain import identity

                    report["scan_id"] = identity(
                        {k: v for k, v in report.items() if k != "scan_id"}
                    )
                    market = args.state_dir / "captures" / (report["scan_id"] + ".json")
                    write(market, dataset)
                    report["market_file"] = str(market.resolve())
                store.save("scans", report, report["scan_id"])
                write(args.state_dir / "latest-candidates.json", report)
                result = saved_scan_failure(args.state_dir, report) or report
            elif args.command == "evidence":
                report = store.scan(args.scan_id)
                result = (
                    report
                    if not args.template
                    else {
                        "scan_id": report["scan_id"],
                        "assessments": [
                            {
                                "scan_id": report["scan_id"],
                                "symbol": c["symbol"],
                                "observed_at": now,
                                "stance": "watch",
                                "hypothesis_type": "event_driven"
                                if set(c["categories"]) == {"verified_event"}
                                else "price_action",
                                "rank": i + 1,
                                "thesis": "",
                                "catalyst": "",
                                "priced_in": "",
                                "contradictions": [],
                                "invalidation": "",
                                "sources": [],
                                "model": "record actual Codex model if exposed, otherwise not-exposed",
                            }
                            for i, c in enumerate(report["candidates"][:3])
                        ],
                    }
                )
            elif args.command == "assess":
                document = read(args.input)
                items = document
                items = (
                    items["assessments"]
                    if isinstance(items, dict) and "assessments" in items
                    else [items]
                )
                assessment_scan = (
                    document.get("scan_id") if isinstance(document, dict) else None
                ) or (items[0].get("scan_id") if items else None)
                failure = saved_scan_failure(args.state_dir, store.scan(assessment_scan))
                if failure:
                    print(json.dumps(failure, indent=2, allow_nan=False))
                    return 2
                # Each frozen assessment is idempotent; retries preserve already saved evidence.
                with store.db:
                    ids = [store.assessment(item, now) for item in items]
                result = {"saved_assessments": ids, "orders_submitted": 0}
            elif args.command == "decide":
                report = store.scan(args.scan_id)
                if args.config:
                    from .execution_policy import load_live_config

                    if set(load_live_config(args.config).config.allowed_symbols) != set(
                        report.get("execution_universe") or []
                    ):
                        raise ValueError(
                            "owner allowlist changed or scan was not LIVE-aligned; start a new aligned scan"
                        )
                elif report.get("execution_universe") is not None:
                    raise ValueError(
                        "LIVE-aligned decide requires the same existing owner --config"
                    )
                dataset = (
                    read(args.input)
                    if args.input
                    else capture(report["universe"], args.provider, args.oauth_helper)
                    if args.refresh
                    else read(report["market_file"])
                    if "market_file" in report
                    else None
                )
                if dataset is None:
                    raise ValueError("saved market capture unavailable; run a new scan")
                resolution = auto_resolve(store, dataset, args)
                # Retain the actual refreshed capture used to freeze all forecasts.
                from .research.domain import identity

                write(args.state_dir / "captures" / (identity(dataset) + ".json"), dataset)
                result = store.decide(
                    report,
                    dataset,
                    now=time.time(),
                    holding_seconds=args.hold_seconds,
                    delay_seconds=args.delay_seconds,
                    execution_policy=EXPERIMENTAL
                    if args.policy == "experimental"
                    else EVIDENCE_GATED,
                )
                write(args.state_dir / "decisions" / (result["decision_id"] + ".json"), result)
                write(args.state_dir / "latest-plan.json", result)
                write(
                    args.state_dir / "resolutions" / (result["decision_id"] + ".json"), resolution
                )
            elif args.command == "show":
                result = store.decision(args.decision_id)
                result["current_status"] = (
                    "NO_TRADE"
                    if not result["selected_plan"]
                    else "EXPIRED"
                    if now >= result["entry_deadline"]
                    else "RESEARCH_ONLY"
                    if result["selected_plan"]["research_only"]
                    else "AWAITING_ENTRY_WINDOW"
                    if now < result["entry_after"]
                    else "AWAITING_FRESH_EXECUTION_CHECKS"
                )
                result["follow_up"] = store.performance()
            elif args.command == "resolve":
                result = store.resolve(read(args.input), now)
            elif args.command == "resolution-status":
                result = {
                    "terminal_unresolvable": store.terminal_windows(now),
                    "pending_sessions": store.pending_sessions(now),
                    "orders_submitted": 0,
                }
            elif args.command == "compare":
                result = store.performance()
            elif args.command == "feedback":
                decision = store.decision(args.decision_id)
                report = read(args.input)
                if (
                    report.get("mode") != "LIVE"
                    or report.get("submission_status") != "BROKER_CONFIRMED"
                    or not report.get("cash_reconciled")
                    or not report.get("flat_bot_position")
                ):
                    raise ValueError(
                        "feedback requires an existing broker-confirmed reconciled LIVE report"
                    )
                selected_plan, _ = selected_execution(decision, args.mode)
                from .research.tradeplan import plan_dict

                selected = plan_dict(selected_plan)
                if not selected:
                    raise ValueError("selected research arm has no executable plan")
                if not any(
                    (event.get("provenance") or {}).get("prediction_id")
                    == selected["decision"]["prediction_id"]
                    for event in [report.get("decision", {})]
                ):
                    raise ValueError(
                        "execution report does not link this frozen research prediction"
                    )
                item = {
                    "decision_id": args.decision_id,
                    "research_arm": args.mode,
                    "execution_policy": decision.get("execution_policy", EVIDENCE_GATED),
                    "recorded_at": now,
                    "report": report,
                    "verification": "owner-imported existing reconciled execution report; not a fresh broker query",
                }
                result = {"execution_record": store.save("executions", item), "orders_submitted": 0}
            elif args.command == "execute":
                from .opportunity_live import execute_opportunity

                result = execute_opportunity(
                    store, args.decision_id, args.config, live=args.live, mode=args.mode
                )
            else:
                from .execution_policy import active_settings, load_live_config
                from .research.tradeplan import execution_handoff, validate_execution_plan

                plan, prediction = selected_execution(store.decision(args.decision_id), args.mode)
                validate_execution_plan(
                    plan, now
                )  # Reject unavailable/expired proposals before authentication.
                settings = active_settings(load_live_config(args.config))
                if args.command == "handoff":
                    from .broker import Broker
                    from .standalone_mcp import ExternalOAuthToken, ReadOnlyMCP

                    with ReadOnlyMCP(
                        ExternalOAuthToken(settings.oauth_helper, Path(__file__).parent),
                        settings.timeout,
                    ) as bridge:
                        snapshot = Broker(bridge, settings.config, settings.risk).snapshot()
                        result = execution_handoff(
                            plan,
                            prediction,
                            snapshot,
                            settings.config,
                            settings.risk,
                            time.time(),
                            settings.options["max_notional"],
                            settings.options["entry"],
                        )
            print(json.dumps(result, indent=2, default=str, allow_nan=False))
            return 2 if result.get("status") in {"INCOMPLETE", "HALTED", "RECOVERY_REQUIRED"} else 0
    except (Halt, OSError, ValueError, KeyError, TypeError) as error:
        print(
            json.dumps(
                {
                    "status": "INCOMPLETE",
                    "decision": "NO_TRADE",
                    "reason": str(error),
                    "orders_submitted": 0,
                }
            )
        )
        return 2
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
