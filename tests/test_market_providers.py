"""Provider contracts, normalized timing, persistence and explicit selection."""

import importlib.util
import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_prospective import DAY, DECISION, OPEN, payload

from tradeagent.calendar import session_bounds
from tradeagent.model import Halt
from tradeagent.prospective import access, providers
from tradeagent.prospective.collector import Collection
from tradeagent.prospective.providers import Alpaca, Robinhood
from tradeagent.prospective.service import CONFIG, Shadow, configuration
from tradeagent.research.domain import iso

PIN = json.loads(
    (Path(__file__).parents[1] / "src/tradeagent/contracts/market-data-1.7.0.json").read_text()
)


def bridge():
    return SimpleNamespace(server_info={"version": "1.7.0"}, tools=PIN["tools"], calls=[])


def rh_payload(starts, received):
    return {
        "history": {
            "data": {
                "results": [
                    {
                        "symbol": symbol,
                        "interval": "minute",
                        "bounds": "regular",
                        "bars": [
                            {
                                "begins_at": iso(start),
                                "open_price": "100",
                                "high_price": "101",
                                "low_price": "99",
                                "close_price": "100.1",
                                "volume": 20,
                                "session": "reg",
                            }
                            for start in starts
                        ],
                    }
                    for symbol in providers.SYMBOLS
                ]
            }
        },
        "quotes": {
            "data": {
                "results": [
                    {
                        "quote": {
                            "symbol": symbol,
                            "venue_bid_time": iso(received),
                            "venue_ask_time": iso(received),
                            "bid_price": "100",
                            "ask_price": "100.2",
                            "has_traded": True,
                            "state": "active",
                            "previous_close": "99",
                            "previous_close_date": "2026-10-07",
                        }
                    }
                    for symbol in providers.SYMBOLS
                ]
            }
        },
    }


def test_normalized_snapshot_equivalence_and_future_exclusion(tmp_path):
    collections = []
    for name, adapter in (("alpaca", Alpaca), ("robinhood", Robinhood)):
        directory = tmp_path / name
        directory.mkdir()
        collections.append(Collection(directory, adapter))
    alpaca, robinhood = collections
    try:
        for start in (OPEN, OPEN + 60):
            received = start + 60.3
            alpaca.ingest(payload(start, received), start, received, session_bounds(DAY), OPEN - 30)
            robinhood.ingest(
                rh_payload([start, start + 60], received),
                start,
                received,
                session_bounds(DAY),
                OPEN - 30,
            )
        a = asdict(alpaca.snapshot("QQQ", DECISION, session_bounds(DAY)))
        r = asdict(robinhood.snapshot("QQQ", DECISION, session_bounds(DAY)))
        assert a.pop("source") != r.pop("source")
        assert a == r
        assert len(robinhood.dataset()["bars"]) == 6
        assert robinhood.history_start(session_bounds(DAY), OPEN - 30) == OPEN + 60
        # A later receipt cannot replace a frozen observation or alter the decision.
        revised = rh_payload([OPEN, OPEN + 60, DECISION], DECISION + 61)
        revised["history"]["data"]["results"][0]["bars"][0]["close_price"] = "100.9"
        robinhood.ingest(revised, DECISION + 60, DECISION + 61, session_bounds(DAY), OPEN - 30)
        assert asdict(robinhood.snapshot("QQQ", DECISION, session_bounds(DAY))) == {
            **r,
            "source": Robinhood.source,
        }
    finally:
        for collection in collections:
            collection.db.close()


