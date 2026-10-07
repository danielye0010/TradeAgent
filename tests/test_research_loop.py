"""Experimental integrity and numerical loop behavior, using fresh synthetic state."""

import copy
import json
import sqlite3
from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from tradeagent.cli import main
from tradeagent.research.attribution import attribute
from tradeagent.research.demo import fixture, run_demo
from tradeagent.research.domain import Bar, MarketSnapshot, Payload, timestamp
from tradeagent.research.evolution import evaluate, propose, retire
from tradeagent.research.expressions import plan
from tradeagent.research.feedback import import_feedback
from tradeagent.research.lab import scan, seed
from tradeagent.research.learning import learn_daily, retire_lesson
from tradeagent.research.outcomes import resolve
from tradeagent.research.store import Experience
from tradeagent.strategy import DEFAULT_PARAMS, BaselineStrategy

START = timestamp("2026-01-05T16:00:00Z")


@pytest.fixture
def store(tmp_path):
    db = Experience(tmp_path / "research")
    seed(db, START - 86400)
    yield db
    db.close()


def one_day(store, day=0, kind="synthetic", move=0.004):
    decision = START + day * 86400
    snapshot, future = fixture(decision, kind, move)
    scanned = scan(store, snapshot, decision)
    result = resolve(store, future, decision + 3600)
    return scanned, result


