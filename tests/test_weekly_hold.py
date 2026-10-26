"""Frozen weekly protocol: real calendar plus synthetic, hand-computable paper markets."""

import copy
import json
import sqlite3
from datetime import date, timedelta

import pytest

from tradeagent.weekly import ledger, models
from tradeagent.weekly.engine import Engine
from tradeagent.weekly.market import momentum, quality
from tradeagent.weekly.protocol import (
    ARMS,
    BOOKS,
    CONFIG_HASH,
    UNIVERSE,
    activation_week,
    dec,
    first_session,
    instant,
    next_session,
    next_week,
    session_bounds,
    valid_quote,
)
from tradeagent.weekly.reporting import snapshot, write_reports
from tradeagent.weekly.store import Store


class Clock:
    def __init__(self, day="2026-10-26", clock="11:29"):
        self.now = instant(day, clock)

    def __call__(self):
        return self.now

    def set(self, day, clock):
        self.now = instant(day, clock)


def quote(clock, price=100):
    return {"bid": price, "ask": price, "asof": clock(), "observed_at": clock(), "active": True}


class Market:
    def __init__(self, clock):
        self.clock, self.prices, self.omit = clock, {}, set()

    def quotes(self, symbols):
        return {
            "quotes": {
                s: quote(self.clock, self.prices.get(s, 100)) for s in symbols if s not in self.omit
            },
            "evidence_kind": "synthetic",
            "observed_at": self.clock(),
        }

    def histories(self, symbols, **kwargs):
        from tradeagent.weekly.protocol import day_at, sessions

        stop = day_at(self.clock())
        if self.clock() < session_bounds(stop)[1]:
            stop -= timedelta(days=1)
        bars = [
            {
                "date": str(day),
                "open": 100,
                "high": 100,
                "low": 100,
                "close": 100,
                "volume": 1000,
                "adjusted_close": 100,
            }
            for day in sessions(stop - timedelta(days=430), stop)
        ]
        return {
            s: {"symbol": s, "bars": bars, "actions": [], "raw_hash": "synthetic"} for s in symbols
        }


def answer(packet, **kwargs):
    return {
        a: {
            "status": "VALID",
            "completed_at": packet["cutoff"] + 30,
            "selection": {
                "picks": [{"symbol": s} for s in UNIVERSE[:3]],
                "reason": "synthetic",
                "sources": [],
            },
        }
        for a in ("codex", "claude")
    }


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    with Store(
        tmp_path / "state", start_date="2026-10-26", evidence="synthetic", clock=clock
    ) as store:
        market = Market(clock)
        engine = Engine(store, market, clock=clock, assessor=answer)
        yield store, market, engine, clock


def prepare(engine, clock, day="2026-10-26"):
    clock.set(day, "11:29")
    engine.packet()
    clock.set(day, "11:30")
    engine.decision()
    clock.set(day, "12:00")
    engine.fill()


def test_frozen_equity_universe():
    assert len(UNIVERSE) == 92 and len(set(UNIVERSE)) == 92
    assert not set("SPY QQQ TLT GLD XLY XLK".split()) & set(UNIVERSE)
    assert len(CONFIG_HASH) == 64


def test_holiday_dst_and_activation():
    assert first_session(date(2027, 1, 18)) == date(2027, 1, 19)
    assert instant("2026-11-02", "11:30") - instant("2026-10-26", "11:30") == 7 * 86400 + 3600
    assert activation_week(date(2026, 10, 26)) == date(2026, 11, 2)
    assert next_session(date(2027, 1, 15)) == date(2027, 1, 19)


def test_immutable_and_lock(setup):
    store, _, _, _ = setup
    store.add("x", "test", {"v": 1})
    assert not store.add("x", "test", {"v": 1})
    with pytest.raises(ValueError, match="conflicting"):
        store.add("x", "test", {"v": 2})
    with pytest.raises(sqlite3.IntegrityError):
        store.db.execute("DELETE FROM events")
    with pytest.raises(ValueError, match="already owns"):
        Store(store.directory)


