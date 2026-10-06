from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from test_broker_contract import NOW, RawReadFake

from tradeagent.account_policy import POLICY, capital, eligible
from tradeagent.broker import Broker
from tradeagent.model import Config, Halt, Risk, dec, load_config
from tradeagent.risk import check_order
from tradeagent.simulator import SimClock, funded_snapshot
from tradeagent.strategy import STATUS, StrategySignal
from tradeagent.strategy import TestTrendStrategy as TrendStrategy


@pytest.mark.parametrize(
    "hour,expected_last_day", [(14, "2026-10-04"), (18, "2026-10-04"), (21, "2026-10-05")]
)
def test_midnight_utc_daily_candle_is_incomplete_until_exchange_close(hour, expected_last_day):
    bridge = RawReadFake()
    bars = bridge.payloads["get_equity_historicals"]["results"][0]["bars"]
    for bar in bars:
        date = datetime.fromisoformat(bar["begins_at"]).date()
        bar["begins_at"] = date.isoformat() + "T00:00:00Z"
    observed = NOW.replace(hour=hour)
    history = Broker(bridge, Config(allowed_symbols=["SPY"]), Risk()).histories(
        observed.timestamp()
    )["SPY"]
    assert history[-1]["begins_at"].startswith(expected_last_day)


def test_small_funded_shadow_records_no_broker_writes_and_suppresses_repeat(tmp_path, monkeypatch):
    import time

    from tradeagent.runner import cycle
    from tradeagent.state import State

    monkeypatch.setattr(time, "time", lambda: NOW.timestamp())
    bridge = RawReadFake()
    portfolio = bridge.payloads["get_portfolio"]
    portfolio.update(total_value="100", cash="100")
    portfolio["buying_power"].update(buying_power="100", unleveraged_buying_power="100")
    bridge.payloads["get_accounts"]["accounts"][0].update(
        type="limited_margin", management_type="self_directed"
    )
    quote = bridge.payloads["get_equity_quotes"]["results"][0]["quote"]
    quote.update(symbol="OPEN", last_trade_price="2.31", ask_price="2.32", bid_price="2.31")
    bridge.payloads["get_equity_tradability"]["results"][0]["symbol"] = "OPEN"
    book = bridge.payloads["get_equity_price_book"]["books"][0]
    book.update(
        symbol="OPEN",
        bids=[{"price": "2.31", "quantity": 10000}],
        asks=[{"price": "2.32", "quantity": 10000}],
    )
    historical = bridge.payloads["get_equity_historicals"]["results"][0]
    historical["symbol"] = "OPEN"
    for i, bar in enumerate(historical["bars"]):
        price = dec(1) + dec(i) / 100
        bar.update(
            open_price=str(price),
            low_price=str(price),
            high_price=str(price),
            close_price=str(price),
        )
    config = Config(allowed_symbols=["OPEN"])
    broker = Broker(bridge, config, Risk())
    native_snapshot = broker.snapshot
    broker.snapshot = lambda: native_snapshot(NOW.timestamp())
    writes = []

    def forbid(*args):
        writes.append(args)
        raise AssertionError("SHADOW attempted a broker write")

    broker.review = broker.submit = broker.cancel = forbid
    state = State(tmp_path)
    try:
        first = cycle(broker, state, config, Risk())
        second = cycle(broker, state, config, Risk())
        assert first["status"] == second["status"] == "completed"
        assert first["proposals"][0]["status"] == "shadow_recorded"
        assert first["proposals"][0]["payload"]["quantity"] == "2"
        assert second["proposals"][0]["status"] == "duplicate_suppressed"
        assert not writes and all(name.startswith("get_") for name in bridge.calls)
        assert state.db.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 1
    finally:
        state.close()


@pytest.mark.parametrize("account_type", ["cash", "limited_margin"])
def test_explicit_self_directed_agentic_account_is_eligible(account_type):
    bridge = RawReadFake()
    bridge.payloads["get_accounts"]["accounts"][0].update(
        type=account_type, management_type="self_directed"
    )
    snapshot = Broker(bridge, Config(allowed_symbols=["SPY"]), Risk()).snapshot(NOW.timestamp())
    assert snapshot.account_policy == POLICY and snapshot.agentic_eligible
    assert snapshot.account_type == account_type


@pytest.mark.parametrize("management", ["managed", "advised", "discretionary", "unknown"])
def test_managed_and_unknown_management_remain_ineligible(management):
    account = RawReadFake().payloads["get_accounts"]["accounts"][0]
    account["management_type"] = management
    with pytest.raises(Halt, match="dedicated"):
        eligible(account)