def test_prediction_and_deep_payload_are_immutable(store):
    snapshot, _ = fixture(START)
    prediction = BaselineStrategy(
        "opening_momentum", "v1", "opening_momentum", DEFAULT_PARAMS
    ).predict(snapshot, START)
    with pytest.raises(FrozenInstanceError):
        prediction.expected_return = 1
    with pytest.raises(TypeError):
        prediction.features.value["entry_close"] = 200
    payload = Payload.of({"values": [{"x": 1}]})
    with pytest.raises(TypeError):
        payload.value["values"][0]["x"] = 2
    scan(store, snapshot, START)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("UPDATE predictions SET expected_return=1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("DELETE FROM predictions")
    assert store.db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 7


@pytest.mark.parametrize("field", ["decision_bar", "quote", "option", "session_reference"])
def test_future_information_rejected(field):
    snapshot, _ = fixture(START)
    if field == "decision_bar":
        with pytest.raises(ValueError, match="future"):
            replace(
                snapshot,
                bars=snapshot.bars[:-1] + (replace(snapshot.bars[-1], available_at=START + 1),),
            )
    elif field == "quote":
        with pytest.raises(ValueError):
            replace(snapshot, quote_available_at=START + 1)
    elif field == "option":
        with pytest.raises(ValueError):
            replace(snapshot, options=(replace(snapshot.options[0], available_at=START + 1),))
    else:
        with pytest.raises(ValueError):
            replace(snapshot, session_open_time=START + 1)


def test_snapshot_replay_duplicates_and_revision_conflict(store):
    snapshot, _ = fixture(START)
    assert scan(store, snapshot, START)["status"] == "persisted"
    assert scan(store, snapshot, START + 1)["status"] == "duplicate_suppressed"
    with pytest.raises(ValueError, match="revised"):
        scan(store, replace(snapshot, bid=100.58), START + 1)
    with pytest.raises(ValueError, match="contemporaneous"):
        scan(store, fixture(START + 86400)[0], START + 86400 + 121)


def test_resolver_arithmetic_idempotence_and_immutability(store):
    snapshot, future = fixture(START)
    scan(store, snapshot, START)
    before = [
        tuple(r) for r in store.db.execute("SELECT * FROM predictions ORDER BY prediction_id")
    ]
    assert resolve(store, future, START + 3600)["resolved"] == 7
    assert resolve(store, future, START + 3601)["resolved"] == 0
    row = store.db.execute(
        "SELECT o.*,p.direction FROM outcomes o JOIN predictions p USING(prediction_id) WHERE p.strategy_id='opening_momentum'"
    ).fetchone()
    assert row["raw_return"] == pytest.approx(0.004)
    assert row["benchmark_return"] == pytest.approx(0.0003)
    assert row["residual_return"] == pytest.approx(0.0037)
    assert row["mfe"] == pytest.approx(0.007)
    assert row["mae"] == pytest.approx(-0.001)
    assert row["realized_volatility"] > 0
    assert before == [
        tuple(r) for r in store.db.execute("SELECT * FROM predictions ORDER BY prediction_id")
    ]
    with pytest.raises(sqlite3.IntegrityError):
        store.db.execute("UPDATE outcomes SET raw_return=1")


@pytest.mark.parametrize("missing", ["final", "middle", "benchmark"])
def test_missing_future_data_stays_unresolved(store, missing):
    snapshot, future = fixture(START)
    scan(store, snapshot, START)
    partial = copy.deepcopy(future)
    if missing == "final":
        partial["bars"] = [b for b in partial["bars"] if b["end"] != START + 3600]
    elif missing == "middle":
        partial["bars"] = [b for b in partial["bars"] if b["start"] != START]
    else:
        partial["bars"] = [b for b in partial["bars"] if b["symbol"] != "SPY"]
    assert resolve(store, partial, START + 3600)["resolved"] == 0
    assert resolve(store, future, START + 3601)["resolved"] == 7


def test_future_observation_and_correction_rejected(store):
    snapshot, future = fixture(START)
    scan(store, snapshot, START)
    with pytest.raises(ValueError, match="unavailable future"):
        resolve(store, future, START + 1)
    assert store.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    resolve(store, future, START + 3600)
    bad = copy.deepcopy(future)
    bad["bars"][0]["volume"] += 1
    with pytest.raises(ValueError, match="revision"):
        resolve(store, bad, START + 3601)


def test_quote_side_counterfactual_and_alpha_expression_attribution(store):
    one_day(store)
    attribute(store, START + 3601)
    p = store.db.execute(
        "SELECT prediction_id FROM predictions WHERE strategy_id='opening_momentum'"
    ).fetchone()[0]
    cf = store.db.execute(
        "SELECT * FROM counterfactuals WHERE prediction_id=? AND kind='LONG_CALL'", (p,)
    ).fetchone()
    assert cf["pnl"] == pytest.approx(-40.0)
    assert cf["exit"] == 1.7
    assert json.loads(cf["metadata"])["entry_quote"]["ask"] == 2.1
    a = store.db.execute("SELECT * FROM attributions WHERE prediction_id=?", (p,)).fetchone()
    assert a["alpha"] == "direction_correct"
    assert a["expression"] == "correct_thesis_poor_option_expression"
    assert a["execution"] == "unobserved_shadow_only"
    assert attribute(store, START + 3602) == 0


def test_missing_option_quote_has_no_fantasy_result(store):
    snapshot, future = fixture(START)
    scan(store, snapshot, START)
    future["options"] = []
    resolve(store, future, START + 3600)
    rows = store.db.execute("SELECT * FROM counterfactuals WHERE kind='LONG_CALL'").fetchall()
    assert all(r["status"] == "unavailable" and r["pnl"] is None for r in rows)


def test_counterfactual_repeated_run_deterministic(store):
    one_day(store)
    before = [
        tuple(r) for r in store.db.execute("SELECT * FROM counterfactuals ORDER BY expression_id")
    ]
    resolve(store, fixture(START)[1], START + 3605)
    assert before == [
        tuple(r) for r in store.db.execute("SELECT * FROM counterfactuals ORDER BY expression_id")
    ]


def test_abstention_controls_and_no_broker_knowledge(store):
    snapshot, _ = fixture(START)
    abstain = BaselineStrategy("null", "v1", "null_control", DEFAULT_PARAMS).predict(
        snapshot, START
    )
    assert abstain.direction == 0 and abstain.expected_return == 0
    random = BaselineStrategy("random", "v1", "random_control", DEFAULT_PARAMS)
    assert random.predict(snapshot, START) == random.predict(snapshot, START)
    assert random.predict(snapshot, START).direction in {-1, 1}
    result = scan(store, snapshot, START)
    for row in result["ranking"]:
        p = store.db.execute(
            "SELECT strategy_id FROM predictions WHERE prediction_id=?", (row["prediction_id"],)
        ).fetchone()[0]
        if p.endswith("control"):
            assert row["selected"] is False
    assert all(
        p["kind"] != "UNDERLYING"
        for p in result["trade_plans"]
        if store.db.execute(
            "SELECT direction FROM predictions WHERE prediction_id=?", (p["prediction_id"],)
        ).fetchone()[0]
        < 0
    )
    assert plan(abstain, snapshot, True).kind == "NO_TRADE"


def test_daily_learning_changes_future_state_without_rewriting_evidence(store):
    first, _ = one_day(store)
    before = [tuple(r) for r in store.db.execute("SELECT * FROM predictions")]
    learned = learn_daily(store, START + 3601, "synthetic")
    assert learned["status"] == "updated"
    assert learn_daily(store, START + 3602, "synthetic")["status"] == "unchanged"
    later = scan(store, fixture(START + 86400)[0], START + 86400)
    first_scores = {r["prediction_id"]: r["score"] for r in first["ranking"]}
    assert first_scores
    assert any(r["rationale"]["weight"] != 1 for r in later["ranking"])
    assert all(r["state_asof"] is None or r["state_asof"] < START + 86400 for r in later["ranking"])
    assert before == [
        tuple(r)
        for r in store.db.execute("SELECT * FROM predictions WHERE decision_time=?", (START,))
    ]
    assert store.db.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] > 0


