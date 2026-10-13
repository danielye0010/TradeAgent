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


def news_assessment(stances, sources=None, price_stances=("long",)):
    """One assessor for both arms, as in production; search picks the stance set."""
    calls = []

    def assessor(report, now=None, search=True):
        calls.append(search)
        chosen = stances if search else price_stances
        items = []
        for i, c in enumerate(report["candidates"]):
            items.append(
                {
                    "symbol": c["symbol"],
                    "stance": chosen[i % len(chosen)],
                    "rank": i + 1,
                    "thesis": "Synthetic read",
                    "catalyst": "Synthetic headline" if search else "Price action only",
                    "priced_in": "Partly",
                    "contradictions": [],
                    "invalidation": "Headline retracted",
                    "hypothesis_type": "price_action",
                    "sources": (sources or []) if search else [],
                }
            )
        return {"assessments": items}, {"model": "synthetic", "web_searches": 2 if search else 0}

    assessor.calls = calls
    return assessor


def pipeline_for(tmp_path, news_assessor):
    clock = [daily.window(DAY, daily.NEWS_PROTOCOL)[0]]

    def collector(symbols, provider, helper, *, session=None):
        return data_for(session or DAY, clock[0])

    pipeline = daily.Pipeline(
        tmp_path,
        collector=collector,
        assessor=lambda report: pytest.fail("v2 must not use the v1 price-only prompt"),
        news_assessor=news_assessor,
        clock=lambda: clock[0],
        evidence_kind="synthetic",
        protocol=daily.NEWS_PROTOCOL,
    )
    return pipeline, clock


def test_news_protocol_freezes_five_paired_arms_and_resolves(tmp_path):
    assessor = news_assessment(["avoid"])
    pipeline, clock = pipeline_for(tmp_path, assessor)
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
        # Both arms come from the same assessor; only the search flag differs.
        assert sorted(assessor.calls) == [False, True]
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


def test_slow_news_refreshes_shared_market_data_without_rescanning(tmp_path):
    working = news_assessment(["long"])
    reports, captures = [], []
    start = daily.window(DAY, daily.NEWS_PROTOCOL)[0]

    def assessor(report, now=None, search=True):
        reports.append((report, now))
        if search:
            clock[0] = start + 300  # Simulated Codex deadline; no wall-clock wait.
        return working(report, now=now, search=search)

    pipeline, clock = pipeline_for(tmp_path, assessor)

    def collector(symbols, provider, helper, *, session=None):
        assert set(symbols) == set(daily.UNIVERSE)
        data = data_for(session or DAY, clock[0])
        if captures:
            for quote in data["quotes"].values():
                quote["ask"] += 0.18  # Decision costs must use the refreshed quotes.
            clock[0] += 2  # Freeze time must include market-data collection latency.
        captures.append(data)
        return data

    pipeline.collector = collector
    try:
        result = pipeline.tick()
        assert result["run"]["status"] == "COMPLETED"
        decision = pipeline.store.decision()
        assert len(captures) == 2
        assert len(reports) == 2 and sorted(working.calls) == [False, True]
        assert reports[0][0] is reports[1][0]
        assert all(now == start for _, now in reports)
        report = reports[0][0]
        assert report["market_observed_at"] == start
        assert decision["scan_id"] == report["scan_id"]
        assert decision["candidate_pool"] == report["candidate_pool"]
        assert decision["market_observed_at"] == start + 300
        assert decision["freeze_time"] == start + 302
        assert set(decision["comparison_universe"]) == {c["symbol"] for c in report["candidates"]}
        assert set(decision["comparisons"]) == set(daily.NEWS_ARMS)
        assert all(a["status"] == "VALID" for a in decision["comparisons"].values())
        for candidate in decision["quantitative_observations"]:
            assert candidate["predictions"] and not candidate["limitations"]
            quote = captures[-1]["quotes"][candidate["symbol"]]
            spread = (quote["ask"] - quote["bid"]) / ((quote["ask"] + quote["bid"]) / 2) * 10000
            assert all(
                p["features"]["observed_spread_bps"] == pytest.approx(spread)
                and p["decision_time"] == decision["freeze_time"]
                for p in candidate["predictions"]
            )
        assert len(pipeline.store.records("scans")) == 1
        assert len(list((tmp_path / "captures").glob("*.json"))) == 2
        assert decision["selected_plan"] is None and result["orders_submitted"] == 0
    finally:
        pipeline.store.close()


