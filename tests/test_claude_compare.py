"""daily-news-claude-v1: Claude on the daily-news-v2 shortlist, paired with Codex."""

import hashlib
import json
from pathlib import Path

import pytest
from test_daily_news import DAY, news_assessment, pipeline_for

from tradeagent.prospective import claude_compare, daily


def v2_session(tmp_path, codex_news=("avoid",), codex_price=("long",)):
    """A completed synthetic daily-news-v2 session; returns the pipeline and its clock."""
    pipeline, clock = pipeline_for(
        tmp_path / "v2", news_assessment(list(codex_news), price_stances=codex_price)
    )
    assert pipeline.tick()["run"]["status"] == "COMPLETED"
    return pipeline, clock


def companion_for(tmp_path, clock, assessor):
    def sleep(seconds):
        clock[0] += seconds

    return claude_compare.Companion(
        tmp_path / "claude",
        tmp_path / "v2",
        assessor=assessor,
        clock=lambda: clock[0],
        sleep=sleep,
    )


def tree(path):
    return {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(path).rglob("*"))
        if p.is_file()
    }


def test_claude_answers_the_v2_question_and_is_scored_on_the_v2_window(tmp_path):
    pipeline, clock = v2_session(tmp_path)
    claude = news_assessment(["long"], price_stances=("watch",))
    companion = companion_for(tmp_path, clock, claude)
    before = tree(tmp_path / "v2")
    try:
        status = companion.tick()
        assert status["run"]["status"] == "COMPLETED" and status["orders_submitted"] == 0
        assert sorted(claude.calls) == [False, True]
        v2 = pipeline.store.decision()
        decision = companion.store.records("decisions")[0]
        assert decision["follows"]["decision_id"] == v2["decision_id"]
        assert decision["scan_id"] == v2["scan_id"]
        for key in ("outcome_entry_time", "outcome_exit_time", "comparison_universe"):
            assert decision[key] == v2[key]
        arms = decision["comparisons"]
        assert set(arms) == set(claude_compare.ARMS)
        # Same candidates, opposite answers: Claude went long with news, Codex avoided.
        assert arms["claude_news"]["shadow_direction"] == 1
        assert arms["codex_news"]["shadow_direction"] == 0
        assert arms["claude_price"]["shadow_direction"] == 0
        assert arms["codex_price"]["shadow_direction"] == 1
        for name in claude_compare.CODEX_ARMS + claude_compare.SHARED_ARMS:
            assert {k: v for k, v in arms[name].items() if k != "source"} == v2["comparisons"][name]
        tags = {a["assessor"] for a in companion.store.records("assessments")}
        assert tags == {"price", "news"}
        assert tree(tmp_path / "v2") == before  # read-only follower

        clock[0] = v2["outcome_exit_time"]
        pipeline.tick()  # v2 resolves its own outcome first
        status = companion.tick()
        outcome = companion.store.records("outcomes")[0]
        v2_outcome = pipeline.store.records("outcomes")[0]
        symbol = arms["claude_news"]["symbol"]
        assert outcome["comparisons"]["claude_news"] == v2_outcome["candidate_outcomes"][symbol]
        assert outcome["comparisons"]["claude_price"] == {
            "gross": 0,
            "base_net": 0,
            "stress_net": 0,
        }
        assert outcome["comparisons"]["codex_news"] == v2_outcome["comparisons"]["codex_news"]
        pool = status["comparison"]["evidence_pools"]["synthetic"]
        assert pool["valid_paired_days"] == 1
        assert pool["paired_excess_differences"]["claude_news"]["codex_news"]["days"] == 1
        assert pool["same_pick_as_codex"]["claude_news"] == {"valid_days": 1, "same_pick_days": 0}
        assert "Claude + news" in (tmp_path / "claude" / "daily-report.md").read_text()
    finally:
        companion.close()
        pipeline.store.close()