def test_no_frozen_decision_no_fill(setup):
    store, market, engine, clock = setup
    clock.set("2026-10-26", "12:00")
    engine.fill()
    assert not list(store.rows("ledger"))
    clock.set("2026-10-26", "11:31")
    with pytest.raises(ValueError, match="before frozen cutoff"):
        engine.packet()


def test_initial_five_books_and_hand_calculation(setup):
    store, _, engine, clock = setup
    prepare(engine, clock)
    expected_qty = dec("33.316674")
    for arm in ARMS[:-1]:
        state = store.state("main:" + arm)
        assert len(state["positions"]) == 3
        assert sum(ledger.shares(state, s) for s in state["positions"]) == expected_qty * 3
        assert ledger.total_cash(state) == dec(10000) - expected_qty * 3 * dec("100.05")
    spy = store.state("main:spy")
    assert ledger.shares(spy, "SPY") == dec("99.950024")
    assert len(list(store.rows("ledger"))) == 26


def test_restart_and_duplicate_no_second_fill(setup):
    store, market, engine, clock = setup
    prepare(engine, clock)
    before = list(store.rows())
    engine.fill()
    assert list(store.rows()) == before
    directory = store.directory
    store.close()
    reopened = Store(directory, evidence="synthetic", clock=clock)
    engine = Engine(reopened, market, clock=clock, assessor=answer)
    engine.fill()
    assert list(reopened.rows()) == before
    # Fixture closes this handle at teardown.
    store.db, store.lock = reopened.db, reopened.lock


def test_cross_week_hold_and_band(setup):
    store, _, engine, clock = setup
    prepare(engine, clock)
    initial = len(list(store.rows("ledger")))
    clock.set("2026-11-02", "11:29")
    engine.packet()
    clock.set("2026-11-02", "11:30")
    engine.decision()
    clock.set("2026-11-02", "12:00")
    engine.fill()
    assert len(store.state("main:codex")["positions"]) == 3
    assert not [r for r in store.rows("ledger") if r["book"] == "main:codex" and r["at"] == clock()]
    assert len(list(store.rows("ledger"))) >= initial


def test_tplusone_and_risk_deferral(setup):
    store, _, _, clock = setup
    d = date(2026, 10, 26)
    q = quote(clock)
    ledger.buy(store, "b1", "main:codex", "AAPL", 50, q, d, at=clock())
    ledger.sell(store, "s1", "main:codex", "AAPL", 50, q, d, at=clock(), reason="weekly")
    ledger.buy(store, "b2", "main:codex", "MSFT", 90, q, d, at=clock())
    assert any(
        lot["unlock"] == "2026-10-27" for lot in store.state("main:codex")["positions"]["MSFT"]
    )
    assert not ledger.sell(store, "risk", "main:codex", "MSFT", 90, q, d, at=clock(), reason="risk")
    assert ledger.sell(
        store,
        "risk",
        "main:codex",
        "MSFT",
        90,
        q,
        next_session(d),
        at=clock() + 86400,
        reason="risk",
    )
    assert not store.state("main:codex")["positions"]


def test_settled_sensitivity_cannot_spend_pending(setup):
    store, _, _, clock = setup
    d = date(2026, 10, 26)
    q = quote(clock)
    ledger.buy(store, "b1", "settled:codex", "AAPL", 99, q, d, at=clock())
    ledger.sell(store, "s1", "settled:codex", "AAPL", 99, q, d, at=clock(), reason="weekly")
    ledger.buy(store, "b2", "settled:codex", "MSFT", 99, q, d, at=clock())
    assert ledger.shares(store.state("settled:codex"), "MSFT") < 1
    ledger.buy(store, "b3", "settled:codex", "MSFT", 90, q, next_session(d), at=clock() + 86400)
    assert ledger.shares(store.state("settled:codex"), "MSFT") > 90


