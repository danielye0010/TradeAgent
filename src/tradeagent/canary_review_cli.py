"""Explicit one-shot review action; no grant, execution transport or order methods."""

import argparse
import json
import time
from pathlib import Path

from .broker import Broker
from .canary_review import CanaryReviewBridge, live_rsi_snapshots, run_review
from .legacy.standalone_reference import PublicReferenceReader
from .model import Halt, load_config
from .research.lab import seed
from .research.store import Experience
from .standalone_mcp import ExternalOAuthToken
from .state import State


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--oauth-helper", type=Path, required=True)
    parser.add_argument("--account-digest", required=True)
    parser.add_argument("--research-state", type=Path, default=Path("data/research"))
    parser.add_argument("--output", type=Path, required=True, help="new private Linux directory")
    args = parser.parse_args(argv)
    state = store = bridge = None
    result = {"status": "HALT", "broker_counts": {"reads": 0, "review": 0, "place": 0, "cancel": 0}}
    try:
        if args.output.exists():
            raise Halt("review output already exists; do not repeat this attempt")
        if len(args.account_digest) != 64 or any(
            c not in "0123456789abcdef" for c in args.account_digest
        ):
            raise Halt("explicit dedicated-account digest required")
        root = args.root.resolve()
        if Path(__file__).resolve().parent != root / "src/tradeagent":
            raise Halt("review package differs from frozen deployment root")
        config, risk = load_config(
            root / "config/canary.example.json", root / "config/risk.example.json"
        )
        tokens = ExternalOAuthToken(args.oauth_helper, root)
        state = State(args.output)
        store = Experience(args.research_state)
        with store.lock():
            if not store.db.execute("SELECT 1 FROM strategy_versions LIMIT 1").fetchone():
                seed(store, time.time())  # Never backdate registration or reset existing RSI state.
        with CanaryReviewBridge(root, tokens, config.request_timeout_seconds) as bridge:
            references = PublicReferenceReader(bridge, config)

            def collect():
                return live_rsi_snapshots(
                    bridge,
                    config.allowed_symbols,
                    recorder=lambda value: state.event("metadata", "live_RSI_market", value),
                )

            result = run_review(
                bridge,
                Broker(bridge, config, risk),
                state,
                store,
                config,
                risk,
                root,
                args.account_digest,
                collect,
                references,
            )
        if result.get("review_expires", float("inf")) <= time.time():
            result.update(status="HALT", blocker="review expired; no automatic re-review")
        result["sqlite_integrity"] = state.db.execute("PRAGMA integrity_check").fetchone()[0]
        result["intents"] = state.db.execute("SELECT COUNT(*) FROM intents").fetchone()[0]
        result["research_integrity"] = store.db.execute("PRAGMA integrity_check").fetchone()[0]
    except (Halt, ValueError, OSError, KeyError, TypeError) as exc:
        result.update(
            status="HALT", blocker=str(exc) if isinstance(exc, Halt) else type(exc).__name__
        )
        if bridge:
            result["broker_counts"] = {
                "reads": sum(n != "review_equity_order" for n in bridge.wire_calls),
                "review": bridge.wire_calls.count("review_equity_order"),
                "place": bridge.wire_calls.count("place_equity_order"),
                "cancel": bridge.wire_calls.count("cancel_equity_order"),
            }
    finally:
        if state:
            state.export_log()
            (state.directory / "result.json").write_text(
                json.dumps(result, indent=2, default=str) + "\n"
            )
            state.close()
        if store:
            store.close()
    print(json.dumps(result, indent=2, default=str, allow_nan=False))
    return 0 if result["status"] in {"NO_TRADE", "READY_FOR_USER_CONFIRMED_MANUAL_CANARY"} else 2
