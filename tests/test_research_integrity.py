"""Protect SQL replacement and all-or-nothing research operations."""

import sqlite3

import pytest

from tradeagent.research.demo import fixture
from tradeagent.research.domain import timestamp
from tradeagent.research.lab import scan, seed
from tradeagent.research.store import Experience

START = timestamp("2026-01-05T16:00:00Z")


def test_replace_cannot_bypass_append_only_even_with_recursive_triggers_off(tmp_path):
    store = Experience(tmp_path)
    try:
        seed(store, START - 1)
        scan(store, fixture(START)[0], START)
        row = dict(store.db.execute("SELECT * FROM predictions LIMIT 1").fetchone())
        row["expected_return"] = 0.02
        store.db.execute("PRAGMA recursive_triggers=OFF")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.db.execute(
                f"INSERT OR REPLACE INTO predictions({','.join(row)}) VALUES({','.join('?' for _ in row)})",
                tuple(row.values()),
            )
        row["prediction_id"] = "new-id-same-scientific-decision"
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.db.execute(
                f"INSERT OR REPLACE INTO predictions({','.join(row)}) VALUES({','.join('?' for _ in row)})",
                tuple(row.values()),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.db.execute(
                "INSERT OR REPLACE INTO strategies VALUES('opening_momentum','mean_reversion','v1',1)"
            )
    finally:
        store.close()


def test_source_column_names_are_checked_before_sql(tmp_path):
    store = Experience(tmp_path)
    try:
        with pytest.raises(ValueError, match="columns"):
            store.insert("lessons", {"lesson_id) VALUES('bad'); --": "unexpected"})
    finally:
        store.close()


def test_scan_transaction_rolls_back_partial_population(tmp_path, monkeypatch):
    store = Experience(tmp_path)
    try:
        seed(store, START - 1)
        original = store.insert
        calls = 0

        def fail_second_prediction(table, item):
            nonlocal calls
            if table == "predictions":
                calls += 1
                if calls == 2:
                    raise ValueError("simulated interrupted scan")
            return original(table, item)

        monkeypatch.setattr(store, "insert", fail_second_prediction)
        with pytest.raises(ValueError, match="interrupted"):
            scan(store, fixture(START)[0], START)
        for table in ("predictions", "market_snapshots", "shadow_expressions", "selections"):
            assert store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        store.close()


def test_research_process_lock_rejects_concurrent_worker(tmp_path):
    first = Experience(tmp_path)
    second = Experience(tmp_path)
    try:
        with first.lock():
            with pytest.raises(ValueError, match="holds the lock"), second.lock():
                pass
    finally:
        second.close()
        first.close()