def test_quote_missing_terminal_no_backfill(setup):
    store, market, engine, clock = setup
    market.omit = {"AAPL"}
    prepare(engine, clock)
    assert "AAPL" not in store.state("main:codex")["positions"]
    clock.set("2026-10-26", "12:10")
    engine.fill()
    assert any(m["symbol"] == "AAPL" for m in store.rows("missing"))
    market.omit.clear()
    clock.set("2026-10-27", "12:00")
    engine.fill()
    assert "AAPL" not in store.state("main:codex")["positions"]


def test_missing_model_keeps_holdings_and_coverage(setup):
    store, _, engine, clock = setup
    prepare(engine, clock)
    before = store.state("main:codex")
    engine.assessor = lambda *a, **k: {
        "codex": {"status": "MISSING", "reason": "timeout"},
        "claude": {"status": "MISSING", "reason": "invalid JSON"},
    }
    prepare(engine, clock, "2026-11-02")
    assert store.state("main:codex") == before
    data = snapshot(store)
    assert data["coverage"]["codex"] == {"scheduled_weeks": 2, "valid_decisions": 1}


def test_risk_exit_cash_until_next_week(setup):
    store, market, engine, clock = setup
    prepare(engine, clock)
    market.prices["AAPL"] = 80
    clock.set("2026-10-27", "15:45")
    engine.history()
    engine.risk()
    assert "AAPL" not in store.state("main:codex")["positions"]
    n = len(list(store.rows("ledger")))
    engine.history()
    engine.risk()
    assert len(list(store.rows("ledger"))) == n
    assert list(store.rows("risk_exit"))


def test_split_and_dividend_pending(setup):
    store, _, _, clock = setup
    clock.set("2026-10-26", "12:00")
    ledger.buy(store, "b", "main:codex", "AAPL", 10, quote(clock), date(2026, 10, 26), at=clock())
    before = ledger.wac(store.state("main:codex"), "AAPL")
    clock.set("2026-10-27", "09:00")
    action = {"symbol": "AAPL", "date": "2026-10-27", "type": "split", "ratio": 2}
    ledger.corporate_action(store, "main:codex", action)
    assert ledger.shares(store.state("main:codex"), "AAPL") == 20
    assert ledger.wac(store.state("main:codex"), "AAPL") == before / 2
    ledger.corporate_action(store, "main:codex", action)
    assert ledger.shares(store.state("main:codex"), "AAPL") == 20
    ledger.corporate_action(
        store,
        "main:codex",
        {"symbol": "AAPL", "date": "2026-10-28", "type": "dividend", "amount": 1, "pay_date": None},
    )
    state = store.state("main:codex")
    assert state["pending"][-1]["amount"] == "20.000000"
    assert ledger.total_cash(state) - ledger.cash(state) == 20


def test_schema_future_sources_and_duplicate_selection():
    item = {
        "picks": [{"symbol": "AAPL", "reason": "x", "invalidation": "y"}],
        "reason": "x",
        "early_exit_advice": "none",
        "sources": [
            {
                "url": "https://example.com/filing",
                "published_at": "2026-10-26T10:00:00-04:00",
                "claim": "x",
            }
        ],
    }
    doc = {"accounts": {"codex": copy.deepcopy(item), "claude": copy.deepcopy(item)}}
    models.check(doc, instant("2026-10-26", "11:30"))
    doc["accounts"]["claude"]["sources"][0]["published_at"] = "2026-10-26T12:00:00-04:00"
    with pytest.raises(ValueError, match="post-cutoff"):
        models.check(doc, instant("2026-10-26", "11:30"))


def test_models_same_prompt_and_concurrent_missing_handling(setup):
    _, _, engine, clock = setup
    packet = engine.packet()
    seen = []

    def fake(text, cutoff, timeout):
        seen.append(text)
        raise TimeoutError("synthetic deadline")

    result = models.concurrent(packet, runners={"codex": fake, "claude": fake}, clock=clock)
    assert len(seen) == 2 and seen[0] == seen[1]
    assert all(r["status"] == "MISSING" for r in result.values())