def test_synthetic_evidence_does_not_change_prospective_selector(store):
    one_day(store)
    learn_daily(store, START + 3601, "synthetic")
    result = scan(store, fixture(START + 86400, "prospective")[0], START + 86400)
    assert all(r["rationale"]["weight"] == 1 for r in result["ranking"])


def test_versions_cannot_overwrite_parent_and_challengers_shadow_only(store):
    with pytest.raises(ValueError, match="overwritten"):
        store.register(
            "opening_momentum",
            "v1",
            "opening_momentum",
            {**DEFAULT_PARAMS, "threshold": 0.003},
            START,
        )
    created = propose(store, START + 1, kind="synthetic")
    assert len(created) == 5
    assert propose(store, START + 2, kind="synthetic") == []
    result = scan(store, fixture(START + 86400)[0], START + 86400)
    assert result["predictions"] == 12
    for row in result["ranking"]:
        version = store.db.execute(
            "SELECT strategy_version FROM predictions WHERE prediction_id=?",
            (row["prediction_id"],),
        ).fetchone()[0]
        if version.startswith("challenger-"):
            assert row["selected"] is False
    assert all(r["champion_version"] == "v1" for r in store.db.execute("SELECT * FROM strategies"))
    child = store.db.execute(
        "SELECT version FROM strategy_versions WHERE strategy_id='opening_momentum' AND role='challenger'"
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="promotion evidence"):
        store.db.execute(
            "UPDATE strategies SET champion_version=? WHERE strategy_id='opening_momentum'",
            (child,),
        )


def test_evaluation_reads_do_not_reuse_oos_evidence_as_new(store):
    propose(store, START - 1, kind="synthetic")
    one_day(store)
    first = evaluate(store, START + 3601)
    second = evaluate(store, START + 3602)
    assert len(first) == len(second) == 5
    assert all(r["n"] == 1 and r["decision"] == "continue_testing" for r in second)
    assert store.db.execute("SELECT COUNT(*) FROM challenger_evaluations").fetchone()[0] == 5
    with pytest.raises(sqlite3.IntegrityError):
        store.db.execute("UPDATE mutations SET evaluation_plan='{}'")


@pytest.mark.parametrize("direction", ["promote", "reject", "synthetic", "replay"])
def test_fixed_prospective_test_and_durable_losing_history(tmp_path, direction):
    db = Experience(tmp_path / direction)
    try:
        parent_threshold = 0.04 if direction != "reject" else 0.001
        child_threshold = 0.001 if direction != "reject" else 0.04
        db.register(
            "momentum",
            "v1",
            "opening_momentum",
            {**DEFAULT_PARAMS, "threshold": parent_threshold},
            START - 100,
        )
        proposal = {
            "strategy_id": "momentum",
            "parent_version": "v1",
            "hypothesis": "frozen test hypothesis",
            "params": {**DEFAULT_PARAMS, "threshold": child_threshold},
        }
        kind = direction if direction in {"synthetic", "replay"} else "prospective"
        mutation_id = propose(db, START - 1, proposal, kind)[0]
        for day in range(60):
            decision = START + day * 86400
            snapshot, future = fixture(decision, kind, 0.022)
            scan(db, snapshot, decision)
            resolve(db, future, decision + 3600)
        result = evaluate(db, START + 59 * 86400 + 3601)[0]
        expected = "promote" if direction == "promote" else "reject"
        assert result["decision"] == expected and result["n"] == 60
        assert db.db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 120
        assert db.db.execute("SELECT COUNT(*) FROM strategy_versions").fetchone()[0] == 2
        if expected == "promote":
            assert db.db.execute("SELECT champion_version FROM strategies").fetchone()[0] != "v1"
            assert db.db.execute("SELECT COUNT(*) FROM promotions").fetchone()[0] == 1
        else:
            assert db.db.execute("SELECT champion_version FROM strategies").fetchone()[0] == "v1"
            assert db.db.execute("SELECT mutation_id FROM rejections").fetchone()[0] == mutation_id
            with pytest.raises(sqlite3.IntegrityError):
                db.db.execute("DELETE FROM rejections")
    finally:
        db.close()


def test_retirement_preserves_history(store):
    one_day(store)
    retire(
        store,
        "opening_momentum",
        "v1",
        START + 4000,
        "explicit review found insufficient usefulness",
    )
    assert store.db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 7
    assert scan(store, fixture(START + 86400)[0], START + 86400)["predictions"] == 6


def test_versioned_new_database_leaves_execution_state_untouched(tmp_path):
    execution = tmp_path / "state.sqlite3"
    with sqlite3.connect(execution) as db:
        db.execute("CREATE TABLE intents(key TEXT PRIMARY KEY)")
        db.execute("INSERT INTO intents VALUES('valuable-v01-state')")
    before = execution.read_bytes()
    research = Experience(tmp_path)
    assert research.db.execute("PRAGMA user_version").fetchone()[0] == 1
    research.close()
    assert execution.read_bytes() == before
    again = Experience(tmp_path)
    assert again.db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
    again.close()