def test_an_answer_frozen_after_the_v2_entry_is_not_scored(tmp_path):
    pipeline, clock = v2_session(tmp_path)
    entry_after = pipeline.store.decision()["entry_after"]
    working = news_assessment(["long"])

    def slow(report, now=None, search=True):
        clock[0] = entry_after + 1
        return working(report, now=now, search=search)

    companion = companion_for(tmp_path, clock, slow)
    try:
        status = companion.tick()
        assert status["run"]["failure_kind"] == "CLAUDE_ASSESSMENT_UNAVAILABLE"
        decision = companion.store.records("decisions")[0]
        for name in claude_compare.CLAUDE_ARMS:
            assert decision["comparisons"][name]["status"] == "UNAVAILABLE"
            assert decision["comparisons"][name]["shadow_direction"] == 0
        assert decision["claude"]["news"]["late"] is True
    finally:
        companion.close()
        pipeline.store.close()


def test_a_failed_claude_call_is_unavailable_never_cash(tmp_path):
    pipeline, clock = v2_session(tmp_path)

    def broken(report, now=None, search=True):
        raise ValueError("Claude Code is not signed in with a Claude subscription")

    companion = companion_for(tmp_path, clock, broken)
    try:
        companion.tick()
        decision = companion.store.records("decisions")[0]
        assert decision["claude"]["news"]["error"] == "ValueError"
        clock[0] = decision["outcome_exit_time"]
        pipeline.tick()
        status = companion.tick()
        outcome = companion.store.records("outcomes")[0]
        assert all(outcome["comparisons"][n] is None for n in claude_compare.CLAUDE_ARMS)
        pool = status["comparison"]["evidence_pools"]["synthetic"]
        assert pool["valid_paired_days"] == 0
        assert pool["arms"]["claude_news"]["unavailable_days"] == 1
        assert pool["paired_excess_differences"]["claude_news"]["codex_news"]["days"] == 0
    finally:
        companion.close()
        pipeline.store.close()


def test_without_a_v2_scan_claude_is_never_asked(tmp_path):
    clock = [daily.window(DAY, daily.NEWS_PROTOCOL)[0]]
    (tmp_path / "v2").mkdir()
    (tmp_path / "v2" / "daily-deployment.json").write_text(
        json.dumps({"config": {"protocol": daily.NEWS_PROTOCOL, "evidence_kind": "synthetic"}})
    )
    companion = companion_for(
        tmp_path, clock, lambda *a, **k: pytest.fail("no scan, no assessment")
    )
    try:
        status = companion.tick()
        assert status["run"]["failure_kind"] == "V2_SCAN_UNAVAILABLE"
        assert clock[0] >= daily.window(DAY, daily.NEWS_PROTOCOL)[1]
        assert companion.store.records("decisions") == []
    finally:
        companion.close()


def test_a_late_start_skips_the_session(tmp_path):
    pipeline, clock = v2_session(tmp_path)
    clock[0] = pipeline.store.scan()["decision_time"] + claude_compare.START_LAG_SECONDS + 1
    companion = companion_for(tmp_path, clock, lambda *a, **k: pytest.fail("too late to ask"))
    try:
        assert companion.tick()["run"]["failure_kind"] == "LATE_START"
    finally:
        companion.close()
        pipeline.store.close()


def test_rerun_and_crash_recovery_never_ask_twice(tmp_path):
    pipeline, clock = v2_session(tmp_path)
    claude = news_assessment(["long"])
    companion = companion_for(tmp_path, clock, claude)
    try:
        companion.tick()
        companion.tick()
        assert len(claude.calls) == 2
        assert len(companion.store.records("decisions")) == 1
    finally:
        companion.close()
        pipeline.store.close()


def test_follow_dir_must_be_daily_news_v2(tmp_path):
    (tmp_path / "v1").mkdir()
    (tmp_path / "v1" / "daily-deployment.json").write_text(
        json.dumps({"config": {"protocol": "daily-four-arm-v1"}})
    )
    with pytest.raises(ValueError, match="not a daily-news-v2"):
        claude_compare.Follow(tmp_path / "v1")
    with pytest.raises(ValueError, match="no daily-news-v2"):
        claude_compare.Follow(tmp_path / "missing")


def test_deployment_is_frozen(tmp_path):
    pipeline, clock = v2_session(tmp_path)
    companion = companion_for(tmp_path, clock, news_assessment(["long"]))
    try:
        companion.tick()
        path = tmp_path / "claude" / "daily-deployment.json"
        saved = json.loads(path.read_text())
        saved["config"]["claude_model"] = "another-model"
        path.write_text(json.dumps(saved))
        with pytest.raises(ValueError, match="deployment differs"):
            companion.tick()
    finally:
        companion.close()
        pipeline.store.close()
