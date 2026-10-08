import math
from datetime import timedelta

import pytest
from test_broker_contract import NOW, RawReadFake

from tradeagent.broker import Broker
from tradeagent.model import (
    MAX_FUTURE_SKEW_SECONDS,
    Config,
    Halt,
    Intent,
    Risk,
    dec,
    timestamp_fresh,
)
from tradeagent.risk import check_order, check_state


@pytest.mark.parametrize(
    "value,expected",
    [
        (1000.0368, True),
        (1000.125, True),
        (1000.25, True),
        (math.nextafter(1000.25, math.inf), False),
        (1000.5, False),
        (880, True),
        (math.nextafter(880, -math.inf), False),
        (879, False),
        (float("nan"), False),
        (True, False),
    ],
)
def test_bounded_book_freshness(value, expected):
    assert timestamp_fresh(value, 1000, 120, future_skew=MAX_FUTURE_SKEW_SECONDS) is expected


@pytest.mark.parametrize("tolerance", [-0.1, 0.251, 1, float("nan"), True])
def test_future_allowance_cannot_exceed_hard_cap_or_be_invalid(tolerance):
    assert not timestamp_fresh(1000, 1000, 120, future_skew=tolerance)


def test_default_timestamp_rule_and_staleness_are_unchanged():
    assert not timestamp_fresh(1000.01, 1000, 120)
    assert timestamp_fresh(880, 1000, 120)
    assert not timestamp_fresh(879.99, 1000, 120)
    assert 0 < MAX_FUTURE_SKEW_SECONDS <= 1
    assert Risk().max_data_age_seconds == 120


def snapshot_with_book_offset(offset):
    raw = RawReadFake()
    raw.payloads["get_equity_price_book"]["books"][0]["updated_at"] = (
        NOW + timedelta(seconds=offset)
    ).isoformat()
    config, risk = Config(allowed_symbols=["SPY"]), Risk()
    snapshot = Broker(raw, config, risk).snapshot(NOW.timestamp())
    return raw, config, risk, snapshot


@pytest.mark.parametrize("offset", [0.0368, 0.125, 0.25])
def test_risk_accepts_only_small_book_clock_skew(offset):
    _, config, risk, snapshot = snapshot_with_book_offset(offset)
    check_order(
        Intent("SPY", "buy", dec(1), dec("100.01")),
        snapshot,
        config,
        risk,
        NOW.timestamp(),
        snapshot.nav,
    )


@pytest.mark.parametrize("offset", [0.250001, 1, -120.000001])
def test_risk_rejects_future_or_stale_book_beyond_boundary(offset):
    with pytest.raises(Halt, match="stale/future"):
        snapshot_with_book_offset(offset)
    # The risk authority also rejects an independently supplied stale book.
    _, config, risk, snapshot = snapshot_with_book_offset(0)
    snapshot.liquidity["SPY"]["asof"] = NOW.timestamp() + offset
    with pytest.raises(Halt, match="liquidity/depth"):
        check_order(
            Intent("SPY", "buy", dec(1), dec("100.01")),
            snapshot,
            config,
            risk,
            NOW.timestamp(),
            snapshot.nav,
        )


@pytest.mark.parametrize("change", ["price", "symbol"])
def test_skew_allowance_does_not_accept_quote_book_mismatch(change):
    raw = RawReadFake()
    book = raw.payloads["get_equity_price_book"]["books"][0]
    book["updated_at"] = (NOW + timedelta(seconds=0.125)).isoformat()
    if change == "price":
        book["bids"][0]["price"] = "99.98"
    else:
        book["symbol"] = "QQQ"
    if change == "symbol":
        with pytest.raises(Halt, match="coverage"):
            Broker(raw, Config(allowed_symbols=["SPY"]), Risk()).snapshot(NOW.timestamp())
    else:
        snapshot = Broker(raw, Config(allowed_symbols=["SPY"]), Risk()).snapshot(NOW.timestamp())
        assert snapshot.bids["SPY"] == dec("99.98")
        assert snapshot.bid_times["SPY"] == snapshot.liquidity["SPY"]["asof"]


@pytest.mark.parametrize("field,reason", [("quote", "stale market"), ("account", "stale account")])
def test_quote_skew_remains_bounded_and_account_future_timestamp_remains_strict(field, reason):
    _, _, risk, snapshot = snapshot_with_book_offset(0.125)
    if field == "quote":
        snapshot.quote_times["SPY"] = NOW.timestamp() + MAX_FUTURE_SKEW_SECONDS + 0.01
    else:
        snapshot.asof = NOW.timestamp() + 0.01
    with pytest.raises(Halt, match=reason):
        check_state(snapshot, risk, NOW.timestamp())


def test_synthetic_shadow_with_skew_records_intent_and_zero_broker_writes(tmp_path, monkeypatch):
    import time

    from tradeagent.legacy.runner import cycle
    from tradeagent.state import State

    monkeypatch.setattr(time, "time", lambda: NOW.timestamp())
    raw, config, risk, _ = snapshot_with_book_offset(0.125)
    for i, bar in enumerate(raw.payloads["get_equity_historicals"]["results"][0]["bars"]):
        price = str(dec(20) + dec(i) / 2)
        bar.update(open_price=price, low_price=price, high_price=price, close_price=price)
    broker = Broker(raw, config, risk)
    native_snapshot, native_histories = broker.snapshot, broker.histories
    broker.snapshot = lambda: native_snapshot(NOW.timestamp())
    broker.histories = lambda: native_histories(NOW.timestamp())
    writes = []

    def forbidden(*args):
        writes.append(args)
        raise AssertionError("Synthetic SHADOW attempted broker review/place/cancel")

    broker.review = broker.submit = broker.cancel = forbidden
    state = State(tmp_path)
    try:
        result = cycle(broker, state, config, risk)
        assert result["status"] == "completed"
        assert result["reconciliation"] == "matched"
        assert result["proposals"][0]["status"] == "shadow_recorded"
        assert state.db.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 1
        assert not writes and all(name.startswith("get_") for name in raw.calls)
    finally:
        state.close()
