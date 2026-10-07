"""Daily signal identity is independent of truthful live input availability."""

import copy
import hashlib
import struct
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from test_canary_review import bridge
from test_release_policy import facts, histories, tiny_snapshot

from tradeagent import canary_review as review_module
from tradeagent.calendar import daily_session_bounds, session_bounds
from tradeagent.canary_review import live_rsi_snapshots, run_review
from tradeagent.model import Config, Halt, Risk, digest
from tradeagent.research.domain import timestamp
from tradeagent.research.lab import scan, seed
from tradeagent.research.store import Experience
from tradeagent.risk import check_order
from tradeagent.simulator import SimClock
from tradeagent.state import State
from tradeagent.strategy import DEFAULT_FAMILIES, DEFAULT_PARAMS, BaselineStrategy

NOW = timestamp("2026-10-07T16:00:00Z")


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def daily_row(label, close="2.3", **changes):
    return {
        "begins_at": label,
        "open_price": close,
        "high_price": close,
        "low_price": close,
        "close_price": close,
        "volume": 100,
        "session": "reg",
        **changes,
    }


class Reads:
    def __init__(self, now, labels=None, quote_time=None):
        self.now = now
        self.labels = labels or ["2026-10-05T00:00:00Z", "2026-10-06T00:00:00Z"]
        self.quote_time = now if quote_time is None else quote_time
        self.calls = []
        self.histories = {}

    def read(self, name, args):
        self.calls.append((name, copy.deepcopy(args)))
        if name == "get_equity_quotes":
            return {
                "data": {
                    "results": [
                        {
                            "quote": {
                                "symbol": s,
                                "bid_price": "2.31",
                                "ask_price": "2.32",
                                "venue_bid_time": stamp(self.quote_time),
                                "venue_ask_time": stamp(self.quote_time),
                            }
                        }
                        for s in args["symbols"]
                    ]
                }
            }
        assert name == "get_equity_historicals"
        assert args["interval"] == "day" and args["bounds"] == "regular"
        self.histories = {
            s: [
                daily_row(label, str(2.2 + i * 0.05) if s == "TINY" else "100")
                for i, label in enumerate(self.labels)
            ]
            for s in args["symbols"]
        }
        return {
            "data": {
                "results": [
                    {"symbol": s, "interval": "day", "bounds": "regular", "bars": bs}
                    for s, bs in self.histories.items()
                ]
            }
        }


def collect(now=NOW, labels=None, quote_time=None):
    reader = Reads(now, labels, quote_time)
    times = iter([now, now + 0.1, now + 0.2, now + 0.3])
    evidence = []
    snapshots = live_rsi_snapshots(reader, ["TINY"], lambda: next(times), evidence.append)
    return snapshots["TINY"], reader, evidence[0]


def test_yesterday_completed_bar_and_actual_receipts_during_regular_session():
    snapshot, reader, evidence = collect()
    last = snapshot.bars[-1]
    expected_close = session_bounds(datetime(2026, 10, 6).date())[1]
    assert last.start == timestamp("2026-10-06T00:00:00Z")
    assert last.end == expected_close < last.available_at <= snapshot.decision_time
    assert last.available_at == NOW + 0.2
    assert snapshot.quote_available_at == NOW + 0.1
    assert snapshot.decision_time == NOW + 0.3
    assert snapshot.signal_bar_begins_at == "2026-10-06T00:00:00Z"
    assert evidence["raw_histories"]["TINY"]["bars"] == reader.histories["TINY"]
    assert evidence["decision_time"] == snapshot.decision_time
    assert snapshot.signal_bar_id is not None


def test_midnight_utc_current_unfinished_candle_excluded_without_rewriting():
    labels = ["2026-10-05T00:00:00Z", "2026-10-06T00:00:00Z", "2026-10-07T00:00:00Z"]
    snapshot, reader, evidence = collect(labels=labels)
    assert len(snapshot.bars) == 2 and snapshot.signal_bar_begins_at == labels[-2]
    assert evidence["raw_histories"]["TINY"]["bars"][-1]["begins_at"] == labels[-1]
    assert reader.histories["TINY"][-1]["begins_at"] == labels[-1]
    assert daily_session_bounds(labels[-1]) == session_bounds(datetime(2026, 10, 7).date())


