"""Prospective collection, strict cutoffs, cold isolation and service access boundaries."""

import getpass
import io
import json
import os
import sqlite3
import subprocess
import warnings
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from tradeagent.calendar import session_bounds
from tradeagent.prospective import access
from tradeagent.prospective.collector import CUTOFF_BLOCKER
from tradeagent.prospective.providers import URL, Alpaca, NoRedirect
from tradeagent.prospective.service import (
    Shadow,
    configuration,
    decision_for,
    next_decision,
    prospective_only,
)
from tradeagent.research.domain import iso
from tradeagent.research.lab import scan

CONFIG = configuration("alpaca")

DAY = date(2026, 10, 8)
OPEN = session_bounds(DAY)[0]
DECISION = decision_for(DAY)


def payload(start, quote_time=None):
    return {
        symbol: {
            "minuteBar": {"t": iso(start), "o": 100, "h": 101, "l": 99, "c": 100.1, "v": 20},
            "latestQuote": {"t": iso(quote_time or start + 60), "bp": 100, "ap": 100.2},
            "prevDailyBar": {"t": iso(OPEN - 86400), "c": 99},
        }
        for symbol in ("QQQ", "IWM", "SPY")
    }


@pytest.fixture
def shadow(tmp_path):
    instance = Shadow(tmp_path / "prospective", OPEN - 30, provider="alpaca")
    instance.initialize()
    yield instance
    instance.close()


def exact_fixture(shadow):
    """Artificial exact-boundary availability tests core integration, not vendor feasibility."""
    for start in (OPEN, OPEN + 60):
        shadow.collection.ingest(payload(start), start, start + 60, session_bounds(DAY), OPEN - 30)


def test_cold_state_no_october7_prediction(shadow):
    shadow.tick(OPEN - 30)
    assert shadow.store.inspect()["counts"]["predictions"] == 0
    assert shadow.store.inspect()["counts"]["learning_runs"] == 0
    assert shadow.store.inspect()["counts"]["mutations"] == 0
    assert shadow.store.inspect()["counts"]["strategy_versions"] == 7
    assert decision_for(date(2026, 10, 7)) is None
    report = shadow.status(OPEN - 30)
    assert report["mode"] == report["status"] == "SHADOW"
    assert report["health"] == "ok" and report["blocks"] == []
    assert report["next_decision_new_york"] == "2026-10-08T09:33:00-04:00"
    assert report["broker_counts"] == dict(reads=0, reviews=0, placements=0, cancellations=0)


def test_actual_receipt_after_bar_end_before_decision_is_usable(shadow):
    for start in (OPEN, OPEN + 60):
        shadow.collection.ingest(
            payload(start), start + 60, start + 60.3, session_bounds(DAY), OPEN - 30
        )
    assert shadow.collection.dataset()["bars"][-1]["available_at"] == DECISION - 60 + 0.3
    snapshot = shadow.collection.snapshot("QQQ", DECISION, session_bounds(DAY))
    assert snapshot.bars[-1].end == DECISION - 60
    assert snapshot.bars[-1].available_at < snapshot.decision_time
    shadow.tick(DECISION + 0.001)
    assert shadow.store.inspect()["counts"]["predictions"] == 14
    assert shadow.collection.done(DAY.isoformat(), "scan")


def test_late_decision_fails_even_when_exact_fixture_is_present(shadow):
    exact_fixture(shadow)
    shadow.tick(DECISION + CONFIG["capture_window_seconds"] + 0.001)
    assert shadow.store.inspect()["counts"]["predictions"] == 0
    row = shadow.collection.db.execute("SELECT * FROM steps").fetchone()
    assert row["status"] == "skipped"
    assert "no late or backfilled" in row["reason"]


def test_restart_does_not_backfill_or_duplicate_skip(shadow):
    shadow.tick(DECISION + 60)
    shadow.status(DECISION + 60)
    directory = shadow.directory
    shadow.close()
    resumed = Shadow(directory, DECISION + 120, provider="alpaca")
    try:
        resumed.initialize()
        resumed.tick(DECISION + 120)
        assert resumed.collection.db.execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 1
        assert resumed.store.inspect()["counts"]["strategy_versions"] == 7
        assert resumed.store.inspect()["counts"]["predictions"] == 0
    finally:
        resumed.close()
    # Fixture teardown tolerates sqlite close on already closed handles.


