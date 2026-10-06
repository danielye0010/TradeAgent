"""Read-only observability. A report is evidence, never execution authorization."""

import json
import sqlite3
from pathlib import Path


def health_report(directory):
    directory = Path(directory).resolve()
    with sqlite3.connect("file:" + str(directory / "state.sqlite3") + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        events = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY seq")]
        for event in events:
            event["payload"] = json.loads(event["payload"])
        latest = {e["kind"]: e for e in events}
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        intents = [dict(r) for r in db.execute("SELECT * FROM intents")]
        accounting = (
            [dict(r) for r in db.execute("SELECT * FROM accounting")]
            if "accounting" in tables
            else []
        )
        halted = (
            [dict(r) for r in db.execute("SELECT * FROM emergency_halt")]
            if "emergency_halt" in tables
            else []
        )
        account_event = max(
            (
                e
                for e in events
                if e["kind"]
                in {"fill_reconciliation", "final_reconciliation", "portfolio_snapshot"}
            ),
            key=lambda e: e["seq"],
            default={},
        )
        account_payload = account_event.get("payload", {})
        snapshot = account_payload.get("snapshot", account_payload)
        if account_event.get("kind") == "fill_reconciliation":
            snapshot = {k: account_payload.get(k) for k in ("positions", "options", "cash", "nav")}
        reconciliation = max(
            (e for e in events if e["kind"] in {"fill_reconciliation", "final_reconciliation"}),
            key=lambda e: e["seq"],
            default=None,
        )
        return {
            "evidence_only": True,
            "integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
            "runs": [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY started")],
            "last_account_evidence": snapshot,
            "intents": intents,
            "accounting": accounting,
            "emergency_halt": halted,
            "last_policy": latest.get("autonomous_policy_authorization"),
            "strategy": latest.get("signal_snapshot") or latest.get("strategy_signal"),
            "risk": latest.get("risk_decisions") or latest.get("risk_pass"),
            "last_reconciliation": reconciliation,
            "last_account_read_time": account_event.get("time"),
            "orders_at_last_reconciliation": (
                latest.get("final_reconciliation", {}).get("payload", {}).get("snapshot") or {}
            ).get("orders"),
            "reconciled_order_receipt": latest.get("fill_reconciliation", {})
            .get("payload", {})
            .get("order"),
            "fills": latest.get("fill_reconciliation", {}).get("payload", {}).get("executions"),
            "submitted_ack": latest.get("broker_acknowledgment"),
            "daily_accounting": latest.get("accounting"),
            "fees_evidence": latest.get("fill_reconciliation", {}).get("payload", {}).get("fees"),
            "note": "No live OAuth probe; last successful recorded broker snapshot only. Cash-flow P&L is distinct from realized profit.",
        }


def halt_entries(directory, reason):
    """Atomic persistent flag; no reset, cancellation, exit order or broker access."""
    import time

    from .model import Halt
    from .state import State

    if not isinstance(reason, str) or not reason.strip():
        raise Halt("halt reason required")
    state = State(Path(directory))
    try:
        with state.db:
            state.db.execute(
                "CREATE TABLE IF NOT EXISTS emergency_halt(singleton INTEGER PRIMARY KEY CHECK(singleton=1), reason TEXT NOT NULL, at REAL NOT NULL)"
            )
            state.db.execute(
                "INSERT OR IGNORE INTO emergency_halt VALUES(1,?,?)", (reason, time.time())
            )
    finally:
        state.close()


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Recorded health evidence; optional persistent entry halt"
    )
    parser.add_argument("--state-dir", type=Path, default=Path("data"))
    parser.add_argument("--halt-entries", metavar="REASON")
    args = parser.parse_args()
    if args.halt_entries is not None:
        halt_entries(args.state_dir, args.halt_entries)
    print(json.dumps(health_report(args.state_dir), sort_keys=True, default=str))


if __name__ == "__main__":
    main()