def test_today_eligible_after_official_close_only_if_provider_supplies_bar():
    after_close = timestamp("2026-10-07T20:01:00Z")
    labels = ["2026-10-06T00:00:00Z", "2026-10-07T00:00:00Z"]
    snapshot, _, _ = collect(after_close, labels)
    assert snapshot.signal_bar_begins_at == labels[-1]
    assert snapshot.bars[-1].end == timestamp("2026-10-07T20:00:00Z")
    missing, _, _ = collect(after_close, labels[:-1])
    assert missing.signal_bar_begins_at == labels[0]


def test_early_close_uses_exchange_calendar():
    assert daily_session_bounds("2026-11-27T00:00:00Z")[1] == timestamp("2026-11-27T18:00:00Z")


@pytest.mark.parametrize(
    "label",
    [
        "2026-10-08T00:00:00Z",
        "2026-10-04T00:00:00Z",
        "2026-10-06T12:00:00Z",
        "2026-10-06T00:00:00",
    ],
)
def test_future_ambiguous_non_session_or_naive_label_fails_closed(label):
    with pytest.raises(Halt):
        collect(labels=["2026-10-05T00:00:00Z", label])


def test_daily_open_label_and_local_midnight_map_to_exact_same_session():
    bounds = session_bounds(datetime(2026, 10, 6).date())
    assert daily_session_bounds("2026-10-06T13:30:00Z") == bounds
    assert daily_session_bounds("2026-10-06T00:00:00-04:00") == bounds


@pytest.mark.parametrize("offset", [-121, 1])
def test_stale_or_future_live_quote_still_rejected(offset):
    with pytest.raises(ValueError, match="stale/future quote"):
        collect(quote_time=NOW + offset)


@pytest.mark.parametrize("field", ["quote_times", "bid_times", "ask_times", "book"])
@pytest.mark.parametrize("offset", [-121, 0.251])
def test_order_quote_and_book_freshness_rules_unchanged(field, offset):
    clock = SimClock()
    snapshot = tiny_snapshot(clock)
    if field == "book":
        snapshot.liquidity["TINY"]["asof"] = clock() + offset
    else:
        getattr(snapshot, field)["TINY"] = clock() + offset
    from tradeagent.model import Intent, dec

    with pytest.raises(Halt):
        check_order(
            Intent("TINY", "buy", dec(1), dec("2.32")),
            snapshot,
            Config(allowed_symbols=["TINY"]),
            Risk(),
            clock(),
            snapshot.nav,
        )


def test_daily_completion_availability_and_benchmark_alignment_cannot_be_forged():
    snapshot, _, _ = collect()
    with pytest.raises(ValueError, match="future/unknown"):
        replace(snapshot, bars=tuple(replace(b, available_at=NOW + 10) for b in snapshot.bars))
    with pytest.raises(ValueError, match="unfinished|invalid session"):
        replace(snapshot, bars=(*snapshot.bars[:-1], replace(snapshot.bars[-1], end=NOW)))
    with pytest.raises(ValueError, match="identity disagreement"):
        replace(snapshot, signal_bar_begins_at="2026-10-05T00:00:00Z")


@pytest.mark.parametrize("family", DEFAULT_FAMILIES)
def test_frozen_strategy_outputs_bit_identical_for_same_completed_closes(family):
    daily, _, _ = collect()
    # Old aligned input shape, same closes, quote, decision, references and parameters.
    intraday = replace(
        daily,
        signal_bar_begins_at=None,
        bars=(
            *daily.bars[:-1],
            replace(daily.bars[-1], end=daily.decision_time, available_at=daily.decision_time),
        ),
        benchmark_bars=(
            *daily.benchmark_bars[:-1],
            replace(
                daily.benchmark_bars[-1], end=daily.decision_time, available_at=daily.decision_time
            ),
        ),
    )
    strategy = BaselineStrategy(family, "v1", family, DEFAULT_PARAMS)
    a, b = (
        strategy.predict(daily, daily.decision_time),
        strategy.predict(intraday, daily.decision_time),
    )
    assert a.direction == b.direction
    for name in ["expected_return", "confidence"]:
        assert struct.pack("!d", getattr(a, name)) == struct.pack("!d", getattr(b, name))
    assert a.features.encoded == b.features.encoded
    assert a.distribution.encoded == b.distribution.encoded


