"""Economic correctness tests use explicitly synthetic minute fixtures."""

import copy
import json
from dataclasses import asdict

import pytest

from tradeagent.research.domain import Bar
from tradeagent.research.historical import (
    COSTS,
    FAMILIES,
    calendar,
    choose_validation,
    evaluate_sessions,
    main,
    metrics,
    net_return,
    observations,
    run,
)
from tradeagent.research.historical_data import HistoricalInputs, IndexedHistory
from tradeagent.research.replay import decision_snapshot
from tradeagent.strategy import DEFAULT_PARAMS, BaselineStrategy


@pytest.fixture
def dataset():
    sessions = calendar("2025-01-06", "2025-01-16")
    bars = {}
    for i, session in enumerate(sessions):
        for j, symbol in enumerate(("QQQ", "IWM", "SPY")):
            previous = session["previous_close"]
            base = 100 + j * 50
            bars[symbol, previous - 60] = asdict(
                Bar(symbol, previous - 60, previous, previous, base, base, base, base, 1000)
            )
            price = base * (1.003 if symbol != "SPY" else 1)
            for minute in range(int((session["close"] - session["open"]) / 60)):
                start = session["open"] + minute * 60
                move = 0.002 if minute < 3 and symbol != "SPY" else (1 if i % 2 else -1) * 0.00002
                close = price * (1 + move)
                bars[symbol, start] = asdict(
                    Bar(
                        symbol,
                        start,
                        start + 60,
                        start + 60,
                        price,
                        max(price, close) * 1.0001,
                        min(price, close) * 0.9999,
                        close,
                        1000,
                    )
                )
                price = close
    return {
        "metadata": {
            "source": "fixture-alpha-tests",
            "frequency": "1Min",
            "adjustment": "raw",
            "availability": "synthetic_fixture",
        },
        "bars": list(bars.values()),
    }


def inputs(tmp_path, dataset):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(dataset))
    return HistoricalInputs(tmp_path / "cache", ["QQQ", "IWM", "SPY"], input_path=path)


def test_calendar_supports_years_dst_and_early_closes():
    sessions = calendar("2022-01-01", "2025-01-01")
    assert len(sessions) > 750
    assert any(s["close"] - s["open"] == 210 * 60 for s in sessions)
    assert len({s["open"] % 86400 for s in sessions}) == 2


def test_costs_and_compounding_are_economic():
    assert net_return(100, 110, COSTS["base"]) == pytest.approx(
        110 * (1 - 0.00021) / (100 * 1.00021) - 1
    )
    assert (
        net_return(100, 100, COSTS["stress"])
        < net_return(100, 100, COSTS["base"])
        < net_return(100, 100, COSTS["low"])
        < 0
    )
    value = metrics([-0.1, 0.2, -0.05])
    assert value["total_return"] == pytest.approx(0.9 * 1.2 * 0.95 - 1)
    assert value["max_drawdown"] == pytest.approx(-0.1)
    assert value["annualized_return"] is None
    assert metrics([0, 0])["sharpe"] is None
    assert metrics([])["total_return"] is None


def test_future_data_cannot_change_predictions(dataset):
    session = calendar("2025-01-06", "2025-01-16")[0]
    history = IndexedHistory(dataset)
    snap = decision_snapshot(history, session, "QQQ", "SPY", session["decision"])
    strategy = BaselineStrategy("opening_momentum", "v1", "opening_momentum", DEFAULT_PARAMS)
    expected = strategy.predict(snap, session["decision"])
    changed = copy.deepcopy(dataset)
    for b in changed["bars"]:
        if b["start"] >= session["decision"]:
            for field in ("open", "high", "low", "close"):
                b[field] *= 10
    new = decision_snapshot(IndexedHistory(changed), session, "QQQ", "SPY", session["decision"])
    assert strategy.predict(new, session["decision"]) == expected


