"""Daily arm comparisons are market-adjusted: being long in a rising market is not skill."""

import pytest

from tradeagent.prospective import daily


class Store:
    def __init__(self, decisions, outcomes):
        self.data = {
            "decisions": decisions,
            "outcomes": outcomes,
            "resolution_failures": [],
            "symbol_outcomes": [],
        }

    def records(self, kind):
        return self.data[kind]


def long(gross, market):
    return {"gross": gross, "base_net": gross, "stress_net": gross, "benchmark_gross": market}


CASH = {"gross": 0, "base_net": 0, "stress_net": 0}


def session(day, arms):
    decision = {
        "decision_id": f"d{day}",
        "research_protocol": daily.PROTOCOL,
        "evidence_kind": "synthetic",
        "session_open": day,
        "quantitative_observations": [],
        "comparisons": {
            name: {"shadow_direction": int(value is not CASH), "status": "VALID"}
            for name, value in arms.items()
        },
    }
    outcome = {"decision_id": f"d{day}", "evidence_kind": "synthetic", "comparisons": arms}
    return decision, outcome


def test_always_long_arm_does_not_beat_cash_on_market_drift():
    sessions = [
        session(
            day,
            {
                # Same stock, same move as SPY: raw return looks good, excess is zero.
                "quant_only": long(0.01, 0.01),
                "codex_only": CASH,
                "quant_codex": long(0.012, 0.01),
                "seeded_random": CASH,
            },
        )
        for day in range(3)
    ]
    store = Store([s[0] for s in sessions], [s[1] for s in sessions])
    pool = daily.compare(store)["evidence_pools"]["synthetic"]
    assert pool["paired_base_net_differences"]["quant_only"]["seeded_random"] == pytest.approx(0.01)
    excess = pool["paired_excess_differences"]["quant_only"]["seeded_random"]
    assert excess["days"] == 3 and excess["mean"] == pytest.approx(0)
    combined = pool["paired_excess_differences"]["quant_codex"]["seeded_random"]
    assert combined["mean"] == pytest.approx(0.002)
    assert pool["arms"]["quant_only"]["long_days"] == 3
    assert pool["arms"]["seeded_random"]["paired_excess_over_benchmark"]["mean"] == 0


def test_single_day_has_no_standard_error():
    decision, outcome = session(
        0,
        {
            "quant_only": long(0.01, 0.0),
            "codex_only": CASH,
            "quant_codex": CASH,
            "seeded_random": CASH,
        },
    )
    pool = daily.compare(Store([decision], [outcome]))["evidence_pools"]["synthetic"]
    stats = pool["arms"]["quant_only"]["paired_excess_over_benchmark"]
    assert stats == {"days": 1, "mean": pytest.approx(0.01), "standard_error": None}