def test_exact_core_scan_horizon_learning_restart_idempotency(shadow):
    exact_fixture(shadow)
    shadow.tick(DECISION)
    assert shadow.store.inspect()["counts"]["predictions"] == 14
    original = [tuple(r) for r in shadow.store.db.execute("SELECT * FROM predictions")]
    with pytest.raises(ValueError, match="within 120"):
        scan(
            shadow.store,
            shadow.collection.snapshot("QQQ", DECISION, session_bounds(DAY)),
            DECISION + 121,
        )
    # Actual forward receipts after each outcome minute, no unavailable last-minute shortcut.
    for minute in range(60):
        start = DECISION + minute * 60
        shadow.collection.ingest(
            payload(start), start + 60, start + 60.2, session_bounds(DAY), OPEN - 30
        )
    shadow.tick(DECISION + 3600.2)
    assert shadow.store.inspect()["counts"]["outcomes"] == 14
    assert shadow.store.inspect()["counts"]["learning_runs"] == 1
    before = shadow.store.inspect()["counts"]
    shadow.tick(DECISION + 3601)
    assert shadow.store.inspect()["counts"] == before
    directory = shadow.directory
    shadow.status(DECISION + 3601)
    shadow.close()
    resumed = Shadow(directory, DECISION + 3602, provider="alpaca")
    try:
        resumed.initialize()
        resumed.tick(DECISION + 3602)
        assert resumed.store.inspect()["counts"] == before
        assert [tuple(r) for r in resumed.store.db.execute("SELECT * FROM predictions")] == original
        assert (
            resumed.store.db.execute(
                "SELECT MIN(outcome_time),MAX(outcome_time) FROM outcomes"
            ).fetchone()[:]
            == (DECISION + 3600,) * 2
        )
    finally:
        resumed.close()


def test_no_premature_resolution_and_missing_horizon_stays_pending(shadow):
    exact_fixture(shadow)
    shadow.tick(DECISION)
    shadow.tick(DECISION + 3599)
    assert shadow.store.inspect()["counts"]["outcomes"] == 0
    shadow.tick(DECISION + 3601)
    assert shadow.store.inspect()["counts"]["outcomes"] == 0
    assert shadow.store.inspect()["counts"]["learning_runs"] == 0


def test_immutable_first_receipt_and_raw_revision_metadata(shadow):
    original = payload(OPEN)
    shadow.collection.ingest(original, OPEN, OPEN + 60.1, session_bounds(DAY), OPEN - 30)
    changed = payload(OPEN)
    for item in changed.values():
        item["minuteBar"]["c"] = 100.5
    shadow.collection.ingest(changed, OPEN + 60.2, OPEN + 60.3, session_bounds(DAY), OPEN - 30)
    assert shadow.collection.dataset()["bars"][0]["close"] == 100.1
    assert shadow.collection.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        shadow.collection.db.execute("UPDATE bars SET available_at=0")


@pytest.mark.parametrize("bad", ["stale", "missing", "future", "pre-startup"])
def test_collection_fails_closed(shadow, bad):
    value = payload(OPEN)
    received, startup = OPEN + 60, OPEN - 30
    if bad == "stale":
        received += 121
    elif bad == "missing":
        del value["SPY"]
    elif bad == "future":
        received -= 1
    else:
        startup = OPEN + 1
    with pytest.raises(ValueError):
        shadow.collection.ingest(value, OPEN, received, session_bounds(DAY), startup)
    assert shadow.collection.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0


def test_replay_state_refused(shadow):
    exact_fixture(shadow)
    snapshot = shadow.collection.snapshot("QQQ", DECISION, session_bounds(DAY))
    from dataclasses import replace

    scan(shadow.store, replace(snapshot, evidence_kind="replay"), DECISION)
    with pytest.raises(ValueError, match="non-prospective"):
        prospective_only(shadow.store, shadow.source)
    with pytest.raises(ValueError, match="non-prospective"):
        shadow.tick(DECISION)
    assert shadow.store.inspect()["counts"]["learning_runs"] == 0


def test_unmarked_existing_database_and_changed_config_refused(tmp_path):
    directory = tmp_path / "existing"
    directory.mkdir()
    (directory / "experience.sqlite3").write_bytes(b"not a valid cold source")
    with pytest.raises(ValueError, match="unmarked"):
        Shadow(directory, OPEN, provider="alpaca")
    (directory / "deployment.json").write_text(
        json.dumps({"configuration": {**CONFIG, "feed": "iex"}, "started_at": OPEN})
    )
    with pytest.raises(ValueError, match="configuration changed"):
        Shadow(directory, OPEN, provider="alpaca")


