"""Offline v5 forward timing, missingness, fair benchmarks and isolation."""

import copy
import json
import sqlite3
from datetime import date, datetime

import pytest

from tradeagent.calendar import session_bounds
from tradeagent.prospective import continuous as c


def instant(hour=11, minute=45):
    return datetime(2026, 10, 15, hour, minute, tzinfo=c.NY).timestamp()


def market(now, moving=True):
    bounds = session_bounds(c.day_of(now))
    end = int(now // 60) * 60
    bars, quotes = [], {}
    for symbol in ("AAPL", "SPY"):
        for i in range(30):
            value = 100 * (1 + (0.0005 * i if moving and symbol == "AAPL" else 0))
            close = value + (0.05 if moving and symbol == "AAPL" else 0)
            bars.append(
                dict(
                    symbol=symbol,
                    start=end - 1800 + i * 60,
                    end=end - 1740 + i * 60,
                    available_at=now,
                    open=value,
                    high=close + 0.01,
                    low=value - 0.01,
                    close=close,
                    volume=50000,
                )
            )
        quotes[symbol] = dict(bid=101, ask=101.02, asof=now, observed_at=now)
    return dict(
        schema_version=1,
        symbols=["AAPL", "SPY"],
        bars=bars,
        quotes=quotes,
        requested_at=now,
        observed_at=now,
        session_open=bounds[0],
        session_close=bounds[1],
        evidence_kind="prospective",
        source="synthetic-offline",
        references={},
        limitations=[],
    )


@pytest.fixture
def pipeline(tmp_path):
    clock, calls = [instant()], []

    def collector(symbols, **kwargs):
        calls.append((symbols, kwargs))
        return market(clock[0])

    p = c.Pipeline(tmp_path, clock=lambda: clock[0], collector=collector)
    yield p, clock, calls
    p.store.close()


def test_rolling_signal_validity_and_future_missingness():
    now = instant()
    rows = c.features(market(now), now)
    assert rows[0]["direction"] == 1 and rows[0]["valid"]
    assert rows[1]["direction"] == 0 and rows[1]["valid"]
    broken = market(now)
    broken["bars"].pop(20)
    assert not c.features(broken, now)[0]["valid"]
    broken = market(now)
    broken["quotes"]["AAPL"]["asof"] = now + 1
    assert not c.features(broken, now)[0]["valid"]
    broken = market(now)
    broken["bars"][0]["available_at"] = now + 1
    with pytest.raises(ValueError, match="future"):
        c.features(broken, now)


def test_duplicate_continuing_expired_retrigger_and_immutable(pipeline):
    p, clock, calls = pipeline
    p.tick()
    assert len(p.records("opportunity")) == 1
    p.tick()
    assert len(calls) == 1
    clock[0] += 300
    p.scan_slot(clock[0])
    assert len(p.records("opportunity")) == 1
    assert p.records("scan")[-1]["rows"][0]["transition"] == "CONTINUING"
    p.collector = lambda *a, **k: market(clock[0], moving=False)
    clock[0] += 300
    p.scan_slot(clock[0])
    assert p.records("scan")[-1]["rows"][0]["transition"] == "EXPIRED"
    p.collector = lambda *a, **k: market(clock[0])
    clock[0] += 300
    p.scan_slot(clock[0])
    assert len(p.records("opportunity")) == 2
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        p.db.execute("DELETE FROM continuous_events")


def test_exact_delays_common_exit_costs_and_matched_basket(pipeline):
    p, clock, calls = pipeline
    p.tick()
    signal = p.records("opportunity")[0]
    for delay in [60, 180, 300, 600, 3600]:
        clock[0] = signal["detected_at"] + delay
        p.endpoints()
    r = p.report()
    assert r["matched_opportunities"] == 1
    assert all(call[1]["quotes_only"] for call in calls[1:])
    for row in r["outcomes"]:
        assert row["status"] == "COMPLETE"
        assert row["selection"]["actual_holding_seconds"] == 3600 - row["delay_minutes"] * 60
        assert row["selection"]["net_modeled_return"] == pytest.approx(101 / 101.02 - 1 - 0.001)
        assert row["selection"]["exit_quote_at"] == signal["exit_at"]
        assert row["basket_net"] == row["spy_net"]
    assert r["comparison"]["long"]["1"]["net"]["session_standard_error"] is None
    assert "not a combined" not in c.render(r)  # renderer does not invent a portfolio
    assert json.loads((p.directory / "daily-status.json").read_text())["matched_opportunities"] == 1


def test_missed_endpoint_never_backfilled_or_selected_away(pipeline):
    p, clock, _ = pipeline
    p.tick()
    start = clock[0]
    clock[0] = start + 120
    p.endpoints()
    for delay in [180, 300, 600, 3600]:
        clock[0] = start + delay
        p.endpoints()
    r = p.report()
    assert r["matched_opportunities"] == 0
    assert r["unique_opportunities"] == 1 and len(r["outcomes"]) == 4
    assert r["outcomes"][0]["status"] == "MISSING"
    clock[0] += 1
    p.endpoints()
    assert len(p.records("endpoint")) == 5


def test_late_quote_rejected_and_stale_kept(pipeline):
    p, clock, _ = pipeline
    p.tick()
    original = clock[0]

    def collector(*args, **kwargs):
        data = market(clock[0])
        for q in data["quotes"].values():
            q.update(bid=110, ask=110.02)
        return data

    p.collector = collector
    for delay in [60, 180, 300, 600, 3600]:
        clock[0] = original + delay
        p.endpoints()
    r = p.report()
    assert r["matched_opportunities"] == 1
    assert all(row["stale_before_entry"] and not row["entry_eligible"] for row in r["outcomes"])
    assert not c.quote_ok(
        dict(bid=1, ask=2, asof=original + 59, observed_at=original + 60),
        original + 60,
        original + 60,
    )
    assert not c.quote_ok(
        dict(bid=1, ask=2, asof=original + 120, observed_at=original + 120),
        original + 120,
        original + 60,
    )


def test_holidays_early_close_and_no_backfill(pipeline):
    assert c.slots(date(2026, 12, 25)) == []
    early = c.slots(date(2026, 11, 27))
    assert datetime.fromtimestamp(early[-1], c.NY).hour == 12
    p, clock, _ = pipeline
    clock[0] += 900
    r = p.report()
    assert len(r["missed_slots"]) == 3
    assert p.records("scan") == []


def test_timeout_is_terminal_and_scan_not_retried(pipeline):
    p, clock, calls = pipeline

    def failed(*args, **kwargs):
        raise TimeoutError("bounded failure")

    p.collector = failed
    p.tick()
    p.tick()
    assert len(p.records("scan_started")) == len(p.records("scan_failure")) == 1
    assert not p.records("opportunity")


def test_session_clustering_not_trade_count():
    r = c.cluster([("a", 1), ("a", -1), ("b", 0.2)])
    assert r["sessions"] == 2 and r["session_mean"] == pytest.approx(0.1)
    assert r["session_standard_error"] == pytest.approx(0.1)


def test_config_drift_fails_closed(pipeline, monkeypatch):
    p, _, _ = pipeline
    monkeypatch.setattr(c, "VERSION", "changed")
    with pytest.raises(ValueError, match="separate state"):
        c.Pipeline(p.directory)


def test_negative_quote_diagnostic_is_explicit(pipeline):
    p, clock, _ = pipeline
    p.tick()
    o = copy.deepcopy(p.records("opportunity")[0])
    q = dict(bid=100, ask=101, asof=clock[0], observed_at=clock[0])
    result = c.modeled(o, {"AAPL": q}, {"AAPL": {**q, "bid": 98, "ask": 99}}, "AAPL", -1)
    assert result["entry_price"] == 100 and result["exit_price"] == 99
    assert result["net_modeled_return"] == pytest.approx(0.009)


def test_horizon_ineligible_recorded_without_forward_reads(pipeline):
    p, clock, calls = pipeline
    clock[0] = instant(15, 0)
    p.scan_slot(clock[0])
    assert len(p.records("opportunity")) == 1
    assert not p.records("opportunity")[0]["eligible"]
    clock[0] += 3600
    p.endpoints()
    assert len(calls) == 1 and not p.records("endpoint")
    assert all(r["status"] == "INELIGIBLE_HORIZON" for r in p.report()["outcomes"])


def test_missing_basket_quote_prevents_matched_comparison(pipeline):
    p, clock, _ = pipeline
    p.tick()
    original = clock[0]

    def collector(*args, **kwargs):
        data = market(clock[0])
        del data["quotes"]["SPY"]
        return data

    p.collector = collector
    for delay in [60, 180, 300, 600, 3600]:
        clock[0] = original + delay
        p.endpoints()
    clock[0] += 60
    r = p.report()
    assert r["matched_opportunities"] == 0
    assert all(
        row["selection"] and row["basket_net"] is None and row["status"] == "MISSING"
        for row in r["outcomes"]
    )


def test_reject_legacy_state_without_changing_existing_records(tmp_path):
    from tradeagent.research.store import Experience

    store = Experience(tmp_path)
    store.close()
    before = (tmp_path / "experience.sqlite3").read_bytes()
    with pytest.raises(ValueError, match="independent state"):
        c.Pipeline(tmp_path)
    assert (tmp_path / "experience.sqlite3").read_bytes() == before


def test_v1_v2_activity_defers_before_market_access(pipeline, monkeypatch):
    from types import SimpleNamespace

    p, _, _ = pipeline
    p.collector = c.capture
    monkeypatch.setattr(c.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    with pytest.raises(ValueError, match="priority"):
        p.collect(["AAPL"])


def test_installer_calendar_isolated_and_safe_paths():
    import runpy
    from pathlib import Path

    installer = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/install_continuous.py")
    )
    service, timer = installer["units"](
        Path("/local/bin/python"), Path("/local/v5"), Path("/local/helper")
    )
    assert "tradeagent.prospective.continuous" in service
    assert "daily-news" not in service and "codex" not in service
    assert "Persistent=false" in timer and "00/15" in timer
    with pytest.raises(ValueError, match="absolute plain"):
        installer["units"](Path("/bad path/python"), Path("/local/v5"), Path("/local/helper"))


def test_expired_direction_uses_only_scans_available_at_entry(pipeline):
    p, clock, _ = pipeline
    p.tick()
    start = clock[0]
    clock[0] = start + 60
    p.endpoints()
    p.collector = lambda *a, **k: market(clock[0], moving=False)
    clock[0] = start + 300
    p.scan_slot(clock[0])
    p.endpoints()
    for delay in [600, 3600]:
        clock[0] = start + delay
        p.endpoints()
    r = p.report()
    assert not r["outcomes"][0]["expired_before_entry"]
    assert r["outcomes"][2]["expired_before_entry"]
    assert not r["outcomes"][2]["entry_eligible"]