def test_entry_is_later_open_exit_is_open_not_known_close(dataset, tmp_path):
    session = calendar("2025-01-06", "2025-01-16")[0]
    for b in dataset["bars"]:
        if b["symbol"] == "QQQ" and b["start"] == session["decision"] + 60:
            b.update(open=150, high=151, low=149, close=150)
        if b["symbol"] == "QQQ" and b["start"] == session["decision"] + 3660:
            b.update(open=160, high=301, low=159, close=300)
    obs = observations(IndexedHistory(dataset), session, ["QQQ", "IWM"], "SPY")
    assert obs["QQQ"]["entry"] == 150
    assert obs["QQQ"]["exit"] == 160
    days, trades, _ = evaluate_sessions(inputs(tmp_path, dataset), [session], ["QQQ", "IWM"], "SPY")
    assert days
    assert all(t["signal_end"] < t["entry_time"] < t["exit_time"] for t in trades)
    assert all(t["exit_time"] - t["entry_time"] == 3600 for t in trades)
    trade = next(t for t in trades if t["family"] == "opening_momentum" and t["symbol"] == "QQQ")
    assert trade["gross_return"] == pytest.approx(160 / 150 - 1)


def test_bearish_predictions_have_no_economic_credit(dataset, tmp_path):
    session = calendar("2025-01-06", "2025-01-16")[0]
    days, trades, skipped = evaluate_sessions(
        inputs(tmp_path, dataset), [session], ["QQQ", "IWM"], "SPY"
    )
    assert not skipped
    reversal = days[0]["strategies"]["opening_reversal"]
    assert reversal["directions"]["bearish_no_trade"] == 2
    assert reversal["gross"] == reversal["base"] == reversal["trades"] == 0
    assert not any(t["family"] == "opening_reversal" for t in trades)
    active = days[0]["strategies"]["opening_momentum"]
    assert active["capital_utilization"] == 1
    assert active["market_time_exposure"] == pytest.approx(60 / 390)


def test_missing_path_is_unavailable_not_cash(dataset, tmp_path):
    session = calendar("2025-01-06", "2025-01-16")[0]
    dataset["bars"] = [
        b
        for b in dataset["bars"]
        if not (b["symbol"] == "QQQ" and b["start"] == session["decision"] + 120)
    ]
    days, trades, skipped = evaluate_sessions(
        inputs(tmp_path, dataset), [session], ["QQQ", "IWM"], "SPY"
    )
    assert days == trades == []
    assert len(skipped) == 1


def test_late_signal_does_not_enter_snapshot(dataset, tmp_path):
    session = calendar("2025-01-06", "2025-01-16")[0]
    for b in dataset["bars"]:
        if b["symbol"] == "QQQ" and b["end"] == session["decision"]:
            b["available_at"] += 1
    days, _, skipped = evaluate_sessions(
        inputs(tmp_path, dataset), [session], ["QQQ", "IWM"], "SPY"
    )
    assert not days and skipped


def test_holdout_frozen_before_test_and_fixture_labeled(dataset, tmp_path, monkeypatch):
    path = tmp_path / "minutes.json"
    path.write_text(json.dumps(dataset))
    output = tmp_path / "result"
    original = evaluate_sessions

    def checked(inputs, sessions, symbols, benchmark):
        if sessions[0]["date"] >= "2025-01-14":
            frozen = json.loads((output / "selection.json").read_text())
            assert frozen["test_evaluated"] is False
        return original(inputs, sessions, symbols, benchmark)

    monkeypatch.setattr("tradeagent.research.historical.evaluate_sessions", checked)
    result = run(
        output,
        "2025-01-06",
        "2025-01-09",
        "2025-01-14",
        "2025-01-16",
        input_path=path,
        registry=tmp_path / "attempts.jsonl",
    )
    assert result["evidence_kind"] == "synthetic_fixture_implementation_only"
    assert result["further_investigation"] == []
    dates = {
        name: {
            d["date"]
            for d in json.loads((output / "daily.json").read_text())
            if d["period"] == name
        }
        for name in ("train", "validation", "test")
    }
    assert (
        max(dates["train"])
        < min(dates["validation"])
        <= max(dates["validation"])
        < min(dates["test"])
    )
    assert not list(output.glob("*.sqlite*"))
    assert (output / "trades.csv").exists()
    with pytest.raises(ValueError, match="fresh"):
        run(output, "2025-01-06", "2025-01-09", "2025-01-14", "2025-01-16", input_path=path)