@pytest.mark.parametrize("bad_version", [0, 99])
def test_unknown_schema_is_not_silently_migrated(tmp_path, bad_version):
    with sqlite3.connect(tmp_path / "experience.sqlite3") as db:
        db.execute("CREATE TABLE valuable_data(x TEXT)")
        db.execute(f"PRAGMA user_version={bad_version}")
    with pytest.raises(ValueError, match="unknown experience schema"):
        Experience(tmp_path)


def test_sql_foreign_keys_and_outcome_time_guard(store):
    one_day(store)
    row = dict(store.db.execute("SELECT * FROM predictions LIMIT 1").fetchone())
    row.update(prediction_id="forged", symbol="OTHER")
    with pytest.raises(sqlite3.IntegrityError, match="mismatch"):
        store.insert("predictions", row)


def test_bar_and_snapshot_roundtrip():
    snapshot, _ = fixture(START)
    assert MarketSnapshot.from_dict(asdict(snapshot)) == snapshot
    with pytest.raises(ValueError):
        Bar("X", START, START + 10, START + 10, 1.0, 0.5, 0.1, 1.0)


def test_cli_and_complete_synthetic_demo(tmp_path, capsys, monkeypatch):
    def no_broker(*args, **kwargs):
        raise AssertionError("research must never connect to broker")

    monkeypatch.setattr("tradeagent.codex_bridge.CodexBridge", no_broker)
    result = run_demo(tmp_path / "demo")
    assert result["status"] == "passed" and result["prediction_count"] == 150
    assert result["selector_state_changed"] and result["real_broker_calls"] == 0
    assert result["challenger_paired_days"] == 9
    assert main(["inspect", "--state-dir", str(tmp_path / "demo")]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["outcomes"] == 150
    assert main(["scan", "--state-dir", str(tmp_path / "empty")]) == 2


def test_late_execution_feedback_does_not_rewrite_alpha_attribution(store):
    one_day(store)
    attribute(store, START + 3601)
    p = store.db.execute(
        "SELECT prediction_id FROM predictions WHERE strategy_id='opening_momentum'"
    ).fetchone()[0]
    before = tuple(
        store.db.execute("SELECT * FROM attributions WHERE prediction_id=?", (p,)).fetchone()
    )
    data = {
        "reconciled": True,
        "records": [
            {
                "prediction_id": p,
                "execution_key": "synthetic-fill-reference",
                "observed_at": START + 3602,
                "expected_entry": 100.0,
                "actual_entry": 101.0,
                "actual_exit": 102.0,
                "fees": 0.1,
                "quantity": 1.0,
                "status": "filled",
            }
        ],
    }
    assert import_feedback(store, data, START + 3603)["imported"] == 1
    assert import_feedback(store, data, START + 3604)["imported"] == 0
    row = store.db.execute("SELECT * FROM live_attributions").fetchone()
    assert row["classification"] == "fill_error"
    assert row["slippage_fraction"] == pytest.approx(0.01)
    assert row["realized_pnl"] == pytest.approx(0.9)
    assert before == tuple(
        store.db.execute("SELECT * FROM attributions WHERE prediction_id=?", (p,)).fetchone()
    )


def test_lesson_retirement_is_an_append_only_revision(store):
    one_day(store)
    learn_daily(store, START + 3601, "synthetic")
    lesson_id = store.db.execute("SELECT lesson_id FROM lessons LIMIT 1").fetchone()[0]
    retire_lesson(store, lesson_id, START + 3602, "superseded research hypothesis")
    rows = store.db.execute(
        "SELECT * FROM lessons WHERE lesson_id=? ORDER BY revision", (lesson_id,)
    ).fetchall()
    assert len(rows) == 2 and rows[0]["status"] != "retired" and rows[1]["status"] == "retired"


def test_opening_window_abstains_and_implementation_identity_fails_closed(store, monkeypatch):
    snapshot, _ = fixture(START)
    late = replace(snapshot, session_open_time=START - 7200)
    p = BaselineStrategy("opening", "v1", "opening_momentum", DEFAULT_PARAMS).predict(late, START)
    assert p.direction == 0
    monkeypatch.setattr("tradeagent.research.lab.implementation_hash", lambda: "changed-source")
    with pytest.raises(ValueError, match="implementation changed"):
        scan(store, snapshot, START)


def test_reports_compare_selector_baseline_and_exact_generations(store):
    one_day(store)
    report = store.inspect()
    assert len(report["version_regime_performance"]) == 7
    assert {r["mode"] for r in report["selector_comparison"]} == {"learned", "raw_baseline"}
    assert all(r["generation"] == 0 for r in report["version_regime_performance"])