def test_small_balance_profile_preserves_all_risk_and_release_defaults():
    config, risk = load_config(
        Path("config/small-balance-shadow.example.json"), Path("config/risk.example.json")
    )
    assert config.mode == "SHADOW" and not config.supervised_enabled and not config.live_enabled
    assert config.target_fraction == "0.05" and config.allowed_symbols == ["OPEN"]
    assert config.strategy_version == Config().strategy_version
    assert risk == Risk()


def small_snapshot(clock, ask="2.32", bid="2.31"):
    snapshot = funded_snapshot(clock)
    snapshot.nav = snapshot.cash = snapshot.buying_power = dec(100)
    snapshot.prices = {"OPEN": dec(bid)}
    snapshot.asks = {"OPEN": dec(ask)}
    snapshot.bids = {"OPEN": dec(bid)}
    snapshot.quote_times = snapshot.ask_times = snapshot.bid_times = {"OPEN": clock()}
    snapshot.tradable = {"OPEN": True}
    snapshot.liquidity = {"OPEN": {"asof": clock(), "bid_size": 10000, "ask_size": 10000}}
    snapshot.option_quotes = {}
    return snapshot


@pytest.mark.parametrize("score,quantity", [(2, "2"), (0, None), (-2, None)])
def test_small_balance_does_not_force_a_signal(score, quantity):
    clock = SimClock()
    config = Config(allowed_symbols=["OPEN"])
    direction = "long" if score > 0 else "exit" if score < 0 else "hold"
    signal = StrategySignal(
        "OPEN",
        "2026-10-02T13:30:00Z",
        score,
        direction,
        "synthetic-signal-only-for-test",
        config.strategy_version,
    )
    strategy = TrendStrategy()
    snapshot = small_snapshot(clock)
    proposal = strategy.proposal(signal, snapshot, config)
    assert signal.status == STATUS
    if quantity is None:
        assert proposal is None
    else:
        assert proposal.quantity == dec(quantity) and proposal.limit_price == dec("2.32")
        assert proposal.quantity * proposal.limit_price == dec("4.64")
        check_order(proposal, snapshot, config, Risk(), clock(), 100)


def test_whole_share_affordability_never_expands_five_percent_target():
    clock = SimClock()
    config = Config(allowed_symbols=["OPEN"])
    signal = StrategySignal(
        "OPEN", "2026-10-02T13:30:00Z", 2, "long", "synthetic", config.strategy_version
    )
    assert TrendStrategy().proposal(signal, small_snapshot(clock, "5.01", "5.00"), config) is None


@pytest.mark.parametrize("gate", ["stale", "spread", "depth", "closed"])
def test_small_balance_preserves_market_risk_gates(gate):
    clock = SimClock()
    snapshot = small_snapshot(clock)
    config = Config(allowed_symbols=["OPEN"])
    signal = StrategySignal(
        "OPEN", "2026-10-02T13:30:00Z", 2, "long", "synthetic", config.strategy_version
    )
    proposal = TrendStrategy().proposal(signal, snapshot, config)
    if gate == "stale":
        snapshot.quote_times = {"OPEN": clock() - 121}
    elif gate == "spread":
        snapshot.bids = {"OPEN": dec("2.00")}
    elif gate == "depth":
        snapshot.liquidity["OPEN"]["ask_size"] = 99
    else:
        snapshot.regular_session = False
    with pytest.raises(Halt):
        check_order(proposal, snapshot, config, Risk(), clock(), 100)


def test_pending_funding_is_excluded_from_small_balance_capital():
    portfolio = RawReadFake().payloads["get_portfolio"]
    portfolio.update(cash="100", pending_deposits="100", total_value="100")
    portfolio["buying_power"].update(buying_power="100", unleveraged_buying_power="100")
    assert capital(portfolio) == dec(0)
    bridge = RawReadFake()
    bridge.payloads["get_portfolio"] = portfolio
    bridge.payloads["get_accounts"]["accounts"][0]["management_type"] = "self_directed"
    with pytest.raises(Halt, match="unsettled deposit"):
        Broker(bridge, replace(Config(), allowed_symbols=["SPY"]), Risk()).snapshot(NOW.timestamp())


def test_terminal_history_cannot_claim_filled_with_only_partial_executions():
    bridge = RawReadFake()
    bridge.payloads["get_equity_orders"]["orders"] = [
        {
            "id": "synthetic-history",
            "symbol": "SPY",
            "side": "buy",
            "type": "limit",
            "state": "filled",
            "quantity": "2",
            "cumulative_quantity": "1",
            "price": "100.01",
            "executions": [
                {
                    "id": "synthetic-fill",
                    "quantity": "1",
                    "price": "100.01",
                    "fees": "0",
                    "timestamp": NOW.isoformat(),
                }
            ],
        }
    ]
    with pytest.raises(Halt, match="filled equity order has incomplete"):
        Broker(bridge, Config(allowed_symbols=["SPY"]), Risk()).snapshot(NOW.timestamp())
