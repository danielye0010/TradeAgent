"""Isolated weekly-hold-v1 SHADOW CLI; never reads owner configuration."""

import argparse
import json
import time
from pathlib import Path

from .engine import Engine, guard
from .market import Market
from .protocol import CONFIG_HASH, activation_week, day_at
from .reporting import write_reports
from .store import Store


def default_state():
    return Path.home() / ".local/state/tradeagent/weekly-hold-v1"


def main(argv=None):
    parser = argparse.ArgumentParser(description="weekly-hold-v1 SHADOW only; no broker writes")
    parser.add_argument(
        "command",
        choices=[
            "init",
            "history",
            "packet",
            "decision",
            "fill",
            "risk",
            "report",
            "prepare",
            "backtest",
            "publish",
            "serve",
        ],
    )
    parser.add_argument("--state-dir", type=Path, default=default_state())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--start-date")
    parser.add_argument("--remote", default="https://github.com/danielye0010/TradeAgent.git")
    parser.add_argument("--port", type=int, default=8643)
    args = parser.parse_args(argv)
    try:
        if args.command == "backtest":
            from .backtest import run

            print(json.dumps(run(args.output or Path("work/weekly-backtest"))))
            return 0
        if args.command == "publish":
            from .web import publish

            print(json.dumps(publish(args.state_dir, args.remote)))
            return 0
        if args.command == "serve":
            from .web import serve

            serve(args.state_dir, port=args.port)
            return 0
        prepare = args.command == "prepare"
        directory = args.output if prepare else args.state_dir
        if prepare and directory is None:
            raise ValueError("prepare requires independent --output directory")
        evidence = "preflight" if prepare else "prospective"
        start = args.start_date or str(activation_week(day_at(time.time())))
        with Store(directory, start_date=start, evidence=evidence) as store:
            market = Market(store.directory / "market-cache")
            engine = Engine(store, market)
            if args.command in {"decision", "packet", "fill", "risk", "prepare"}:
                try:
                    guard()
                except Exception:
                    from .protocol import week_key

                    store.add(
                        "priority-delay:" + args.command + ":" + str(int(time.time() // 60)),
                        "delay",
                        {
                            "book": "all-active",
                            "week": week_key(day_at(time.time())),
                            "date": str(day_at(time.time())),
                            "phase": args.command,
                            "reason": "existing shortline service has priority",
                        },
                    )
                    raise
            if prepare:
                from ..prospective.claude_assessor import login
                from ..prospective.event_assessor import subscription_status

                subscription_status()
                login()
                packet = engine.packet(prepare=True)
                result = engine.decision(prepare=True, packet=packet)
                valid = all(result["arms"][a]["status"] == "VALID" for a in ("codex", "claude"))
                quotes = sum(bool(f["quote"]) for f in packet["price_features"].values())
                result.update(
                    status="PASS" if valid and quotes else "FAIL",
                    quote_count=quotes,
                    config_hash=CONFIG_HASH,
                    prospective_records_written=0,
                )
                (store.directory / "prepare-result.json").write_text(json.dumps(result, indent=2))
                print(
                    json.dumps(
                        {
                            "status": result["status"],
                            "quote_count": quotes,
                            "models": {
                                a: {
                                    "status": result["arms"][a]["status"],
                                    "reason": result["arms"][a].get("reason"),
                                    "searches": (result["arms"][a].get("provenance") or {}).get(
                                        "web_searches"
                                    ),
                                }
                                for a in ("codex", "claude")
                            },
                            "prospective_records_written": 0,
                        }
                    )
                )
                return 0 if result["status"] == "PASS" else 2
            if args.command == "history":
                engine.history()
            elif args.command == "packet":
                engine.packet()
            elif args.command == "decision":
                engine.decision()
            elif args.command == "fill":
                engine.fill()
            elif args.command == "risk":
                engine.risk()
            elif args.command == "report":
                engine.valuation()
            data = write_reports(store)
            print(
                json.dumps(
                    {
                        "status": data["status"],
                        "protocol": data["protocol"],
                        "start_date": data["start_date"],
                        "real_orders_submitted": 0,
                    }
                )
            )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "UNAVAILABLE",
                    "error": type(error).__name__ + ": " + str(error)[:300],
                    "real_orders_submitted": 0,
                }
            )
        )
        return 2