def test_exchange_holidays_weekend_early_close_and_dst():
    assert decision_for(date(2026, 12, 25)) is None
    assert decision_for(date(2026, 10, 10)) is None
    november = decision_for(date(2026, 11, 2))
    assert iso(november) == "2026-11-02T14:33:00+00:00"
    assert iso(DECISION) == "2026-10-08T13:33:00+00:00"
    assert decision_for(date(2026, 11, 27)) + 3600 < session_bounds(date(2026, 11, 27))[1]
    assert next_decision(decision_for(date(2026, 12, 24)) + 1) == decision_for(date(2026, 12, 28))


def test_clock_rollback_halts(shadow):
    shadow.tick(OPEN)
    with pytest.raises(ValueError, match="backwards"):
        shadow.tick(OPEN - 1)


def test_only_fixed_data_endpoint_and_errors_redacted(monkeypatch, capsys):
    sentinel = "fake-sensitive-key"
    client = Alpaca((sentinel, "fake-sensitive-secret"))

    class Transport:
        def open(self, request, timeout):
            assert request.full_url == URL
            raise HTTPError(
                URL, 403, sentinel, {"x-secret": sentinel}, io.BytesIO(sentinel.encode())
            )

    client._opener = Transport()
    with pytest.raises(ValueError, match="HTTP 403") as caught:
        client.fetch()
    assert sentinel not in str(caught.value)
    assert sentinel not in repr(client)
    assert capsys.readouterr() == ("", "")
    assert NoRedirect().redirect_request(None, None, 302, "", {}, "https://broker") is None


