"""Supplementary cohorts must not require the original four-delay complete case."""

import hashlib
from datetime import datetime

from test_continuous import market

from tradeagent.prospective import continuous as v5
from tradeagent.prospective.v5_supplement import supplement, write_report


def test_pairwise_and_per_benchmark_cohorts_are_independent_and_read_only(tmp_path):
    clock = [datetime(2026, 10, 15, 11, 45, tzinfo=v5.NY).timestamp()]

    def collector(*args, **kwargs):
        data = market(clock[0])
        if clock[0] == start + 180:
            del data["quotes"]["SPY"]
        return data

    start = clock[0]
    p = v5.Pipeline(tmp_path / "source", clock=lambda: clock[0], collector=collector)
    try:
        frozen = v5.VERSION
        p.tick()
        for delay in [60, 180, 600, 3600]:
            clock[0] = start + delay
            p.endpoints()
        clock[0] += 60
        db = p.directory / "experience.sqlite3"
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        r = supplement(p.directory, clock[0])
        write_report(tmp_path / "derived", r)
        assert r["original_primary_matched_n"] == 0
        assert r["pairwise"]["long"]["1_vs_3"]["raw_later_minus_1"]["opportunities"] == 1
        assert r["pairwise"]["long"]["1_vs_5"]["raw_later_minus_1"]["opportunities"] == 0
        assert r["pairwise"]["long"]["1_vs_10"]["raw_later_minus_1"]["opportunities"] == 1
        assert r["per_delay_baselines"]["long"]["3"]["spy"]["opportunities"] == 0
        assert r["per_delay_baselines"]["long"]["1"]["spy"]["opportunities"] == 1
        assert r["completeness"]["morning"]["5"]["entry_missing"] == 1
        assert r["completeness"]["morning"]["3"]["own_quote_outcome_complete"] == 1
        assert hashlib.sha256(db.read_bytes()).hexdigest() == before
        assert v5.VERSION == frozen
    finally:
        p.store.close()
