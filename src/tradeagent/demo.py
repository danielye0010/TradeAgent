"""Run a local trading demonstration with a synthetic MCP broker."""

import copy
import json
from dataclasses import asdict
from pathlib import Path

from .legacy.strategy import TestTrendStrategy
from .model import Config, Halt, Risk, dec, digest
from .options import OptionIntent
from .simulator import SimClock, SimulatedMCP
from .state import State
from .supervised import OfficialExecutionAdapter, SupervisedLifecycle


def simulated_human(plan):
    """Explicit fixture authorization. Rejected by every production approval verifier."""
    if plan["binding"].get("simulation") is not True:
        raise Halt("simulation fixture cannot authorize a real order")
    return {
        "binding": copy.deepcopy(plan["binding"]),
        "simulation": True,
        "actor": "SIMULATED_HUMAN",
    }


def run_demo(directory: Path, read_evidence: Path | None = None):
    directory = directory.resolve()
    if directory.exists():
        raise Halt("demo directory must be new; preserve prior evidence and runtime state")
    c = Config(mode="SUPERVISED", supervised_enabled=True)
    r = Risk()
    result = {
        "mode": "SIMULATION",
        "real_review_place_cancel_calls": 0,
        "account_and_orders": "SYNTHETIC",
        "strategy_validation": "TEST_STRATEGY_NOT_VALIDATED_FOR_LIVE_TRADING",
    }
    if read_evidence:
        evidence = json.loads(read_evidence.read_text())
        if evidence["real_review_place_cancel_calls"] != 0:
            raise Halt("read-only input contains broker write calls")
        result["real_read_connectivity"] = {
            k: evidence[k]
            for k in (
                "timestamp",
                "codex_native_authenticated",
                "robinhood_authenticated",
                "server_version",
                "structural_contract_hash",
                "selected_account",
                "equity_positions_count",
                "equity_orders_count",
                "option_positions_count",
                "option_orders_count",
                "option_read_error",
            )
        }
        result["real_read_evidence_hash"] = digest(evidence)
    for asset in ("equity", "option"):
        clock = SimClock()
        broker = SimulatedMCP(directory / asset / "broker", clock, "full_fill")
        state = State(directory / asset / "agent")
        adapter = OfficialExecutionAdapter(broker, broker, clock)
        try:
            with state.lock(c.lease_seconds) as fence:
                run = state.start(c, r)
                initial = broker.snapshot()
                state.event(run, "synthetic_funded_account", asdict(initial))
                engine = SupervisedLifecycle(state, adapter, c, r, run, fence, clock)
                if asset == "equity":
                    histories = {
                        k: [
                            {"begins_at": "2026-10-02T13:30:00Z", "close_price": str(350 + 2 * i)}
                            for i in range(220)
                        ]
                        for k in c.allowed_symbols
                    }
                    strategy = TestTrendStrategy()
                    signal = strategy.signals(histories, c)["SPY"]
                    intent = strategy.proposal(signal, initial, c)
                    normalized = asdict(signal)
                    bar = signal.bar_time
                else:
                    quote = next(iter(initial.option_quotes.values()))
                    intent = OptionIntent(quote.contract, "buy", dec(1), quote.ask)
                    normalized = {
                        "strategy_id": "long-premium-infrastructure-fixture",
                        "strategy_version": c.strategy_version,
                        "contract": asdict(quote.contract),
                        "premium_at_risk": str(intent.premium_at_risk),
                        "source": "SYNTHETIC",
                        "status": "TEST_STRATEGY_NOT_VALIDATED_FOR_LIVE_TRADING",
                    }
                    bar = "2026-10-05T14:00:00Z"
                plan = engine.prepare(intent, bar, normalized)
                artifact = simulated_human(plan)
                (state.directory / "simulated-human-approval.json").write_text(
                    json.dumps(artifact, indent=2) + "\n"
                )
                first = engine.execute(plan["key"], intent, artifact)
                state.finish(run, "simulated_filled")
                # A second run with a new run ID uses the same decision and persistent broker.
                repeat = state.start(c, r)
                again = SupervisedLifecycle(state, adapter, c, r, repeat, fence, clock).prepare(
                    intent, bar, normalized
                )
                state.finish(repeat, "duplicate_suppressed")
                state.export_log()
                final = broker.snapshot()
                result[asset] = {
                    "first_run": run,
                    "second_run": repeat,
                    "signal": normalized,
                    "proposal": intent.payload(),
                    "synthetic_initial_nav": str(initial.nav),
                    "synthetic_initial_cash": str(initial.cash),
                    "first_result": first,
                    "second_result": again,
                    "final_positions": final.positions,
                    "final_options": final.options,
                    "final_cash": str(final.cash),
                    "final_nav": str(final.nav),
                    "broker_order_count": broker.db.execute(
                        "SELECT COUNT(*) FROM simulated_orders"
                    ).fetchone()[0],
                    "agent_intent_count": state.db.execute(
                        "SELECT COUNT(*) FROM intents"
                    ).fetchone()[0],
                    "consumed_approvals": state.db.execute(
                        "SELECT COUNT(*) FROM approvals WHERE consumed=1"
                    ).fetchone()[0],
                    "simulated_wire_calls": [x["tool"] for x in broker.calls],
                    "sqlite_integrity": state.db.execute("PRAGMA integrity_check").fetchone()[0],
                    "journal": str(state.path),
                    "log": str(state.directory / "events.jsonl"),
                    "broker_journal": str(broker.store.path),
                }
        finally:
            state.close()
            broker.close()
    inspection = asdict(Config(state_dir=str(directory / "equity" / "agent")))
    (directory / "inspect.example.json").write_text(json.dumps(inspection, indent=2) + "\n")
    (directory / "demonstration.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    return result
