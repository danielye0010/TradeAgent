"""Offline, deterministic closed-loop fixture. This is never profitability evidence."""

import json
from dataclasses import asdict
from pathlib import Path

from .domain import Bar, MarketSnapshot, OptionBook, canonical, timestamp
from .evolution import evaluate, propose
from .lab import scan, seed
from .learning import learn_daily
from .outcomes import resolve
from .store import Experience


def fixture(decision, kind="synthetic", move=0.004):
    bars = tuple(
        Bar(
            "XYZ",
            decision - 3600 * (6 - i),
            decision - 3600 * (5 - i),
            decision - 3600 * (5 - i),
            100 + i * 0.1,
            100.2 + i * 0.1,
            99.9 + i * 0.1,
            100.1 + i * 0.1,
            10000,
        )
        for i in range(6)
    )
    benchmark = tuple(
        Bar(
            "SPY",
            decision - 3600 * (6 - i),
            decision - 3600 * (5 - i),
            decision - 3600 * (5 - i),
            500 + i * 0.1,
            500.2 + i * 0.1,
            499.9 + i * 0.1,
            500.1 + i * 0.1,
            10000,
        )
        for i in range(6)
    )
    call = OptionBook(
        "fixture-call",
        "XYZ",
        "call",
        "2027-01-15",
        100.0,
        decision,
        decision,
        1.9,
        2.1,
        2.0,
        iv=0.5,
        delta=0.5,
        gamma=0.1,
        theta=-0.1,
        vega=0.2,
        volume=1000,
        open_interest=1000,
    )
    put = OptionBook(
        "fixture-put", "XYZ", "put", "2027-01-15", 100.0, decision, decision, 1.9, 2.1, 2.0
    )
    snapshot = MarketSnapshot(
        "XYZ",
        decision,
        "deterministic-fixture",
        kind,
        "SPY",
        bars,
        benchmark,
        100.59,
        100.61,
        decision,
        decision,
        100.0,
        99.8,
        (call, put),
        decision - 3600,
        decision - 64800,
    )
    entry = bars[-1].close
    future = [
        Bar(
            "XYZ",
            decision,
            decision + 1800,
            decision + 1800,
            entry,
            entry * 1.006,
            entry * 0.999,
            entry * 1.003,
        ),
        Bar(
            "XYZ",
            decision + 1800,
            decision + 3600,
            decision + 3600,
            entry * 1.003,
            max(entry * 1.007, entry * (1 + move)),
            min(entry * 0.999, entry * (1 + move)),
            entry * (1 + move),
        ),
    ]
    bentry = benchmark[-1].close
    future.extend(
        [
            Bar(
                "SPY",
                decision,
                decision + 1800,
                decision + 1800,
                bentry,
                bentry * 1.001,
                bentry * 0.999,
                bentry * 1.0001,
            ),
            Bar(
                "SPY",
                decision + 1800,
                decision + 3600,
                decision + 3600,
                bentry * 1.0001,
                bentry * 1.001,
                bentry * 0.999,
                bentry * 1.0003,
            ),
        ]
    )
    exit_call = {
        **asdict(call),
        "asof": decision + 3600,
        "available_at": decision + 3600,
        "bid": 1.7,
        "ask": 1.9,
        "mark": 1.8,
    }
    exit_put = {
        **asdict(put),
        "asof": decision + 3600,
        "available_at": decision + 3600,
        "bid": 1.4,
        "ask": 1.6,
        "mark": 1.5,
    }
    return snapshot, {
        "source": snapshot.source,
        "bars": [asdict(b) for b in future],
        "options": [exit_call, exit_put],
    }


def run_demo(directory: Path):
    directory = Path(directory)
    if directory.exists():
        raise ValueError("choose a fresh demo directory")
    store = Experience(directory)
    start = timestamp("2026-01-05T16:00:00Z")
    try:
        seed(store, start - 86400)
        events = []
        first_weights = {}
        for day in range(15):
            decision = start + day * 86400
            snapshot, future = fixture(decision)
            scan_result = scan(store, snapshot, decision)
            resolved = resolve(store, future, decision + 3600)
            learned = learn_daily(store, decision + 3601, "synthetic")
            if day == 0:
                first_weights = {
                    r["strategy_id"]: r["weight"]
                    for r in store.db.execute(
                        "SELECT * FROM strategy_scores WHERE run_id=?", (learned["run_id"],)
                    )
                }
            challengers = propose(store, decision + 3602, kind="synthetic") if day == 5 else []
            events.append(
                {
                    "day": day,
                    "scan": scan_result,
                    "resolve": resolved,
                    "learn": learned,
                    "challengers": challengers,
                }
            )
        evaluations = evaluate(store, start + 14 * 86400 + 3603)
        report = store.inspect()
        last_weights = {
            r["strategy_id"]: r["weight"] for r in report["scores"] if r["version"] == "v1"
        }
        assert first_weights != last_weights
        assert (
            report["counts"]["predictions"]
            == report["counts"]["outcomes"]
            == report["counts"]["attributions"]
        )
        assert report["counts"]["mutations"] == 5 and len(evaluations) == 5
        assert all(r["n"] == 9 and r["decision"] == "continue_testing" for r in evaluations)
        alpha_good_expression_bad = store.db.execute(
            "SELECT COUNT(*) FROM attributions WHERE expression='correct_thesis_poor_option_expression'"
        ).fetchone()[0]
        assert alpha_good_expression_bad > 0
        summary = {
            "status": "passed",
            "evidence": "SYNTHETIC_ONLY_NOT_ALPHA_VALIDATION",
            "real_broker_calls": 0,
            "prediction_count": report["counts"]["predictions"],
            "outcome_count": report["counts"]["outcomes"],
            "attribution_count": report["counts"]["attributions"],
            "lesson_revisions": report["counts"]["lessons"],
            "daily_learning_runs": report["counts"]["learning_runs"],
            "challengers_created": 5,
            "challenger_paired_days": 9,
            "challenger_state": "continue_testing",
            "correct_alpha_poor_option_expression": alpha_good_expression_bad,
            "initial_weights": first_weights,
            "final_weights": last_weights,
            "selector_state_changed": True,
            "schema_version": report["schema_version"],
            "integrity": report["integrity"],
        }
        (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        (directory / "events.json").write_text(canonical(events) + "\n")
        (directory / "snapshot.example.json").write_text(
            json.dumps(asdict(fixture(start)[0]), indent=2) + "\n"
        )
        (directory / "future.example.json").write_text(
            json.dumps(fixture(start)[1], indent=2) + "\n"
        )
        return summary
    finally:
        store.close()