def test_momentum_gap_and_exact_252_21(setup):
    _, market, _, _ = setup
    h = market.histories(["AAPL"])["AAPL"]
    previous = date.fromisoformat(h["bars"][-1]["date"])
    h["bars"][-22]["adjusted_close"] = 120
    assert momentum(h, previous) == pytest.approx(0.2)
    assert quality(h)["usable"]
    h["bars"].pop(-100)
    assert momentum(h, previous) is None
    assert not quality(h)["usable"]


def test_quote_window_validation(setup):
    _, _, _, clock = setup
    q = quote(clock)
    assert valid_quote(q, clock())
    assert not valid_quote(q, clock() + 16)
    assert not valid_quote(q, clock(), clock() + 1, clock() + 600)


def test_reports_all_five_and_missing_are_not_zero(setup):
    store, _, engine, clock = setup
    prepare(engine, clock)
    data = write_reports(store)
    assert len(data["books"]) == 10
    assert all(c["n"] == 0 and c["mean"] is None for c in data["comparisons"].values())
    assert data["weeks"][0]["returns"]["main:codex"] is None
    assert json.loads((store.directory / "status.json").read_text())["real_orders_submitted"] == 0


def test_fourteen_week_e2e_holiday_dst_risk_missing_and_restart(setup):
    store, market, engine, clock = setup
    day = date(2026, 10, 26)
    for index in range(14):
        market.omit = {"AAPL"} if index == 2 else set()
        prepare(engine, clock, str(day))
        clock.set(str(day), "12:10")
        engine.fill()
        if index == 1:
            market.prices = {s: 80 for s in UNIVERSE}
            clock.set(str(next_session(day)), "15:45")
            engine.history()
            engine.risk()
            market.prices = {}
        report_day = next_session(day) if index == 1 else day
        clock.set(str(report_day), "17:15")
        engine.valuation()
        day = next_week(day)
    data = write_reports(store)
    assert len(data["decisions"]) == 14
    assert any(d["week"] == "2027-01-19" for d in data["decisions"])
    assert data["missing"] and list(store.rows("risk_exit"))
    for book in BOOKS:
        state = store.state(book)
        assert ledger.cash(state) >= 0
        fills = [r for r in store.rows("ledger") if r["book"] == book and r.get("side")]
        hand_cash = dec(10000) + sum(
            (dec(r["notional"]) * (1 if r["side"] == "sell" else -1) for r in fills), dec(0)
        )
        assert ledger.total_cash(state) == hand_cash
        for symbol in {r["symbol"] for r in fills}:
            hand_shares = sum(
                (
                    dec(r["qty"]) * (1 if r["side"] == "buy" else -1)
                    for r in fills
                    if r["symbol"] == symbol
                ),
                dec(0),
            )
            assert ledger.shares(state, symbol) == hand_shares
        hand_nav = hand_cash + sum(
            (ledger.shares(state, symbol) * 100 for symbol in state["positions"]), dec(0)
        )
        assert float(data["books"][book]["nav"]) == pytest.approx(float(hand_nav), abs=1e-8)
        assert all(dec(lot["qty"]) > 0 for lots in state["positions"].values() for lot in lots)
    assert len(list(store.rows("ledger"))) == len({r["id"] for r in store.rows("ledger")})
    (store.directory / "e2e-result.json").write_text(
        json.dumps(
            {"weeks": 14, "holiday": "2027-01-19", "dst": "2026-11-01", "books": data["books"]},
            indent=2,
        )
    )