def test_news_failure_leaves_price_arms_valid(tmp_path):
    working = news_assessment(["long"])

    def broken(report, now=None, search=True):
        if search:
            raise ValueError("Codex assessment deadline exceeded")
        return working(report, now=now, search=search)

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


def recording_codex(tmp_path, document):
    log = tmp_path / "calls.jsonl"
    executable = tmp_path / "codex-recorder"
    executable.write_text(
        "#!/usr/bin/env python3\nimport sys,json\nfrom pathlib import Path\n"
        + "args=sys.argv\nprompt=sys.stdin.read()\n"
        + f"with open({str(log)!r},'a') as f: f.write(json.dumps({{'argv':args,'prompt':prompt}})+'\\n')\n"
        + "Path(args[args.index('--output-last-message')+1]).write_text("
        + repr(json.dumps(document))
        + ")\n"
    )
    executable.chmod(0o700)
    return executable, log


def test_price_and_news_arms_differ_only_in_search(tmp_path):
    report = scan(market(), now=NOW, candidate_count=1)
    document = cli_document(report)
    document["assessments"][0]["sources"] = []
    executable, log = recording_codex(tmp_path, document)
    _, price = assess_news(report, executable=str(executable), now=NOW, search=False)
    _, news = assess_news(report, executable=str(executable), now=NOW, search=True)
    first, second = (json.loads(line) for line in log.read_text().splitlines())

    def neutral(call):
        argv = [a for a in call["argv"][1:] if not a.startswith("web_search=")]
        # Temporary paths differ per run and carry no treatment.
        return [a if "tradeagent-assessor-" not in a else "TMP" for a in argv]

    assert neutral(first) == neutral(second)
    assert 'web_search="disabled"' in first["argv"] and 'web_search="live"' in second["argv"]
    shared = [p for p in first["prompt"].split(". ") if p in second["prompt"]]
    assert len(shared) >= len(first["prompt"].split(". ")) - 1
    assert price["web_search_mode"] == "disabled" and news["web_search_mode"] == "live"


def test_price_arm_rejects_any_search_activity(tmp_path):
    report = scan(market(), now=NOW, candidate_count=1)
    executable = fake_codex(
        tmp_path, cli_document(report), [{"item": {"id": "s", "type": "web_search"}}]
    )
    with pytest.raises(ValueError, match="tool activity"):
        assess_news(report, executable=str(executable), now=NOW, search=False)


def test_v3_news_requires_actual_search_and_cited_source(tmp_path):
    report = scan(market(), now=NOW, candidate_count=1)
    report["research_protocol"] = "daily-bidirectional-v3"
    doc = cli_document(report)
    for searches, sources in (
        ([], doc["assessments"][0]["sources"]),
        ([{"item": {"type": "web_search", "id": "one"}}], []),
    ):
        document = {"assessments": [{**doc["assessments"][0], "sources": sources}]}
        executable = fake_codex(tmp_path, document, searches)
        with pytest.raises(ValueError, match="observed web search"):
            assess_news(report, executable=str(executable), now=NOW)
        assert (
            assess_news(
                report, executable=str(fake_codex(tmp_path, document, [])), now=NOW, search=False
            )[1]["web_searches"]
            == 0
        )


