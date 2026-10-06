"""Prepared manual supervision entry point; NOT installed and blocked before any I/O."""

import argparse
import json
from pathlib import Path

from .broker import Broker
from .codex_bridge import CodexBridge
from .model import Halt, Intent, dec, load_config
from .options import OptionIntent, OptionsReader
from .schema import Contracts
from .state import State
from .supervised import (
    HumanApproval,
    NativeExecutionTransport,
    OfficialExecutionAdapter,
    SupervisedLifecycle,
)


def require_cli_release():
    # Independent from release.require_real_release and native transport. Future
    # authorization must explicitly release BOTH barriers; there is no config flag.
    raise Halt("CLI milestone gate: real SUPERVISED execution has not been released")


def read_intent(path, broker):
    raw = json.loads(Path(path).read_text())
    allowed = {"asset", "symbol", "contract_id", "side", "quantity", "limit_price", "effect"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise Halt("invalid structured intent file")
    if raw.get("asset", "equity") == "option":
        q = OptionsReader(broker).contract_quote(raw["contract_id"], broker.snapshot().asof)
        return OptionIntent(
            q.contract,
            raw["side"],
            dec(raw["quantity"]),
            dec(raw["limit_price"]),
            raw.get("effect", "open"),
        )
    if raw.get("asset", "equity") not in {"equity", "etf"}:
        raise Halt("unsupported asset")
    return Intent(
        raw["symbol"],
        raw["side"],
        dec(raw["quantity"]),
        dec(raw["limit_price"]),
        raw.get("asset", "equity"),
    )


def main(argv=None):
    require_cli_release()  # Before configuration, state files, auth or broker connection.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["review", "submit", "reconcile", "cancel"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--risk", type=Path, required=True)
    parser.add_argument("--intent", type=Path, required=True)
    parser.add_argument("--signal-bar", default="")
    parser.add_argument("--plan-key")
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--public-key", type=Path)
    args = parser.parse_args(argv)
    c, r = load_config(args.config, args.risk)
    if c.mode != "SUPERVISED" or not c.supervised_enabled:
        raise Halt("explicit SUPERVISED configuration required")
    state = State(Path(c.state_dir))
    try:
        with state.lock(c.lease_seconds) as fence:
            run = state.start(c, r)
            try:
                with CodexBridge(
                    Path.cwd(), c.request_timeout_seconds, tuple(Contracts().tools)
                ) as bridge:
                    contracts = Contracts()
                    contracts.check_current(
                        bridge.tools, bridge.inventory["data"][0]["serverInfo"]["version"]
                    )
                    b = Broker(bridge, c, r, options_enabled=True)
                    b.accounts()
                    intent = read_intent(args.intent, b)
                    adapter = OfficialExecutionAdapter(b, NativeExecutionTransport(bridge))
                    lifecycle = SupervisedLifecycle(state, adapter, c, r, run, fence)
                    if args.action == "review":
                        if not args.signal_bar:
                            raise Halt("exact signal-bar identity required")
                        result = lifecycle.prepare(intent, args.signal_bar)
                        if result["status"] == "approval_required":
                            packet = json.loads(
                                state.db.execute(
                                    "SELECT packet FROM plans WHERE key=?", (result["key"],)
                                ).fetchone()[0]
                            )
                            disclosure = packet["review"]["response"]["data"].get(
                                "market_data_disclosure"
                            )
                            if disclosure:
                                print(disclosure)  # Broker-required verbatim quote disclosure.
                            result["review"] = packet["review"]["response"]
                            result["authorization"] = (
                                "Unsigned binding only. Human must sign outside Codex."
                            )
                    elif args.action == "reconcile":
                        if not args.plan_key:
                            raise Halt("plan key required")
                        result = lifecycle.reconcile(args.plan_key, intent)
                    else:
                        if not args.plan_key or not args.approval or not args.public_key:
                            raise Halt(
                                "plan key and externally signed human approval/public key required"
                            )
                        artifact = json.loads(args.approval.read_text())
                        verifier = HumanApproval(args.public_key)
                        method = lifecycle.execute if args.action == "submit" else lifecycle.cancel
                        result = method(args.plan_key, intent, artifact, verifier)
                    state.event(run, "supervised_result", result)
                    state.finish(
                        run,
                        "completed"
                        if result["status"] not in {"pending", "approval_required"}
                        else result["status"],
                    )
                    print(json.dumps(result, indent=2, default=str, allow_nan=False))
                    return result
            except Exception as exc:
                state.event(run, "supervised_halt", {"class": type(exc).__name__})
                state.finish(run, "halted")
                raise
            finally:
                state.export_log()
    finally:
        state.close()


if __name__ == "__main__":
    main()