def test_mid_fill_interruption_resumes_exactly_once(setup, monkeypatch):
    store, _, engine, clock = setup
    clock.set("2026-10-26", "11:29")
    engine.packet()
    clock.set("2026-10-26", "11:30")
    engine.decision()
    clock.set("2026-10-26", "12:00")
    original = ledger.buy
    calls = []

    def interrupted(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("synthetic power interruption after committed fill")
        return result

    monkeypatch.setattr(ledger, "buy", interrupted)
    with pytest.raises(RuntimeError, match="power interruption"):
        engine.fill()
    assert len(list(store.rows("ledger"))) == 1
    monkeypatch.setattr(ledger, "buy", original)
    engine.fill()
    assert len(list(store.rows("ledger"))) == 26
    assert len({r["id"] for r in store.rows("ledger")}) == 26


def test_engine_settled_buy_next_session_uses_same_decision(setup):
    store, _, engine, clock = setup
    prepare(engine, clock)

    def alternate(packet, **kwargs):
        result = answer(packet)
        for arm in ("codex", "claude"):
            result[arm]["selection"]["picks"] = [{"symbol": s} for s in UNIVERSE[3:6]]
        return result

    engine.assessor = alternate
    prepare(engine, clock, "2026-11-02")
    assert list(store.rows("buy_delay"))
    before = ledger.shares(store.state("settled:codex"), UNIVERSE[3])
    clock.set("2026-11-03", "12:00")
    engine.fill()
    assert ledger.shares(store.state("settled:codex"), UNIVERSE[3]) > before
    assert len(list(store.rows("decision"))) == 2
    n = len(list(store.rows("ledger")))
    engine.fill()
    assert len(list(store.rows("ledger"))) == n


def test_entire_missing_week_stays_in_coverage(setup):
    store, _, engine, clock = setup
    clock.set("2026-11-03", "17:15")
    engine.finalize_missed()
    data = snapshot(store)
    assert data["coverage"]["codex"]["scheduled_weeks"] == 2
    assert data["coverage"]["codex"]["valid_decisions"] == 0
    assert not list(store.rows("ledger"))


def test_interrupted_model_attempt_is_not_rerun(setup):
    store, _, engine, clock = setup
    engine.packet()
    store.add("assessment-started:2026-10-26", "assessment_started", {"week": "2026-10-26"})

    def forbidden(*args, **kwargs):
        raise AssertionError("interrupted model attempt must not rerun")

    engine.assessor = forbidden
    clock.set("2026-10-26", "11:31")
    result = engine.decision()
    assert result["arms"]["codex"]["status"] == "MISSING"


def test_install_schedule_timezone_and_only_weekly_names():
    from tradeagent.weekly.deployment import units

    rendered = units("/usr/bin/python3", "/tmp/weekly")
    assert all(name.startswith("tradeagent-weekly-") for name in rendered)
    assert all(
        "America/New_York" in text for name, text in rendered.items() if name.endswith(".timer")
    )
    assert "Persistent=false" in rendered["tradeagent-weekly-fill.timer"]
    assert "11:30..54" in rendered["tradeagent-weekly-decision.timer"]


def test_public_projection_rejects_synthetic_and_preserves_all_five(setup):
    from tradeagent.weekly.reporting import read_public

    store, _, _, _ = setup
    write_reports(store)
    with pytest.raises(ValueError, match="prospective"):
        read_public(store.directory)
    from tradeagent.weekly.web import asset

    assert b"weekly.json" in asset("weekly.js")
    assert b"12" in asset("weekly.js")


def test_no_early_daily_valuation(setup):
    _, _, engine, _ = setup
    with pytest.raises(ValueError, match="17:15"):
        engine.valuation()


def test_weighted_average_basis_survives_partial_sale(setup):
    store, _, _, clock = setup
    day = date(2026, 10, 26)
    ledger.buy(store, "b1", "main:codex", "AAPL", 10, quote(clock, 100), day, at=clock())
    ledger.buy(store, "b2", "main:codex", "AAPL", 10, quote(clock, 200), day, at=clock())
    average = ledger.wac(store.state("main:codex"), "AAPL")
    assert average == dec("150.075")
    ledger.sell(
        store, "s1", "main:codex", "AAPL", 5, quote(clock, 180), day, at=clock(), reason="weekly"
    )
    assert ledger.wac(store.state("main:codex"), "AAPL") == average


def test_journal_refuses_regressing_clock(setup):
    store, _, _, clock = setup
    store.add("first-clock", "test", {"v": 1})
    clock.now -= 1
    with pytest.raises(ValueError, match="clock regressed"):
        store.add("second-clock", "test", {"v": 2})
