"""daily-news-v2: price-only and news-reading Codex on identical candidates and windows."""

import importlib.util
import json
import sys
from datetime import date

import pytest
from jsonschema.exceptions import ValidationError
from test_daily_research import data_for, valid_assessment
from test_opportunities import NOW, market

from tradeagent.prospective import daily
from tradeagent.prospective.assessor import assess_news
from tradeagent.research.opportunities import scan

DAY = date(2026, 10, 12)


def news_assessment(stances, sources=None):
    def assessor(report, now=None):
        items = []
        for i, c in enumerate(report["candidates"]):
            items.append(
                {
                    "symbol": c["symbol"],
                    "stance": stances[i % len(stances)],
                    "rank": i + 1,
                    "thesis": "Synthetic news read",
                    "catalyst": "Synthetic headline",
                    "priced_in": "Partly",
                    "contradictions": [],
                    "invalidation": "Headline retracted",
                    "hypothesis_type": "price_action",
                    "sources": sources or [],
                }
            )
        return {"assessments": items}, {"model": "synthetic-news", "web_searches": 2}

    return assessor


def pipeline_for(tmp_path, news_assessor, assessor=valid_assessment):
    clock = [daily.window(DAY, daily.NEWS_PROTOCOL)[0]]

    def collector(symbols, provider, helper, *, session=None):
        return data_for(session or DAY, clock[0])

    pipeline = daily.Pipeline(
        tmp_path,
        collector=collector,
        assessor=assessor,
        news_assessor=news_assessor,
        clock=lambda: clock[0],
        evidence_kind="synthetic",
        protocol=daily.NEWS_PROTOCOL,
    )
    return pipeline, clock


def test_news_protocol_freezes_five_paired_arms_and_resolves(tmp_path):
    pipeline, clock = pipeline_for(tmp_path, news_assessment(["avoid"]))
    try:
        result = pipeline.tick()
        assert result["run"]["status"] == "COMPLETED"
        decision = pipeline.store.decision()
        assert decision["research_protocol"] == daily.NEWS_PROTOCOL
        assert set(decision["comparisons"]) == set(daily.NEWS_ARMS)
        # Price-only Codex said long; the news read said avoid on the same candidates.
        assert decision["comparisons"]["codex_price"]["shadow_direction"] == 1
        assert decision["comparisons"]["codex_news"]["shadow_direction"] == 0
        assert decision["comparisons"]["quant_codex_news"]["shadow_direction"] == 0
        assert decision["news_assessment_status"] == "VALID"
        tags = {a.get("assessor") for a in pipeline.store.records("assessments")}
        assert tags == {"price", "news"}
        clock[0] = decision["outcome_exit_time"]
        status = pipeline.tick()
        pool = status["comparison"]["evidence_pools"]["synthetic"]
        assert status["comparison"]["protocol"] == daily.NEWS_PROTOCOL
        assert pool["valid_paired_days"] == 1
        assert pool["arms"]["codex_news"]["paired_excess_over_benchmark"]["mean"] == 0
        news_vs_price = pool["paired_excess_differences"]["codex_news"]["codex_price"]
        assert news_vs_price["days"] == 1
        assert status["orders_submitted"] == 0
    finally:
        pipeline.store.close()


def test_news_failure_leaves_price_arms_valid(tmp_path):
    def broken(report, now=None):
        raise ValueError("Codex assessment deadline exceeded")

    pipeline, _ = pipeline_for(tmp_path, broken)
    try:
        result = pipeline.tick()
        assert result["run"]["status"] == "INCOMPLETE"
        assert result["run"]["news_assessment_error"] == "ValueError"
        arms = pipeline.store.decision()["comparisons"]
        assert arms["codex_news"]["status"] == arms["quant_codex_news"]["status"] == "UNAVAILABLE"
        assert arms["codex_price"]["status"] == arms["quant_only"]["status"] == "VALID"
    finally:
        pipeline.store.close()


def test_citations_are_sanitized_before_freezing(tmp_path):
    sources = [
        {
            "url": "https://example.com/a",
            "title": "Guidance raised",
            "published_at": "2026-10-12T12:00:00Z",
            "claim": "Company raised guidance",
            "role": "supporting",
        },
        {
            "url": "http://insecure.example.com/b",
            "title": "",
            "published_at": None,
            "claim": "Dropped: not https",
            "role": "context",
        },
        {
            "url": "https://example.com/future",
            "title": "",
            "published_at": "2099-01-01T00:00:00Z",
            "claim": "Future date is nulled",
            "role": "context",
        },
    ]
    pipeline, _ = pipeline_for(tmp_path, news_assessment(["long"], sources))
    try:
        pipeline.tick()
        news = [a for a in pipeline.store.records("assessments") if a["assessor"] == "news"]
        kept = news[0]["sources"]
        assert [s["url"] for s in kept] == ["https://example.com/a", "https://example.com/future"]
        assert kept[0]["published_at"] is not None and kept[1]["published_at"] is None
        assert kept[1]["title"] == "https://example.com/future"
    finally:
        pipeline.store.close()


