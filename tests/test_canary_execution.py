import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from test_broker_contract import NOW, RawReadFake
from test_execution_lifecycle import approval, equity, harness
from test_release_policy import facts, histories, tiny_snapshot

from tradeagent.broker import Broker
from tradeagent.canary import CanarySelector, canary_known_action_check
from tradeagent.codex_bridge import SERVER, CodexBridge
from tradeagent.model import MAX_FUTURE_SKEW_SECONDS, Config, Halt, Intent, Risk, dec
from tradeagent.risk import check_order
from tradeagent.schema import Contracts
from tradeagent.simulator import SimClock


def receipt_snapshot(monkeypatch, offset):
    start, receipt = NOW.timestamp(), NOW.timestamp() + 1
    moments = iter((start, receipt))

    class ReceiptClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(next(moments), tz)

    monkeypatch.setattr("tradeagent.broker.datetime", ReceiptClock)
    raw = RawReadFake()
    stamp = datetime.fromtimestamp(receipt + offset, timezone.utc).isoformat()
    quote = raw.payloads["get_equity_quotes"]["results"][0]["quote"]
    for field in ("venue_last_trade_time", "venue_bid_time", "venue_ask_time"):
        quote[field] = stamp
    raw.payloads["get_equity_price_book"]["books"][0]["updated_at"] = stamp
    config, risk = Config(allowed_symbols=["SPY"]), Risk()
    snapshot = Broker(raw, config, risk).snapshot()
    return snapshot, config, risk, start, receipt


@pytest.mark.parametrize("offset", [-0.5, 0.125, MAX_FUTURE_SKEW_SECONDS])
def test_inflight_and_bounded_postreceipt_market_times_pass(monkeypatch, offset):
    s, config, risk, start, receipt = receipt_snapshot(monkeypatch, offset)
    assert s.asof == start  # Old account reads do not gain a fresh asof.
    assert s.regular_session
    assert s.quote_times["SPY"] > start
    check_order(Intent("SPY", "buy", dec(1), dec("100.01")), s, config, risk, receipt, s.nav)


@pytest.mark.parametrize("offset", [MAX_FUTURE_SKEW_SECONDS + 0.001, -120.001])
def test_future_beyond_cap_and_stale_market_times_fail(monkeypatch, offset):
    s, config, risk, _, receipt = receipt_snapshot(monkeypatch, offset)
    assert not s.regular_session
    with pytest.raises(Halt):
        check_order(Intent("SPY", "buy", dec(1), dec("100.01")), s, config, risk, receipt, s.nav)


def test_validation_receipt_calendar_still_blocks_after_close(monkeypatch):
    start = NOW.replace(hour=19, minute=59, second=59)
    receipt = start + timedelta(seconds=1)
    moments = iter((start, receipt))

    class ReceiptClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(moments).astimezone(tz)

    monkeypatch.setattr("tradeagent.broker.datetime", ReceiptClock)
    raw = RawReadFake()
    quote = raw.payloads["get_equity_quotes"]["results"][0]["quote"]
    quote["venue_last_trade_time"] = start.isoformat()
    assert not Broker(raw, Config(allowed_symbols=["SPY"]), Risk()).snapshot().regular_session


def checks(clock, entries=None):
    return [
        {
            "symbol": "TINY",
            "source": url,
            "asof": clock(),
            "sha256": "a" * 64,
            "parsed": True,
            "entries": [] if entries is None else entries,
        }
        for url in (
            "https://www.nasdaqtrader.com/Trader.aspx?id=nasdaq-security-status-updates",
            "https://www.nasdaqtrader.com/Trader.aspx?id=nasdaq-ex-date",
        )
    ]


def test_available_action_check_only_allows_one_share_canary_not_production():
    c = SimClock()
    original = replace(facts(c)["TINY"], corporate_action_clear=None)
    checked = canary_known_action_check(original, checks(c), c())
    assert checked.corporate_action_clear is None  # No commercial coverage claim.
    with pytest.raises(Halt):
        checked.validate(c())
    checked.validate(c(), canary=True)
    selected, _ = CanarySelector().select(
        tiny_snapshot(c),
        Config(allowed_symbols=["TINY"]),
        Risk(),
        {"TINY": checked},
        histories(c),
        c(),
        dec(100),
    )
    assert selected.quantity == 1 and selected.limit_price <= dec(3)