def test_repeat_same_completed_bar_retains_identity_and_suppresses_predictions(tmp_path):
    first, _, _ = collect()
    later, _, _ = collect(NOW + 60)
    assert first.decision_time != later.decision_time
    assert first.signal_bar_id == later.signal_bar_id
    store = Experience(tmp_path)
    try:
        seed(store, NOW - 1)
        original = scan(store, first, first.decision_time)
        repeated = scan(store, later, later.decision_time)
        assert repeated["status"] == "duplicate_suppressed"
        assert repeated["snapshot_id"] == original["snapshot_id"]
        assert repeated["signal_bar_id"] == original["signal_bar_id"]
        assert store.db.execute("select count(*) from market_snapshots").fetchone()[0] == 1
        assert store.db.execute("select count(*) from predictions").fetchone()[0] == 7
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        store.close()


def test_exact_timing_only_hash_transition_and_unknown_drift(tmp_path, monkeypatch):
    import tradeagent.research.lab as lab
    import tradeagent.strategy as strategy

    snapshot, _, _ = collect()
    actual = strategy.implementation_hash()
    old = "e8b7ee7dbf6eb6e2ad6b8e46198e437d9036255210cbc748b77db7cc082ca80e"
    store = Experience(tmp_path)
    try:
        monkeypatch.setattr(strategy, "implementation_hash", lambda: old)
        seed(store, NOW - 1)
        monkeypatch.setattr(strategy, "implementation_hash", lambda: actual)
        monkeypatch.setattr(
            lab, "implementation_hash", lambda: hashlib.sha256(b"unknown").hexdigest()
        )
        with pytest.raises(ValueError, match="implementation changed"):
            scan(store, snapshot, snapshot.decision_time)
        monkeypatch.setattr(lab, "implementation_hash", lambda: actual)
        assert scan(store, snapshot, snapshot.decision_time)["status"] == "persisted"
        assert (
            store.db.execute(
                "select distinct implementation_hash from strategy_versions"
            ).fetchone()[0]
            == old
        )
    finally:
        store.close()


def test_synthetic_daily_canary_review_lifecycle_then_duplicate_halts(tmp_path, monkeypatch):
    clock = SimClock(NOW)
    market, _, _ = collect()
    clock.value = market.decision_time
    b, sent = bridge(tmp_path, monkeypatch, clock)
    monkeypatch.setattr(review_module, "deployment_hash", lambda root: "synthetic")
    snapshot = tiny_snapshot(clock)

    class Broker:
        account = {"account_number": "synthetic-only"}

        def snapshot(self):
            return copy.deepcopy(snapshot)

        def histories(self):
            return histories(clock)

    state, store = State(tmp_path / "journal"), Experience(tmp_path / "research")
    try:
        seed(store, NOW - 1)
        args = (
            b,
            Broker(),
            state,
            store,
            Config(allowed_symbols=["TINY"]),
            Risk(),
            tmp_path,
            digest("synthetic-only"),
            lambda: {"TINY": market},
            lambda: facts(clock),
            clock,
        )
        result = run_review(*args)
        assert result["status"] == "READY_FOR_USER_CONFIRMED_MANUAL_CANARY", result
        assert result["broker_counts"] == {"reads": 0, "review": 1, "place": 0, "cancel": 0}
        clock.advance(1)
        again = run_review(*args)
        assert again["status"] == "HALT" and "already used" in again["blocker"]
        assert len(sent) == 1
        assert state.db.execute("SELECT count(*) from intents").fetchone()[0] == 0
        assert state.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        state.close()
        store.close()