def test_validation_selection_uses_only_validation():
    summary = {
        "complete_sessions": 2,
        "strategies": {f: {"base": {"total_return": -0.01}} for f in FAMILIES},
    }
    assert choose_validation(summary) == "cash"
    summary["strategies"]["mean_reversion"]["base"]["total_return"] = 0.01
    assert choose_validation(summary) == "mean_reversion"


def test_adjustments_duplicates_and_future_range_rejected(dataset):
    dataset["metadata"]["adjustment"] = "all"
    with pytest.raises(ValueError, match="split-only"):
        IndexedHistory(dataset)
    dataset["metadata"]["adjustment"] = "split"
    dataset["bars"].append(dataset["bars"][0])
    with pytest.raises(ValueError, match="duplicate"):
        IndexedHistory(dataset)
    with pytest.raises(ValueError, match="today"):
        calendar("2099-01-01", "2099-02-01")


def test_cli_error_is_nonzero(tmp_path, capsys):
    assert (
        main(
            [
                "--start",
                "2025-01-01",
                "--validation-start",
                "2025-03-01",
                "--test-start",
                "2025-02-01",
                "--end",
                "2025-04-01",
                "--output",
                str(tmp_path / "result"),
            ]
        )
        == 2
    )
    assert "chronological" in capsys.readouterr().out


def test_passive_basket_drifts_instead_of_daily_rebalancing():
    from tradeagent.research.historical import benchmark_returns

    days = [
        {"passive_by_symbol": {"A": 1.0, "B": 0.0}},
        {"passive_by_symbol": {"A": 0.0, "B": 1.0}},
    ]
    returns = benchmark_returns(days)
    side = sum(COSTS["base"].values()) / 10000
    assert (1 + returns[0]) * (1 + returns[1]) == pytest.approx(2 * (1 - side) / (1 + side))


def test_multi_year_fetch_uses_monthly_chunks(tmp_path, monkeypatch, dataset):
    requests = []

    def fetch(self, symbols, start, end):
        requests.append((start, end))
        return dataset

    monkeypatch.setattr("tradeagent.research.historical_data.AlpacaHistory.fetch", fetch)
    source = HistoricalInputs(tmp_path, ["QQQ", "IWM", "SPY"])
    chunks = list(source.chunks(calendar("2022-01-01", "2025-01-01")))
    assert len(requests) == 36
    assert sum(len(rows) for _, rows in chunks) > 750
    assert all(end - start < 35 * 86400 for start, end in requests)


def test_unavailable_data_writes_honest_report_and_counts_attempt(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("historical data unavailable: missing test credentials")

    monkeypatch.setattr("tradeagent.research.historical_data.AlpacaHistory.fetch", fail)
    registry = tmp_path / "search.jsonl"
    output = tmp_path / "unavailable"
    with pytest.raises(ValueError, match="missing test credentials"):
        run(output, "2025-01-06", "2025-01-09", "2025-01-14", "2025-01-16", registry=registry)
    result = json.loads((output / "results.json").read_text())
    assert result["evidence_kind"] == "unavailable"
    assert result["periods"] == {}
    assert "unavailable" in (output / "REPORT.md").read_text()
    assert len(registry.read_text().splitlines()) == 1


def test_changing_final_prices_cannot_change_selection(dataset, tmp_path):
    path = tmp_path / "input.json"
    registry = tmp_path / "search.jsonl"
    path.write_text(json.dumps(dataset))
    first = run(
        tmp_path / "first",
        "2025-01-06",
        "2025-01-09",
        "2025-01-14",
        "2025-01-16",
        input_path=path,
        registry=registry,
    )
    boundary = calendar("2025-01-14", "2025-01-16")[0]["decision"]
    for bar in dataset["bars"]:
        if bar["start"] >= boundary and bar["symbol"] != "SPY":
            for field in ("open", "high", "low", "close"):
                bar[field] *= 2
    path.write_text(json.dumps(dataset))
    second = run(
        tmp_path / "second",
        "2025-01-06",
        "2025-01-09",
        "2025-01-14",
        "2025-01-16",
        input_path=path,
        registry=registry,
    )
    assert first["selected"] == second["selected"]
    assert first["periods"]["train"] == second["periods"]["train"]
    assert first["periods"]["validation"] == second["periods"]["validation"]
    assert second["attempt"] == 2
    assert second["cumulative_hypotheses"] == 10