@pytest.mark.parametrize("bad", ["interpolated", "missing", "interval", "future_book", "zero_book"])
def test_missing_or_invalid_data_cannot_predict(tmp_path, bad):
    with_shadow = Shadow(tmp_path, OPEN - 30)
    try:
        with_shadow.initialize()
        data = rh_payload([OPEN, OPEN + 60], DECISION - 10)
        first = data["history"]["data"]["results"][0]
        if bad == "interpolated":
            first["bars"][0]["interpolated"] = True
        elif bad == "missing":
            data["history"]["data"]["results"].pop()
        elif bad == "interval":
            first["interval"] = "5minute"
        elif bad == "future_book":
            data["quotes"]["data"]["results"][0]["quote"]["venue_ask_time"] = iso(DECISION)
        else:
            data["quotes"]["data"]["results"][0]["quote"]["bid_price"] = "0"
        if bad == "interpolated":
            with_shadow.collection.ingest(
                data, DECISION - 11, DECISION - 10, session_bounds(DAY), OPEN - 30
            )
        else:
            with pytest.raises(ValueError):
                with_shadow.collection.ingest(
                    data, DECISION - 11, DECISION - 10, session_bounds(DAY), OPEN - 30
                )
        with_shadow.tick(DECISION)
        assert with_shadow.store.inspect()["counts"]["predictions"] == 0
        assert with_shadow.status(DECISION)["broker_counts"] == {
            "reads": 0,
            "reviews": 0,
            "placements": 0,
            "cancellations": 0,
        }
    finally:
        with_shadow.close()


def test_robinhood_duplicate_restart_alignment_and_exact_outcome(tmp_path):
    shadow = Shadow(tmp_path, OPEN - 30)
    shadow.initialize()
    shadow.collection.ingest(
        rh_payload([OPEN, OPEN + 60], DECISION - 10),
        DECISION - 11,
        DECISION - 10,
        session_bounds(DAY),
        OPEN - 30,
    )
    # A mismatched benchmark cannot enter the shared snapshot.
    original = shadow.collection.snapshot("QQQ", DECISION, session_bounds(DAY))
    with pytest.raises(ValueError, match="align"):
        replace(original, benchmark_bars=(original.benchmark_bars[0],))
    shadow.tick(DECISION + 0.5)
    assert shadow.store.inspect()["counts"]["predictions"] == 14
    shadow.close()
    shadow = Shadow(tmp_path, DECISION + 1)
    try:
        shadow.initialize()
        shadow.tick(DECISION + 1)
        assert shadow.store.inspect()["counts"]["predictions"] == 14
        missing = rh_payload([DECISION + i * 60 for i in range(59)], DECISION + 3541)
        for result in missing["history"]["data"]["results"]:
            for bar in result["bars"]:
                bar.update(open_price="120", high_price="122", low_price="119", close_price="121")
        shadow.collection.ingest(
            missing, DECISION + 3540, DECISION + 3541, session_bounds(DAY), OPEN - 30
        )
        shadow.tick(DECISION + 3601)
        assert shadow.store.inspect()["counts"]["outcomes"] == 0
        # A huge movement before decision is excluded from the return entry.
        future = rh_payload([DECISION + i * 60 for i in range(60)], DECISION + 3602)
        for result in future["history"]["data"]["results"]:
            for bar in result["bars"]:
                bar.update(open_price="120", high_price="122", low_price="119", close_price="121")
        shadow.collection.ingest(
            future, DECISION + 3601, DECISION + 3602, session_bounds(DAY), OPEN - 30
        )
        shadow.tick(DECISION + 3602)
        rows = shadow.store.db.execute("SELECT * FROM outcomes").fetchall()
        assert len(rows) == 14
        for row in rows:
            assert row["raw_return"] == pytest.approx(121 / 120 - 1)
            assert row["residual_return"] == 0
            assert json.loads(row["metadata"])["entry_price_model"] == "decision-minute open"
        shadow.tick(DECISION + 3603)
        assert shadow.store.inspect()["counts"]["outcomes"] == 14
    finally:
        shadow.close()


def test_robinhood_fetch_uses_only_named_market_reads():
    client = bridge()

    def read(name, args):
        client.calls.append((name, args))
        assert name in {"get_equity_historicals", "get_equity_quotes"}
        return {}

    client.read = read
    source = Robinhood(client, clock=lambda: DECISION - 10)
    _, started, received = source.fetch(session_bounds(DAY), OPEN - 30)
    assert started == received == DECISION - 10
    assert client.calls[0][1] == {
        "symbols": ["QQQ", "IWM", "SPY"],
        "start_time": iso(OPEN),
        "end_time": iso(DECISION - 10),
        "interval": "minute",
        "bounds": "regular",
        "adjustment_type": "none",
    }
    client.server_info["version"] = "unknown"
    with pytest.raises(Halt, match="version"):
        Robinhood(client)


