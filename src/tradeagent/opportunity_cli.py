"""Manual opportunity research and an explicit handoff to the established owner engine."""

import argparse
import json
import time
from pathlib import Path

from .model import Halt
from .research.opportunities import UNIVERSE, capture, scan
from .research.opportunity_workflow import DailyResearch, selected_execution


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    from .oneshot import atomic_json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path("data/opportunities"))
    commands = parser.add_subparsers(dest="command", required=True)
    scanner = commands.add_parser("scan", help="market-only candidate discovery, never orders")
    scanner.add_argument("--symbols", nargs="+", default=UNIVERSE)
    scanner.add_argument("--provider", choices=["robinhood", "alpaca"], default="robinhood")
    scanner.add_argument("--oauth-helper", type=Path)
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
    commands.add_parser("compare", help="paired Quant/Codex/combined performance by evidence pool")
    feedback = commands.add_parser(
        "feedback", help="link an existing reconciled owner execution report"
    )
    feedback.add_argument("--decision-id", required=True)
    feedback.add_argument("--input", type=Path, required=True)
    feedback.add_argument("--mode", choices=["quant_only", "quant_codex"], default="quant_codex")
    for name in ("handoff", "execute"):
        command = commands.add_parser(
            name,
            help="fresh read-only sizing"
            if name == "handoff"
            else "explicit owner execution using existing LIVE engine",
        )
        command.add_argument("--decision-id")
        command.add_argument("--mode", choices=["quant_only", "quant_codex"], default="quant_codex")
        command.add_argument("--config", type=Path, required=True)
        if name == "execute":
            command.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args(argv)
    store = None
    try:
        store = DailyResearch(args.state_dir)
        with store.experience.lock():
            now = time.time()
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
                        "candidates": [],
                        "candidate_pool": [],
                        "universe": args.symbols,
                        "limitations": [str(error)],
                        "orders_submitted": 0,
                    }
                    from .research.domain import identity

                    report["scan_id"] = identity(report)
                if dataset:
                    market = args.state_dir / "captures" / (report["scan_id"] + ".json")
                    write(market, dataset)
                    report["market_file"] = str(market.resolve())
                store.save("scans", report, report["scan_id"])
                write(args.state_dir / "latest-candidates.json", report)
                result = report
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
                items = read(args.input)
                items = (
                    items["assessments"]
                    if isinstance(items, dict) and "assessments" in items
                    else [items]
                )
                # Each frozen assessment is idempotent; retries preserve already saved evidence.
                with store.db:
                    ids = [store.assessment(item, now) for item in items]
                result = {"saved_assessments": ids, "orders_submitted": 0}
            elif args.command == "decide":
                report = store.scan(args.scan_id)
                dataset = (
                    read(args.input)
                    if args.input
                    else capture(report["universe"], args.provider, args.oauth_helper)
                    if args.refresh
                    else read(report["market_file"])
                    if "market_file" in report
                    else {
                        "source": "unavailable",
                        "evidence_kind": "historical_market",
                        "observed_at": now,
                        "bars": [],
                        "quotes": {},
                    }
                )
                result = store.decide(
                    report,
                    dataset,
                    now=time.time(),
                    holding_seconds=args.hold_seconds,
                    delay_seconds=args.delay_seconds,
                )
                write(args.state_dir / "decisions" / (result["decision_id"] + ".json"), result)
                write(args.state_dir / "latest-plan.json", result)
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
                selected = decision["comparisons"][args.mode]["plan"]
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
                    "recorded_at": now,
                    "report": report,
                    "verification": "owner-imported existing reconciled execution report; not a fresh broker query",
                }
                result = {"execution_record": store.save("executions", item), "orders_submitted": 0}
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
                else:
                    from .oneshot import run_live
                    from .oneshot_cli import progress

                    result = run_live(settings, plan=plan, prediction=prediction, observer=progress)
            print(json.dumps(result, indent=2, default=str, allow_nan=False))
            return 2 if result.get("status") == "INCOMPLETE" else 0
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