def test_service_import_has_no_broker_execution_modules():
    result = subprocess.run(
        [
            os.sys.executable,
            "-c",
            "import sys; import tradeagent.prospective.service; "
            "assert not any(x in sys.modules for x in ('tradeagent.broker','tradeagent.execution','tradeagent.canary_review')); print('safe')",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "safe"


def test_service_loader_no_interactive_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setattr(getpass, "getpass", lambda *args: pytest.fail("unattended prompt"))
    with pytest.raises(ValueError, match="systemd"):
        access.load()
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    path = tmp_path / "alpaca"
    path.write_bytes(b"fake-key\nfake-secret\n")
    path.chmod(0o600)
    assert access.load() == ("fake-key", "fake-secret")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        access.load()


def test_setup_hidden_input_only_ciphertext_written(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(os.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(os.sys.stderr, "isatty", lambda: True)
    entries = iter(["fake-key-input", "fake-secret-input"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt: next(entries))

    def encrypt(argv, **kwargs):
        assert kwargs["input"] == b"fake-key-input\nfake-secret-input\n"
        assert not any("fake-key-input" in arg or "fake-secret-input" in arg for arg in argv)
        assert argv[:5] == ["systemd-creds", "encrypt", "--user", "--name=alpaca", "-"]
        Path(argv[-1]).write_bytes(b"ciphertext only")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", encrypt)
    access.setup()
    captured = capsys.readouterr()
    assert "fake-key-input" not in captured.out + captured.err
    assert "fake-secret-input" not in captured.out + captured.err
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"fake-key-input" not in path.read_bytes()
            assert b"fake-secret-input" not in path.read_bytes()
            assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("fallback", [False, True])
def test_setup_no_echo_or_noninteractive_fallback(tmp_path, monkeypatch, fallback):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(os.sys.stdin, "isatty", lambda: fallback)
    monkeypatch.setattr(os.sys.stderr, "isatty", lambda: fallback)

    def bad_input(prompt):
        warnings.warn("echo fallback", getpass.GetPassWarning, stacklevel=2)
        pytest.fail("must stop before echo fallback")

    monkeypatch.setattr(getpass, "getpass", bad_input)
    with pytest.raises(ValueError):
        access.setup()
    assert not list(tmp_path.rglob("*.cred"))


def test_credential_audit_no_values_or_fingerprints(tmp_path, monkeypatch):
    repo, state = tmp_path / "repo", tmp_path / "state"
    repo.mkdir()
    state.mkdir()
    (repo / "source.py").write_text("source only")
    (state / "STATUS.md").write_text("status only")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b"source.py\x00", stderr=b"")
    )
    values = ("fake-key-isolated", "fake-secret-isolated")
    assert all(access.audit(repo, state, values).values())
    (state / "STATUS.md").write_text(values[1])
    result = access.audit(repo, state, values)
    assert not result["repository_and_reports_clean"]
    assert all(value not in json.dumps(result) for value in values)


def test_foreign_observation_source_cannot_enter_shadow_learning(shadow):
    from tradeagent.research.outcomes import ingest

    dataset = {
        "source": "alpaca-historical-replay",
        "bars": [
            {
                "symbol": "QQQ",
                "start": OPEN,
                "end": OPEN + 60,
                "available_at": OPEN + 60.1,
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 10,
            }
        ],
    }
    with shadow.store.db:
        ingest(shadow.store, dataset, OPEN + 61)
    with pytest.raises(ValueError, match="source observations"):
        shadow.tick(DECISION)
    assert shadow.store.inspect()["counts"]["learning_runs"] == 0


def test_credential_file_symlink_refused(tmp_path, monkeypatch):
    target = tmp_path / "private"
    target.write_bytes(b"fake-key\nfake-secret\n")
    target.chmod(0o600)
    (tmp_path / "alpaca").symlink_to(target)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    with pytest.raises(ValueError, match="private regular file"):
        access.load()


def test_journal_credential_leak_detected_without_emission(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    values = ("fake-key-in-journal", "fake-secret-in-journal")

    def commands(argv, **kwargs):
        return SimpleNamespace(
            stdout=values[1].encode() if argv[0] == "journalctl" else b"", stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", commands)
    result = access.audit(tmp_path, state, values)
    assert result["repository_and_reports_clean"]
    assert not result["service_journal_clean"]
    assert all(value not in json.dumps(result) for value in values)
    assert capsys.readouterr() == ("", "")


def test_data_received_after_decision_cannot_leak_into_snapshot(shadow):
    shadow.collection.ingest(payload(OPEN), OPEN + 60, OPEN + 60.3, session_bounds(DAY), OPEN - 30)
    shadow.collection.ingest(
        payload(OPEN + 60, DECISION + 0.1),
        DECISION,
        DECISION + 0.3,
        session_bounds(DAY),
        OPEN - 30,
    )
    with pytest.raises(ValueError, match=CUTOFF_BLOCKER):
        shadow.collection.snapshot("QQQ", DECISION, session_bounds(DAY))
    shadow.tick(DECISION + 0.3)
    assert shadow.store.db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0


def test_fractional_poll_duplicate_and_restart_are_idempotent(shadow):
    exact_fixture(shadow)
    shadow.tick(DECISION + 2.3)
    original = [tuple(r) for r in shadow.store.db.execute("SELECT * FROM predictions")]
    shadow.tick(DECISION + 3.9)
    directory = shadow.directory
    shadow.status(DECISION + 3.9)
    shadow.close()
    resumed = Shadow(directory, DECISION + 10, provider="alpaca")
    try:
        resumed.initialize()
        resumed.tick(DECISION + 10)
        assert [tuple(r) for r in resumed.store.db.execute("SELECT * FROM predictions")] == original
        assert len(original) == 14
        assert resumed.collection.db.execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 1
    finally:
        resumed.close()


def test_lifetime_owner_lock_precedes_writes_and_covers_cli(shadow):
    from tradeagent.research.store import Experience

    marker = (shadow.directory / "deployment.json").read_bytes()
    with pytest.raises(BlockingIOError):
        Shadow(shadow.directory, DECISION, provider="alpaca")
    assert (shadow.directory / "deployment.json").read_bytes() == marker
    other = Experience(shadow.directory)
    try:
        with pytest.raises(ValueError, match="holds the lock"):
            with other.lock():
                pytest.fail("second runtime admitted")
    finally:
        other.close()
    directory = shadow.directory
    shadow.close()
    resumed = Shadow(directory, DECISION, provider="alpaca")
    resumed.close()  # Kernel releases the lock; stale lock files do not block restart.


def test_two_symbols_commit_together_and_crash_retries_without_duplicates(shadow, monkeypatch):
    import tradeagent.prospective.service as service

    exact_fixture(shadow)
    original = service.scan
    calls = []

    def crash(store, snapshot, now):
        calls.append(snapshot.symbol)
        if snapshot.symbol == "IWM":
            raise SystemExit("simulated process crash")
        return original(store, snapshot, now)

    monkeypatch.setattr(service, "scan", crash)
    with pytest.raises(SystemExit):
        shadow.tick(DECISION + 1)
    assert calls == ["QQQ", "IWM"]
    assert shadow.store.db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0
    monkeypatch.setattr(service, "scan", original)
    shadow.tick(DECISION + 2)
    assert shadow.store.db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 14


def test_delayed_signal_outcomes_exclude_predecision_move_and_learner_report_fail_soft(
    shadow, monkeypatch
):
    import tradeagent.prospective.service as service

    exact_fixture(shadow)
    shadow.tick(DECISION + 1)
    for minute in range(60):
        start = DECISION + minute * 60
        value = payload(start)
        for item in value.values():
            item["minuteBar"].update(o=120, h=122, l=119, c=121)
        shadow.collection.ingest(
            value,
            start + 60,
            start + 60.2,
            session_bounds(DAY),
            OPEN - 30,
        )

    def fail(*args, **kwargs):
        raise RuntimeError("optional research failure")

    champions = [
        tuple(r)
        for r in shadow.store.db.execute(
            "SELECT strategy_id,champion_version FROM strategies ORDER BY strategy_id"
        )
    ]
    monkeypatch.setattr(service, "learn_daily", fail)
    shadow.tick(DECISION + 3600.3)
    outcomes = shadow.store.db.execute("SELECT * FROM outcomes").fetchall()
    assert len(outcomes) == 14
    for row in outcomes:
        assert row["raw_return"] == pytest.approx(121 / 120 - 1)
        assert row["residual_return"] == 0
        metadata = json.loads(row["metadata"])
        assert metadata["entry_price_model"] == "decision-minute open"
        assert metadata["signal_bar_end"] == DECISION - 60
    before = shadow.store.inspect()["counts"]
    shadow.tick(DECISION + 3601)
    assert shadow.store.inspect()["counts"] == before
    assert before["learning_runs"] == before["mutations"] == before["promotions"] == 0
    assert champions == [
        tuple(r)
        for r in shadow.store.db.execute(
            "SELECT strategy_id,champion_version FROM strategies ORDER BY strategy_id"
        )
    ]

    def bad_report(*args, **kwargs):
        raise OSError("optional report failure")

    monkeypatch.setattr(shadow, "status", bad_report)
    assert shadow.report(DECISION + 3601) is None
    shadow.tick(DECISION + 3602)
    assert shadow.store.inspect()["counts"] == before
    assert (
        shadow.collection.db.execute(
            "SELECT reason FROM failures ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        == "optional status report failure"
    )


def test_service_missing_credential_persists_block_and_releases_owner(tmp_path, monkeypatch):
    import sys

    import tradeagent.prospective.service as service

    directory = tmp_path / "cold"
    monkeypatch.setattr(
        sys, "argv", ["shadow", "--state-dir", str(directory), "--market-data-provider", "alpaca"]
    )
    monkeypatch.setattr(service.time, "time", lambda: OPEN - 30)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setattr(service.signal, "signal", lambda *args: None)
    with pytest.raises(ValueError, match="systemd"):
        service.main()
    report = json.loads((directory / "status.json").read_text())
    assert report["mode"] == "SHADOW" and report["health"] == "blocked"
    assert report["blocks"] == ["alpaca market-data authentication or contract unavailable"]
    assert report["predictions"] == report["resolved"] == 0
    resumed = Shadow(directory, OPEN - 29, provider="alpaca")
    resumed.close()


def test_resolved_history_is_not_reprocessed_on_every_poll(shadow, monkeypatch):
    import tradeagent.prospective.service as service

    exact_fixture(shadow)
    shadow.tick(DECISION + 1)
    for minute in range(60):
        start = DECISION + minute * 60
        shadow.collection.ingest(
            payload(start), start + 60, start + 60.2, session_bounds(DAY), OPEN - 30
        )
    shadow.tick(DECISION + 3600.3)
    assert shadow.store.db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 14
    monkeypatch.setattr(
        service, "resolve", lambda *a, **k: pytest.fail("resolved history repeated")
    )
    shadow.tick(DECISION + 3602)


def test_optional_selector_failure_preserves_predictions_with_no_trade_plans(shadow, monkeypatch):
    import tradeagent.research.lab as lab

    exact_fixture(shadow)
    original = lab.rank

    def unavailable(*args, **kwargs):
        if kwargs.get("use_learning", True):
            raise RuntimeError("optional learned selector unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(lab, "rank", unavailable)
    shadow.tick(DECISION + 1.1)
    assert shadow.store.db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 14
    assert [r[0] for r in shadow.store.db.execute("SELECT DISTINCT kind FROM trade_plans")] == [
        "NO_TRADE"
    ]
    assert not any(r[0] for r in shadow.store.db.execute("SELECT selected FROM selections"))
    assert all(
        json.loads(r[0])["selection_unavailable"] == "RuntimeError"
        for r in shadow.store.db.execute("SELECT rationale FROM selections")
    )
