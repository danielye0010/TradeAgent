"""One-shot prediction lab. Live execution is a separate legacy library boundary."""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from .research.domain import MarketSnapshot
from .research.evolution import evolve_weekly, retire
from .research.feedback import import_feedback
from .research.lab import scan, seed
from .research.learning import learn_daily, retire_lesson
from .research.outcomes import resolve
from .research.store import Experience


def main(argv=None):
    supplied = list(sys.argv[1:] if argv is None else argv)
    if supplied and supplied[0] in {"live-check", "run-once", "new-run"}:
        from .oneshot_cli import main as oneshot_main

        return oneshot_main(supplied)
    if supplied and supplied[0] == "canary-review":
        from .canary_review_cli import main as review_main

        return review_main(supplied[1:])
    if supplied and (
        supplied[0] in {"validate", "tools", "shadow", "simulate", "execution"}
        or (supplied[0] == "inspect" and "--config" in supplied)
    ):
        from .legacy.cli import main as execution_main

        return execution_main(supplied[1:] if supplied[0] == "execution" else supplied)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "init",
            "scan",
            "resolve",
            "learn-daily",
            "evolve-weekly",
            "inspect",
            "demo",
            "retire",
            "retire-lesson",
            "import-execution",
            "replay-history",
            "live-check",
            "run-once",
        ],
    )
    parser.add_argument("--state-dir", type=Path, default=Path("data/research"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--demo-dir", type=Path, default=Path("data/rsi-demo"))
    parser.add_argument(
        "--evidence-kind", choices=["prospective", "synthetic"], default="prospective"
    )
    parser.add_argument("--max-selected", type=int, default=3)
    parser.add_argument("--strategy-id")
    parser.add_argument("--version")
    parser.add_argument("--reason")
    parser.add_argument("--lesson-id")
    parser.add_argument("--sessions", type=int, default=60)
    parser.add_argument("--symbols", default="QQQ,IWM")
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--horizon", choices=["60m"], default="60m")
    parser.add_argument("--output", type=Path, default=Path("work/commissioning"))
    parser.add_argument("--as-of", help="run date in New York; excludes this session")
    args = parser.parse_args(supplied)
    store = None
    try:
        if args.command == "replay-history":
            from .research.replay import run_history

            result = run_history(
                args.output,
                args.sessions,
                args.symbols.split(","),
                args.benchmark,
                args.as_of,
                args.input,
            )
        elif args.command == "demo":
            from .research.demo import run_demo

            result = run_demo(args.demo_dir)
        else:
            store = Experience(args.state_dir)
            with store.lock():
                now = time.time()
                if args.command == "init":
                    seed(store, now)
                    result = store.inspect()
                elif args.command in {"scan", "resolve"}:
                    if args.input is None:
                        raise ValueError(
                            "scan/resolve requires --input with timestamped market data"
                        )
                    dataset = json.loads(args.input.read_text())
                    if args.command == "scan":
                        result = scan(
                            store, MarketSnapshot.from_dict(dataset), now, args.max_selected
                        )
                    else:
                        result = resolve(store, dataset, now)
                elif args.command == "learn-daily":
                    result = learn_daily(store, now, args.evidence_kind)
                elif args.command == "evolve-weekly":
                    proposal = json.loads(args.proposal.read_text()) if args.proposal else None
                    result = evolve_weekly(store, now, proposal, args.evidence_kind)
                elif args.command == "retire":
                    retire(store, args.strategy_id, args.version, now, args.reason)
                    result = {"retired": args.strategy_id, "version": args.version}
                elif args.command == "retire-lesson":
                    retire_lesson(store, args.lesson_id, now, args.reason)
                    result = {"retired_lesson": args.lesson_id}
                elif args.command == "import-execution":
                    if args.input is None:
                        raise ValueError("import-execution requires --input")
                    result = import_feedback(store, json.loads(args.input.read_text()), now)
                else:
                    result = store.inspect()
        print(json.dumps(result, indent=2, default=str, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({"status": "halted", "reason": str(exc)}), file=sys.stderr)
        return 2
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