def test_protocols_cannot_share_a_state_directory(tmp_path):
    pipeline, _ = pipeline_for(tmp_path, news_assessment(["long"]))
    pipeline.tick()
    pipeline.store.close()
    v1 = daily.Pipeline(
        tmp_path,
        collector=lambda *a, **k: pytest.fail("no capture"),
        assessor=valid_assessment,
        clock=lambda: daily.window(DAY)[0],
        evidence_kind="synthetic",
    )
    try:
        with pytest.raises(ValueError, match="deployment differs"):
            v1.tick()
    finally:
        v1.store.close()


def test_v1_window_and_config_are_unchanged():
    start, end = daily.window(DAY)
    assert end - start == 180
    assert daily.CONFIG["decision_window_ny"] == "10:00:00-10:03:00"
    news_start, news_end = daily.window(DAY, daily.NEWS_PROTOCOL)
    assert news_start - start == 1800 and news_end - news_start == 420


def fake_codex(tmp_path, document, events):
    executable = tmp_path / "codex"
    executable.write_text(
        "#!/usr/bin/env python3\nimport sys,json\nfrom pathlib import Path\n"
        + "args=sys.argv\n"
        + "Path(args[args.index('--output-last-message')+1]).write_text("
        + repr(json.dumps(document))
        + ")\n"
        + "Path(args[args.index('--cd')+1]).joinpath('argv.json').write_text(json.dumps(args))\n"
        + "print("
        + repr("\n".join(json.dumps(e) for e in events))
        + ")\n"
    )
    executable.chmod(0o700)
    return executable


def cli_document(report):
    return {
        "assessments": [
            {
                **valid_assessment(report)[0]["assessments"][0],
                "hypothesis_type": "event_driven",
                "sources": [
                    {
                        "url": "https://example.com/x",
                        "title": "t",
                        "published_at": None,
                        "claim": "c",
                        "role": "supporting",
                    }
                ],
            }
        ]
    }


def test_news_cli_allows_web_search_and_counts_it(tmp_path):
    report = scan(market(), now=NOW, candidate_count=1)
    searches = [
        {"type": "item.completed", "item": {"id": f"s{i}", "type": "web_search"}} for i in range(3)
    ]
    executable = fake_codex(tmp_path, cli_document(report), searches)
    document, provenance = assess_news(report, executable=str(executable), now=NOW)
    assert provenance["web_searches"] == 3 and provenance["web_search_mode"] == "live"
    assert document["assessments"][0]["sources"][0]["url"] == "https://example.com/x"


@pytest.mark.parametrize("item", ["command_execution", "mcp_tool_call", "file_change"])
def test_news_cli_still_rejects_every_other_tool(tmp_path, item):
    report = scan(market(), now=NOW, candidate_count=1)
    executable = fake_codex(tmp_path, cli_document(report), [{"item": {"type": item}}])
    with pytest.raises(ValueError, match="tool activity"):
        assess_news(report, executable=str(executable), now=NOW)


def test_news_cli_rejects_missing_citation_fields(tmp_path):
    report = scan(market(), now=NOW, candidate_count=1)
    document = cli_document(report)
    del document["assessments"][0]["sources"][0]["published_at"]
    executable = fake_codex(tmp_path, document, [])
    with pytest.raises(ValidationError):
        assess_news(report, executable=str(executable), now=NOW)


def test_installer_writes_a_separate_news_timer(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("install_shadow", "scripts/install_shadow.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    interpreter = tmp_path / "venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("synthetic interpreter")
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(installer.subprocess, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install_shadow.py",
            "--daily",
            "--protocol",
            "daily-news-v2",
            "--python",
            str(interpreter),
            "--state-dir",
            str(tmp_path / "news-state"),
        ],
    )
    installer.main()
    units = tmp_path / ".config/systemd/user"
    service = (units / "tradeagent-daily-news.service").read_text()
    timer = (units / "tradeagent-daily-news.timer").read_text()
    assert service.rstrip().count("--protocol daily-news-v2") == 1
    assert "OnCalendar=Mon..Fri *-*-* 10:30:00 America/New_York" in timer
    assert "Unit=tradeagent-daily-news.service" in timer
    assert not (units / "tradeagent-daily-research.timer").exists()
    assert calls[-1][-1] == "tradeagent-daily-news.timer"
