import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from robinhood_agent.broker import Broker
from robinhood_agent.model import Config, Halt, Risk

NOW = datetime(2026, 10, 5, 18, tzinfo=timezone.utc)


class RawReadFake:
    def __init__(self):
        quote = {
            "symbol": "SPY",
            "state": "active",
            "has_traded": True,
            "last_trade_price": "100",
            "venue_last_trade_time": (NOW - timedelta(seconds=1)).isoformat(),
            "last_non_reg_trade_price": None,
            "bid_price": "99.99",
            "ask_price": "100.01",
            "venue_bid_time": NOW.isoformat(),
            "venue_ask_time": NOW.isoformat(),
        }
        self.payloads = {
            "get_accounts": {
                "accounts": [
                    {
                        "account_number": "fake-only-not-a-real-account",
                        "agentic_allowed": True,
                        "type": "cash",
                        "brokerage_account_type": "individual",
                        "state": "active",
                        "deactivated": False,
                        "permanently_deactivated": False,
                    }
                ]
            },
            "get_portfolio": {
                **{
                    k: "0"
                    for k in (
                        "options_value",
                        "futures_value",
                        "event_contracts_value",
                        "crypto_value",
                        "mutual_funds_value",
                        "fixed_income_value",
                        "pending_deposits",
                        "equity_value",
                    )
                },
                "currency": "USD",
                "total_value": "10000",
                "cash": "10000",
                "buying_power": {
                    "buying_power": "10000",
                    "unleveraged_buying_power": "9000",
                    "display_currency": "USD",
                },
            },
            "get_equity_positions": {"positions": [], "next": ""},
            "get_equity_orders": {"orders": [], "next": ""},
            "get_equity_quotes": {"results": [{"quote": quote}]},
            "get_equity_tradability": {
                "results": [{"symbol": "SPY", "tradeable": True, "state": "active"}]
            },
            "get_equity_price_book": {
                "books": [
                    {
                        "symbol": "SPY",
                        "updated_at": NOW.isoformat(),
                        "bids": [{"price": "99.99", "quantity": 10000}],
                        "asks": [{"price": "100.01", "quantity": 10000}],
                    }
                ]
            },
            "get_equity_historicals": {
                "results": [
                    {
                        "symbol": "SPY",
                        "interval": "day",
                        "bounds": "regular",
                        "bars": [self.bar(NOW - timedelta(days=219 - i)) for i in range(220)],
                    }
                ]
            },
        }
        self.calls = []

    def bar(self, day):
        return {
            "begins_at": day.replace(hour=13, minute=30).isoformat(),
            "session": "reg",
            "open_price": "99",
            "high_price": "101",
            "low_price": "98",
            "close_price": "100",
            "interpolated": False,
            "volume": 10,
        }

    def read(self, name, arguments=None):
        self.calls.append(name)
        return {"data": copy.deepcopy(self.payloads[name])}


@pytest.fixture
def raw():
    return RawReadFake()


def broker(raw):
    return Broker(raw, Config(allowed_symbols=["SPY"]), Risk())


def test_normalized_snapshot_and_no_identifiers(raw):
    b = broker(raw)
    s = b.snapshot(NOW.timestamp())
    assert s.nav == D(10000)
    assert s.buying_power == D(9000)  # Never use leverage in authoritative spendable value.
    assert s.regular_session
    assert s.positions == {} and s.orders == []
    assert "fake-only" not in repr(s) and "fake-only" not in repr(b.account_scope)
    assert set(raw.calls) <= {
        "get_accounts",
        "get_portfolio",
        "get_equity_positions",
        "get_equity_orders",
        "get_equity_quotes",
        "get_equity_tradability",
        "get_equity_price_book",
    }


@pytest.mark.parametrize(
    "field", ["pending_deposits", "options_value", "crypto_value", "futures_value"]
)
def test_other_assets_and_pending_funds_fail_closed(raw, field):
    raw.payloads["get_portfolio"][field] = "1"
    with pytest.raises(Halt, match="unsupported holdings"):
        broker(raw).snapshot(NOW.timestamp())


def test_ambiguous_account_rejected(raw):
    raw.payloads["get_accounts"]["accounts"].append(
        {**raw.payloads["get_accounts"]["accounts"][0], "account_number": "second-fake-only"}
    )
    with pytest.raises(Halt, match="exactly one"):
        broker(raw).snapshot(NOW.timestamp())


def test_missing_quotes_rejected(raw):
    raw.payloads["get_equity_quotes"]["results"] = []
    with pytest.raises(Halt, match="coverage"):
        broker(raw).snapshot(NOW.timestamp())


def test_fills_must_reconcile(raw):
    raw.payloads["get_equity_orders"]["orders"] = [
        {"id": "fake-order", "state": "filled", "cumulative_quantity": "1", "executions": []}
    ]
    with pytest.raises(Halt, match="cumulative fills"):
        broker(raw).snapshot(NOW.timestamp())


def test_history_ignores_incomplete_and_interpolated(raw):
    bars = raw.payloads["get_equity_historicals"]["results"][0]["bars"]
    bars[0]["interpolated"] = True
    result = broker(raw).histories(NOW.timestamp())["SPY"]
    assert len(result) == 218
    assert result[-1]["begins_at"].startswith("2026-10-04")


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda bars: bars.reverse(), "out-of-order"),
        (lambda bars: bars[-1].update(close_price="-1"), "invalid historical"),
        (lambda bars: bars.__delitem__(slice(0, 20)), "insufficient"),
    ],
)
def test_bad_history_rejected(raw, change, reason):
    bars = raw.payloads["get_equity_historicals"]["results"][0]["bars"]
    # Change a completed bar for the invalid-price case, not today's unfinished bar.
    bars.pop()
    change(bars)
    with pytest.raises(Halt, match=reason):
        broker(raw).histories(NOW.timestamp())


def test_history_stale(raw):
    bars = raw.payloads["get_equity_historicals"]["results"][0]["bars"]
    del bars[-8:]
    with pytest.raises(Halt, match="stale"):
        broker(raw).histories(NOW.timestamp())


def test_pagination_collects_all_pages(raw):
    b = broker(raw)
    b.accounts()

    def pages(name, args):
        assert name == "get_equity_positions"
        if "cursor" not in args:
            return {"data": {"positions": [{"symbol": "SPY"}], "next": "page-two"}}
        assert args["cursor"] == "page-two"
        return {"data": {"positions": [{"symbol": "QQQ"}], "next": ""}}

    raw.read = pages
    assert [row["symbol"] for row in b.paged("get_equity_positions", "positions")] == ["SPY", "QQQ"]