@pytest.mark.parametrize(
    "fault", ["observed_action", "unparsed", "missing_source", "stale", "mismatch", "positive_flag"]
)
def test_positive_actions_ambiguity_or_missing_checks_fail_closed(fault):
    c = SimClock()
    original = replace(facts(c)["TINY"], corporate_action_clear=None)
    available = checks(c)
    if fault == "observed_action":
        available[0]["entries"] = ["split or other action ambiguity"]
    elif fault == "unparsed":
        available[0]["parsed"] = False
    elif fault == "missing_source":
        available.pop()
    elif fault == "stale":
        available[0]["asof"] -= 86401
    elif fault == "mismatch":
        available[0]["symbol"] = "OTHER"
    elif fault == "positive_flag":
        original = replace(original, corporate_action_clear=False)
    with pytest.raises(Halt):
        canary_known_action_check(original, available, c())


def test_unknown_instrument_is_not_rescued_by_no_reported_actions():
    c = SimClock()
    checked = canary_known_action_check(
        replace(facts(c)["TINY"], ordinary_common=None, corporate_action_clear=None), checks(c), c()
    )
    with pytest.raises(Halt):
        checked.validate(c(), canary=True)


def authorized_bridge(path):
    b = CodexBridge(path, allow_equity_review=True)
    b.tools = copy.deepcopy(Contracts().tools)
    b.inventory = {"data": [{"name": SERVER, "serverInfo": {"version": "1.6.2"}}]}
    b.thread_id = "synthetic-local"
    return b


def args():
    return {
        "account_number": "synthetic-only",
        **Intent("TINY", "buy", dec(1), dec("2.32")).payload(),
    }


def test_one_nonmutating_review_timeout_consumes_allowance_and_never_retries(tmp_path):
    b = authorized_bridge(tmp_path)
    calls = []

    def lost_response(method, params):
        calls.append(params["tool"])
        raise TimeoutError("synthetic response loss")

    b._exchange = lost_response
    with pytest.raises(TimeoutError):
        b.review_equity_once(args())
    with pytest.raises(Halt, match="consumed"):
        b.review_equity_once(args())
    assert calls == b.calls == ["review_equity_order"]
    with pytest.raises(Halt):
        b.execution_call("place_equity_order", args())
    with pytest.raises(Halt):
        b.rpc("mcpServer/tool/call", {"server": SERVER, "tool": "place_equity_order"})
    assert calls == ["review_equity_order"]


@pytest.mark.parametrize(
    "change",
    [
        {"quantity": "2"},
        {"side": "sell"},
        {"limit_price": "3.01"},
        {"market_hours": "extended_hours"},
        {"time_in_force": "gtc"},
    ],
)
def test_review_capability_cannot_be_used_for_other_orders(tmp_path, change):
    b = authorized_bridge(tmp_path)
    with pytest.raises(Halt):
        b.review_equity_once({**args(), **change})
    assert not b.calls and not b.equity_review_consumed


def test_normal_connection_has_no_review_permission(tmp_path):
    with pytest.raises(Halt, match="not authorized"):
        CodexBridge(tmp_path).review_equity_once(args())


@pytest.mark.parametrize(
    "receipt", ["approval", "missing", "unknown_state", "wrong_session", "unknown_shape"]
)
def test_equity_non_ack_receipt_is_unknown_halt_and_never_retry(tmp_path, receipt):
    with harness(tmp_path, "accepted") as (e, sim, state, _):
        intent = equity()
        plan = e.prepare(intent, "synthetic-bar")
        original = sim.invoke

        def response(name, arguments):
            result = original(name, arguments)
            if name != "place_equity_order":
                return result
            if receipt == "approval":
                result["data"]["order"] = None
                result["guide"] = "Approval required; manually place in app."
            elif receipt == "missing":
                result["data"]["order"] = None
            elif receipt == "unknown_state":
                result["data"]["order"]["state"] = "unrecognized_status"
            elif receipt == "wrong_session":
                result["data"]["order"]["market_hours"] = "extended_hours"
            else:
                result["data"]["unrecognized"] = True
            return result

        sim.invoke = response
        with pytest.raises(Halt, match="unknown"):
            e.execute(plan["key"], intent, approval(plan))
        assert state.db.execute("SELECT status FROM intents").fetchone()[0] == "unknown"
        with pytest.raises(Halt):
            e.execute(plan["key"], intent, approval(plan))
        assert sum(c["tool"] == "place_equity_order" for c in sim.calls) == 1


def test_explicit_valid_ack_reconciles_without_claiming_fill(tmp_path):
    with harness(tmp_path, "accepted") as (e, sim, _, _):
        intent = equity()
        plan = e.prepare(intent, "synthetic-bar")
        result = e.execute(plan["key"], intent, approval(plan))
        assert result["status"] == "pending" and result["filled"] == "0"
        assert sum(c["tool"] == "place_equity_order" for c in sim.calls) == 1
