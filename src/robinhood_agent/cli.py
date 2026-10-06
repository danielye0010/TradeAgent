"""One-shot CLI. SHADOW default; autonomous writes require a pinned signed policy."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .broker import Broker
from .codex_bridge import CodexBridge
from .model import Halt, load_config
from .runner import cycle
from .state import State


def main(argv=None):
    supplied = list(sys.argv[1:] if argv is None else argv)
    if supplied and supplied[0] in {"canary-buy", "canary-exit", "run-once"}:
        from .standalone_cli import main as standalone_main

        return standalone_main(supplied)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "tools", "shadow", "inspect", "simulate"])
    parser.add_argument("--config", type=Path, default=Path("config/config.example.json"))
    parser.add_argument("--risk", type=Path, default=Path("config/risk.example.json"))
    parser.add_argument("--demo-dir", type=Path)
    parser.add_argument("--read-evidence", type=Path)
    args = parser.parse_args(argv)
    state = None
    try:
        config, risk = load_config(args.config, args.risk)
        if args.command == "validate":
            print(
                json.dumps(
                    {
                        "valid": True,
                        "mode": config.mode,
                        "production_broker": "read-only",
                        "scheduler": "absent",
                    }
                )
            )
            return 0
        if config.mode != "SHADOW":
            raise Halt("CLI permits SHADOW only in this release")
        if args.command == "simulate":
            from .demo import run_demo

            directory = args.demo_dir or Path(config.state_dir) / "simulations" / (
                "simulation-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            )
            result = run_demo(directory, args.read_evidence)
            print(json.dumps(result, indent=2, default=str, allow_nan=False))
            return 0
        state = State(Path(config.state_dir))
        if args.command == "inspect":
            report = {
                table: [dict(row) for row in state.db.execute(f"SELECT * FROM {table}")]
                for table in ("runs", "intents", "baseline")
            }
            report["integrity"] = state.db.execute("PRAGMA integrity_check").fetchone()[0]
            print(json.dumps(report, indent=2))
            return 0
        with CodexBridge(Path.cwd(), config.request_timeout_seconds) as bridge:
            if args.command == "tools":
                print(
                    json.dumps(
                        {"auth": bridge.auth_status, "effective_tools": sorted(bridge.tools)}
                    )
                )
                return 0
            result = cycle(Broker(bridge, config, risk), state, config, risk)
            print(json.dumps(result, indent=2, default=str, allow_nan=False))
            return {"completed": 0, "halted": 2, "failed": 1}[result["status"]]
    except (Halt, OSError) as exc:
        print(json.dumps({"status": "halted", "reason": str(exc)}), file=sys.stderr)
        return 2
    finally:
        if state:
            state.close()


if __name__ == "__main__":
    raise SystemExit(main())
