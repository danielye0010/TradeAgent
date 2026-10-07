"""Replay isolation, information cutoffs, chronology and frozen scoring."""

import copy
import importlib.util
import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from tradeagent.cli import main
from tradeagent.research.demo import fixture
from tradeagent.research.domain import timestamp
from tradeagent.research.history_data import AlpacaHistory, MinuteHistory, load_history
from tradeagent.research.lab import seed
from tradeagent.research.replay import (
    ReplayClock,
    decision_snapshot,
    evidence_digest,
    run_history,
    session_window,
)
from tradeagent.research.store import Experience

spec = importlib.util.spec_from_file_location(
    "commissioning_fixture", Path(__file__).parents[1] / "scripts/commissioning_fixture.py"
)
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)


@pytest.fixture
def data(tmp_path):
    path = tmp_path / "minutes.json"
    path.write_text(json.dumps(fixture_module.bundle()))
    return path


def run(output, data):
    return run_history(output, 6, ["QQQ", "IWM"], "SPY", "2026-01-12", data)


def test_clock_is_explicit_and_production_cli_stays_fail_closed(tmp_path, capsys):
    real_before = time.time()
    clock = ReplayClock(timestamp("2026-01-05T14:32:00Z"))
    clock.advance(clock.now + 3600)
    assert time.time() >= real_before
    with pytest.raises(ValueError, match="backwards"):
        clock.advance(clock.now - 1)
    snapshot, _ = fixture(timestamp("2026-01-05T16:00:00Z"), "prospective")
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(asdict(snapshot)))
    store = Experience(tmp_path / "normal")
    seed(store, snapshot.decision_time - 1)
    store.close()
    assert main(["scan", "--state-dir", str(tmp_path / "normal"), "--input", str(path)]) == 2
    assert "contemporaneous" in capsys.readouterr().err


def test_future_prices_cannot_affect_snapshot_or_features(data):
    dataset = json.loads(data.read_text())
    _, calendar = session_window("2026-01-12", 6)
    session = calendar[0]
    original = decision_snapshot(MinuteHistory(dataset), session, "QQQ", "SPY", session["decision"])
    for b in dataset["bars"]:
        if b["start"] >= session["decision"]:
            for field in ("open", "high", "low", "close"):
                b[field] *= 100
    changed = decision_snapshot(MinuteHistory(dataset), session, "QQQ", "SPY", session["decision"])
    assert original == changed
    assert all(b.available_at <= session["decision"] for b in changed.bars)


def test_late_decision_bar_is_skipped_without_fabrication(tmp_path, data):
    dataset = json.loads(data.read_text())
    _, calendar = session_window("2026-01-12", 6)
    for b in dataset["bars"]:
        if b["symbol"] == "QQQ" and b["end"] == calendar[0]["decision"]:
            b["available_at"] += 1
    data.write_text(json.dumps(dataset))
    result = run(tmp_path / "replay", data)
    assert result["integrity"]["completed_sessions"] == 5
    assert "missing decision" in result["integrity"]["skipped_sessions"][0]["reason"]


def test_future_release_only_after_all_predictions(tmp_path, data, monkeypatch):
    original = MinuteHistory.window
    _, calendar = session_window("2026-01-12", 6)
    decisions = {s["decision"] for s in calendar}
    output = tmp_path / "replay"
    releases = []

    def checked(self, symbol, start, end, cutoff):
        if start in decisions and end == start + 3600:
            with sqlite3.connect(output / "experience.sqlite3") as db:
                assert (
                    db.execute(
                        "SELECT COUNT(DISTINCT symbol) FROM predictions WHERE decision_time=?",
                        (start,),
                    ).fetchone()[0]
                    == 2
                )
            releases.append((symbol, start))
        return original(self, symbol, start, end, cutoff)

    monkeypatch.setattr(MinuteHistory, "window", checked)
    run(output, data)
    assert len(releases) == 18


def test_prior_only_next_day_state_and_historical_pool(tmp_path, data):
    output = tmp_path / "replay"
    result = run(output, data)
    store = Experience(output)
    try:
        _, calendar = session_window("2026-01-12", 6)
        for day, session in enumerate(calendar):
            rows = store.db.execute(
                "SELECT s.state_asof,s.rationale FROM selections s JOIN predictions p USING(prediction_id) "
                "WHERE p.decision_time=?",
                (session["decision"],),
            ).fetchall()
            assert rows
            assert all(
                r["state_asof"] is None or r["state_asof"] < session["decision"] for r in rows
            )
            if day == 0:
                assert all(r["state_asof"] is None for r in rows)
            else:
                assert any(r["state_asof"] == calendar[day - 1]["decision"] + 3601 for r in rows)
            states = store.db.execute(
                "SELECT MAX(s.n) FROM strategy_scores s JOIN learning_runs l USING(run_id) "
                "WHERE l.asof<?",
                (session["decision"],),
            ).fetchone()[0]
            assert states == (day if day else None)
        assert {r[0] for r in store.db.execute("SELECT evidence_kind FROM market_snapshots")} == {
            "replay"
        }
        assert all(
            json.loads(r[0])["evidence_kind"] == "replay"
            for r in store.db.execute("SELECT configuration FROM learning_runs")
        )
        assert all(
            json.loads(r[0])["evidence_kind"] == "replay"
            for r in store.db.execute("SELECT evaluation_plan FROM mutations")
        )
        assert result["integrity"]["promotions"] == 0
    finally:
        store.close()