def test_installer_prepares_minute_aligned_v3_without_enabling_or_changing_v1_v2(
    tmp_path, monkeypatch
):
    spec = importlib.util.spec_from_file_location("install_v3", "scripts/install_shadow.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    from test_bidirectional import profile

    interpreter = tmp_path / "venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    measured = tmp_path / "profile.json"
    measured.write_text(json.dumps(profile()))
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(installer.subprocess, "run", lambda args, **kwargs: calls.append(args))
    units = tmp_path / ".config/systemd/user"
    units.mkdir(parents=True)
    v1, v2 = units / "tradeagent-daily-research.timer", units / "tradeagent-daily-news.timer"
    v1.write_text("frozen v1")
    v2.write_text("frozen v2")
    installer.install_daily(
        tmp_path / "state",
        tmp_path / "helper",
        interpreter,
        "daily-bidirectional-v3",
        universe_profile=measured,
        enable=False,
    )
    timer = (units / "tradeagent-daily-bidirectional.timer").read_text()
    service = (units / "tradeagent-daily-bidirectional.service").read_text()
    assert "10:30..59:00 America/New_York" in timer and "11:00..59:00 America/New_York" in timer
    assert "12:00:00 America/New_York" in timer and "17:10:00 America/New_York" in timer
    assert "--universe-profile " + str(measured) in service
    assert "--protocol daily-bidirectional-v3" in service
    assert "--user" in calls[0] and "verify" in calls[0]
    assert not any("enable" in command for command in calls)
    assert v1.read_text() == "frozen v1" and v2.read_text() == "frozen v2"


def test_code_mode_configuration_keeps_price_news_arms_identical_and_shell_disabled(tmp_path):
    report = scan(market(), now=NOW, candidate_count=1)
    doc = cli_document(report)
    executable, log = recording_codex(tmp_path, doc)
    assess_news(report, executable=str(executable), now=NOW, search=False)
    assess_news(report, executable=str(executable), now=NOW, search=True)
    first, second = (json.loads(line) for line in log.read_text().splitlines())
    for call in (first, second):
        args = call["argv"]
        assert "features.code_mode=true" in args and "features.code_mode_host=true" in args
        assert args.index("features.code_mode=true") > args.index("exec")
        assert [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--disable"].count(
            "shell_tool"
        ) == 1
        assert "unified_exec" in args and "apps" in args and "plugins" in args


def test_readable_pnl_preserves_pending_and_unavailable_without_bearish_substitution(tmp_path):
    from test_bidirectional import assessment, candidate

    from tradeagent.research import bidirectional
    from tradeagent.research.opportunity_workflow import DailyResearch

    store = DailyResearch(tmp_path)
    arms = bidirectional.select(
        [candidate()],
        assessment("bearish"),
        assessment("bearish"),
        "VALID",
        "VALID",
        "seed",
        1791900000,
    )
    decision = {
        "decision_id": "report",
        "research_protocol": bidirectional.PROTOCOL,
        "decision_time": 1791900000,
        "outcome_entry_time": 1791900300,
        "outcome_exit_time": 1791903900,
        "comparisons": arms,
        "put_selections": {},
        "quantitative_observations": [candidate(), {**candidate(), "symbol": "SPY"}],
    }
    try:
        store.save("decisions", decision, "report", 1791900000)
        assert "PENDING" in bidirectional.pnl_report(store)
        observed = {
            s: {"gross": -0.02, "entry_price": 100, "exit_price": 98} for s in ("QQQ", "SPY")
        }
        prices = bidirectional.resolve_arms(decision, observed, [])
        store.save(
            "outcomes", {"decision_id": "report", "comparisons": prices}, "report", 1791903960
        )
        text = bidirectional.pnl_report(store)
        funded, theory = text.split("## Theoretical underlying benchmark")
        assert "| QQQ | BEARISH | LONG_PUT | — | — |" in funded
        assert "UNAVAILABLE" in funded and "18.02" not in funded
        assert "18.02" in theory and "non-executable" in theory
        assert "SPY" in funded and "CASH" in funded
    finally:
        store.close()