def test_default_provider_does_not_load_alpaca(monkeypatch, tmp_path):
    class MockSession:
        def __init__(self, token):
            pass

        def __enter__(self):
            return bridge()

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(providers, "ReadOnlyMCP", MockSession)
    monkeypatch.setattr(providers, "ExternalOAuthToken", lambda *args: object())
    monkeypatch.setattr(access, "load", lambda: pytest.fail("Alpaca credential loaded"))
    with providers.open_provider("robinhood", tmp_path) as source:
        assert isinstance(source, Robinhood)
    assert CONFIG["market_data_provider"] == CONFIG["broker_provider"] == "robinhood"
    assert "feed" not in CONFIG
    assert configuration("alpaca")["feed"] == "sip"
    with pytest.raises(ValueError, match="unknown"):
        configuration("other")


@pytest.mark.parametrize("provider", ["robinhood", "alpaca"])
def test_installer_reuses_one_owner_with_explicit_provider(tmp_path, monkeypatch, provider):
    import sys

    spec = importlib.util.spec_from_file_location(
        "install_shadow", Path(__file__).parents[1] / "scripts/install_shadow.py"
    )
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["install", "--market-data-provider", provider])
    calls = []
    monkeypatch.setattr(installer.subprocess, "run", lambda args, **kw: calls.append(args))
    installer.main()
    unit = (tmp_path / ".config/systemd/user/tradeagent-prospective.service").read_text()
    assert f"--market-data-provider {provider}" in unit
    assert ("LoadCredentialEncrypted=alpaca:" in unit) == (provider == "alpaca")
    assert ("--oauth-helper" in unit) == (provider == "robinhood")
    assert calls[-1] == ["systemctl", "--user", "restart", installer.UNIT]


def test_default_auth_failure_is_blocked_without_alpaca_or_writes(tmp_path, monkeypatch):
    import sys
    from contextlib import contextmanager

    from tradeagent.prospective import service

    @contextmanager
    def unavailable(*args):
        raise Halt("external OAuth unavailable")
        yield

    monkeypatch.setattr(service, "open_provider", unavailable)
    monkeypatch.setattr(access, "load", lambda: pytest.fail("Alpaca credential loaded"))
    monkeypatch.setattr(sys, "argv", ["shadow", "--state-dir", str(tmp_path)])
    monkeypatch.setattr(service.time, "time", lambda: OPEN - 30)
    monkeypatch.setattr(service.signal, "signal", lambda *args: None)
    with pytest.raises(Halt, match="OAuth"):
        service.main()
    report = json.loads((tmp_path / "status.json").read_text())
    assert report["blocks"] == ["robinhood market-data authentication or contract unavailable"]
    assert report["predictions"] == report["resolved"] == 0
    assert not any(report["broker_counts"].values())
    resumed = Shadow(tmp_path, OPEN - 29)
    resumed.close()


def test_provider_state_and_gap_cursor_are_not_silently_reused(tmp_path):
    from tradeagent.prospective.service import prospective_only
    from tradeagent.research.lab import scan

    shadow = Shadow(tmp_path, OPEN - 30)
    shadow.initialize()
    try:
        # A missing second minute stays in the next request even if a later bar exists.
        data = rh_payload([OPEN, OPEN + 120], DECISION - 1)
        shadow.collection.ingest(data, DECISION - 2, DECISION - 1, session_bounds(DAY), OPEN - 30)
        assert shadow.collection.history_start(session_bounds(DAY), OPEN - 30) == OPEN
        shadow.collection.ingest(
            rh_payload([OPEN, OPEN + 60], DECISION - 0.5),
            DECISION - 1,
            DECISION - 0.5,
            session_bounds(DAY),
            OPEN - 30,
        )
        snapshot = shadow.collection.snapshot("QQQ", DECISION, session_bounds(DAY))
        scan(shadow.store, replace(snapshot, source=Alpaca.source), DECISION)
        with pytest.raises(ValueError, match="provider snapshot source"):
            prospective_only(shadow.store)
    finally:
        shadow.close()
    marker = (tmp_path / "deployment.json").read_bytes()
    with pytest.raises(ValueError, match="configuration changed"):
        Shadow(tmp_path, DECISION + 1, provider="alpaca")
    assert (tmp_path / "deployment.json").read_bytes() == marker