def test_cached_determinism_and_rerun_no_duplicate_evidence(tmp_path, data):
    first = run(tmp_path / "a", data)
    assert first == run(tmp_path / "b", data)
    store = Experience(tmp_path / "a")
    digest = evidence_digest(store)
    store.close()
    # No input supplied: the same adapter cache must be sufficient without network.
    assert first == run_history(tmp_path / "a", 6, ["QQQ", "IWM"], "SPY", "2026-01-12")
    store = Experience(tmp_path / "a")
    assert evidence_digest(store) == digest
    store.close()
    assert first["integrity"]["idempotency_failures"] == 0
    assert first["status"] == "REVIEW"  # synthetic validation, never market commissioning PASS


@pytest.mark.parametrize("target", ["research", "execution"])
def test_output_refuses_existing_normal_state_before_opening_it(tmp_path, data, target):
    output = tmp_path / target
    if target == "research":
        normal = Experience(output)
        seed(normal, 1)
        normal.close()
        db_path = output / "experience.sqlite3"
    else:
        output.mkdir()
        db_path = output / "state.sqlite3"
        db_path.write_bytes(b"operator execution sentinel")
    before = db_path.read_bytes()
    with pytest.raises(ValueError, match="non-commissioning|execution journal"):
        run(output, data)
    assert db_path.read_bytes() == before


def test_separate_output_leaves_normal_research_and_journal_unchanged(tmp_path, data, monkeypatch):
    normal = Experience(tmp_path / "normal")
    seed(normal, 1)
    normal.close()
    normal_db = tmp_path / "normal/experience.sqlite3"
    journal = tmp_path / "normal/state.sqlite3"
    journal.write_bytes(b"execution sentinel")
    before = normal_db.read_bytes(), journal.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("broker access forbidden")

    monkeypatch.setattr("tradeagent.codex_bridge.CodexBridge", forbidden)
    result = run(tmp_path / "commissioning", data)
    assert before == (normal_db.read_bytes(), journal.read_bytes())
    assert result["real_broker_calls"] == 0


@pytest.mark.parametrize("symbol", ["QQQ", "SPY"])
def test_missing_exact_horizon_stays_unresolved_and_records_reason(tmp_path, data, symbol):
    dataset = json.loads(data.read_text())
    _, calendar = session_window("2026-01-12", 6)
    end = calendar[0]["decision"] + 3600
    dataset["bars"] = [
        b for b in dataset["bars"] if not (b["symbol"] == symbol and b["end"] == end)
    ]
    data.write_text(json.dumps(dataset))
    result = run(tmp_path / "replay", data)
    assert result["integrity"]["completed_sessions"] == 5
    assert result["integrity"]["unresolved_predictions"] == (7 if symbol == "QQQ" else 14)
    assert result["integrity"]["timestamp_leakage_violations"] == 0
    assert "missing exact-horizon" in result["integrity"]["skipped_sessions"][0]["reason"]


def test_calendar_holiday_early_close_and_dst():
    _, holidays = session_window("2026-07-07", 3)
    assert [r["date"] for r in holidays] == ["2026-07-01", "2026-07-02", "2026-07-06"]
    _, early = session_window("2025-12-01", 2)
    assert early[-1]["close"] - early[-1]["open"] == 3.5 * 3600
    _, dst = session_window("2026-03-10", 2)
    assert iso_for_test(dst[0]["decision"])[11:16] == "14:32"
    assert iso_for_test(dst[1]["decision"])[11:16] == "13:32"


def iso_for_test(value):
    from tradeagent.research.domain import iso

    return iso(value)


def test_changed_cached_input_is_rejected(tmp_path, data):
    output = tmp_path / "replay"
    run(output, data)
    before = (output / "experience.sqlite3").read_bytes()
    dataset = json.loads(data.read_text())
    dataset["bars"][0]["volume"] += 1
    data.write_text(json.dumps(dataset))
    with pytest.raises(ValueError, match="cached input changed"):
        run(output, data)
    assert (output / "experience.sqlite3").read_bytes() == before


def test_cache_metadata_and_no_credentials_no_broker_requirement(tmp_path, data, monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="historical data unavailable"):
        AlpacaHistory().fetch(["QQQ"], 1, 2)
    dataset = json.loads(data.read_text())

    class Source:
        calls = 0

        def fetch(self, symbols, start, end):
            self.calls += 1
            return copy.deepcopy(dataset)

    source = Source()
    a, digest, cache = load_history(tmp_path, ["QQQ", "IWM", "SPY"], 1, 2, source=source)
    b, repeated, _ = load_history(tmp_path, ["QQQ", "IWM", "SPY"], 1, 2, source=source)
    assert source.calls == 1 and digest == repeated and a.metadata == b.metadata
    assert json.loads(cache.read_text())["dataset"]["metadata"]["adjustment"] == "raw"


def test_cli_replay_defaults_and_frozen_horizon(tmp_path, data, capsys):
    assert (
        main(
            [
                "replay-history",
                "--sessions",
                "6",
                "--as-of",
                "2026-01-12",
                "--input",
                str(data),
                "--output",
                str(tmp_path / "replay"),
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["configuration"]["horizon_seconds"] == 3600
    assert summary["configuration"]["decision_time"].startswith("09:32 America/New_York")
    assert set(summary["selector_comparison"]) == {"learned", "raw", "null", "random"}
    assert (
        summary["integrity"]["predictions_resolved"]
        == summary["integrity"]["predictions_generated"]
    )


def test_unavailable_real_data_reports_review_without_creating_database(tmp_path, monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    output = tmp_path / "unavailable"
    result = run_history(output, as_of="2026-10-07")
    assert result["status"] == "REVIEW" and result["session_range"] is None
    assert result["integrity"]["completed_sessions"] == 0
    assert not (output / "experience.sqlite3").exists()
    assert json.loads((output / "summary.json").read_text()) == result
