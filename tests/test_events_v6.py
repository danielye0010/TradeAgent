"""Offline SEC, CLI, timing, fair cohorts and durable forward outcomes."""

import copy
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from tradeagent.calendar import session_bounds
from tradeagent.prospective import event_assessor as ai
from tradeagent.prospective import events as v6
from tradeagent.prospective import sec_events as sec
from tradeagent.prospective.event_reporting import compare, report
from tradeagent.prospective.event_store import Store


def instant(day=date(2026, 10, 19), hour=10, minute=1, second=45):
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=v6.NY).timestamp()


def submission(accepted, form="8-K", items="2.02,9.01"):
    return {
        "cik": "320193",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-26-000018"],
                "form": [form],
                "primaryDocument": ["aapl-20261019.htm"],
                "acceptanceDateTime": [datetime.fromtimestamp(accepted, v6.NY).isoformat()],
                "filingDate": ["2026-10-19"],
                "items": [items],
            }
        },
    }


def assessment(recommendation="LONG"):
    return {
        "assessments": [
            {
                "symbol": "AAPL",
                "rank": 1,
                "event_classification": "earnings",
                "verified_facts": [
                    {
                        "fact": "Revenue grew.",
                        "source_url": "https://www.sec.gov/Archives/test.htm",
                        "source_quote": "Revenue grew 10 percent.",
                    }
                ],
                "interpretation": "bullish" if recommendation == "LONG" else "neutral",
                "surprise_status": "unknown",
                "surprise_reason": "No genuine consensus supplied.",
                "relevance": "multi_day",
                "main_uncertainty": "Price reaction may already reflect the filing.",
                "contradictions": ["Historical comparisons do not establish expectations."],
                "recommendation": recommendation,
                "priced_in": "Unknown without prior consensus.",
            }
        ]
    }


def seed(p, now, event_id="event-a", symbol="AAPL"):
    row = sec.metadata(submission(now - 180), symbol, now - 120, p.store.activated_at)[0]
    row["id"] = event_id
    p.store.add("event", event_id, row)
    content = {
        "event_id": event_id,
        "first_retrieved_at": now - 100,
        "available_at": now - 10,
        "sources": [
            {
                "url": "https://www.sec.gov/Archives/test.htm",
                "sha256": "abc",
                "text": "Revenue grew 10 percent. No consensus is supplied.",
                "received_at": now - 100,
            }
        ],
        "extract": "Revenue grew 10 percent. No consensus is supplied.",
        "content_hash": "abc",
    }
    p.store.add("content", event_id, content)
    return row, content


def data(now, symbols, price=101):
    end = int(now // 60) * 60
    opening, closing = session_bounds(v6.session(now))
    previous = v6.prior_session(v6.session(now))
    bars, quotes, refs = [], {}, {}
    for symbol in symbols:
        base = 200 if symbol == "SPY" else 100
        for i in range(30):
            start = end - 1800 + i * 60
            if start < opening:
                continue
            bars.append(
                dict(
                    symbol=symbol,
                    start=start,
                    end=start + 60,
                    available_at=now,
                    open=base,
                    high=base + 0.01,
                    low=base - 0.01,
                    close=base,
                    volume=30000,
                )
            )
        value = 200 if symbol == "SPY" else price
        quotes[symbol] = dict(
            bid=value,
            ask=value + 0.02,
            asof=now,
            observed_at=now,
            bid_asof=now,
            ask_asof=now,
            active=True,
        )
        refs[symbol] = dict(
            previous_close=base,
            adjusted_previous_close=base,
            previous_close_time=session_bounds(previous)[1],
            previous_close_date=previous.isoformat(),
        )
    return dict(
        source="offline-fixture",
        evidence_kind="prospective",
        symbols=list(symbols),
        observed_at=now,
        requested_at=now,
        bars=bars,
        quotes=quotes,
        references=refs,
        session_open=opening,
        session_close=closing,
        limitations=[],
    )


@pytest.fixture
def pipeline(tmp_path):
    clock = [instant() - 300]
    calls = []
    prices = [101]

    def collector(symbols, **kwargs):
        calls.append((list(symbols), kwargs))
        return data(clock[0], symbols, prices[0])

    def assessor(event, content, context, **kwargs):
        clock[0] += 90
        return assessment()["assessments"][0], {"authentication": "fixture", "tool_calls": 0}

    p = v6.Pipeline(
        tmp_path,
        clock=lambda: clock[0],
        collector=collector,
        assessor=assessor,
        guard=lambda *a: True,
    )
    clock[0] = instant()
    yield p, clock, calls, prices
    p.close()


def test_sec_metadata_exact_timestamps_historical_amendment_unknown():
    now = instant()
    row = sec.metadata(submission(now - 10), "AAPL", now, now - 20)[0]
    assert row["prospective"] and row["accepted_at"] == now - 10
    assert row["first_discovered_at"] == now and row["category"] == "earnings"
    assert not sec.metadata(submission(now - 30), "AAPL", now, now - 20)[0]["prospective"]
    amended = sec.metadata(submission(now - 10, "8-K/A"), "AAPL", now, now - 20)[0]
    assert amended["rejection"] == "AMENDMENT" and not amended["material"]
    missing = submission(now - 10)
    missing["filings"]["recent"]["acceptanceDateTime"] = ["2026-10-19"]
    assert not sec.metadata(missing, "AAPL", now, now - 20)[0]["prospective"]
    bad = submission(now)
    bad["filings"]["recent"]["primaryDocument"] = ["../credentials"]
    with pytest.raises(ValueError, match="unsafe"):
        sec.metadata(bad, "AAPL", now, now - 20)


def test_sec_issuer_scope_excludes_etfs():
    assert len(sec.ISSUERS) == 21
    assert not set(sec.ISSUERS) & sec.ETFS


class Response:
    status = 200
    headers = {"ETag": '"abc"', "Last-Modified": "Mon, 19 Oct 2026 12:00:00 GMT"}

    def __init__(self, body):
        self.body = body

    def read(self, n):
        return self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_sec_conditional_cache_rate_limit_and_403_no_retries(tmp_path):
    clock = [instant()]
    store = Store(tmp_path, lambda: clock[0])
    requests = []

    def opened(request, timeout):
        requests.append(request)
        if len(requests) == 2:
            raise HTTPError(request.full_url, 304, "not modified", {}, None)
        if len(requests) == 3:
            raise HTTPError(request.full_url, 403, "forbidden", {}, None)
        return Response(b'{"cik":320193}')

    client = sec.Client(
        store,
        "Research Test contact@example.org",
        opener=SimpleNamespace(open=opened),
        clock=lambda: clock[0],
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
    )
    try:
        url = sec.SUBMISSIONS.format(cik=320193)
        body, first, digest = client.get(url)
        cached, second, same = client.get(url)
        assert cached == body and same == digest and second - first >= 0.75
        assert requests[1].get_header("If-none-match") == '"abc"'
        with pytest.raises(sec.SECUnavailable) as error:
            client.get(url)
        assert error.value.status == 403 and len(requests) == 3
        assert store.rows("sec_http")[-1]["status"] == 403
        with pytest.raises(ValueError, match="official"):
            client.get("https://attacker.invalid/filing")
        with pytest.raises(ValueError, match="genuine"):
            sec.Client(store, "fake noreply@example.org")
    finally:
        store.close()


def test_sec_poll_dedup_and_historical_never_content_or_ai(tmp_path, monkeypatch):
    now = instant()
    store = Store(tmp_path, lambda: now)
    urls = []

    class Client:
        def clock(self):
            return now

        def get(self, url):
            urls.append(url)
            body = json.dumps(
                {"0": {"ticker": "AAPL", "cik_str": 320193}}
                if url == sec.TICKERS_URL
                else submission(now - 3600)
            ).encode()
            return body, now, "digest"

    monkeypatch.setattr(sec, "ISSUERS", ("AAPL",))
    try:
        result = sec.poll(store, Client())
        assert result["status"] == "COMPLETE"
        assert sec.poll(store, Client())["status"] == "DUPLICATE"
        assert len(urls) == 2 and len(store.rows("event")) == 1
        assert store.rows("content") == [] and not store.rows("event")[0]["prospective"]
    finally:
        store.close()


def test_codex_fact_parser_and_prompt_tools_boundary(pipeline):
    p, clock, _, _ = pipeline
    event, content = seed(p, clock[0])
    valid = assessment()
    assert ai.parse(valid, event, content)["recommendation"] == "LONG"
    bad = copy.deepcopy(valid)
    bad["assessments"][0]["verified_facts"][0]["source_url"] = "https://attacker.invalid"
    with pytest.raises(ValueError, match="original source"):
        ai.parse(bad, event, content)
    calls = []

    def runner(prompt, schema, candidates, **kwargs):
        calls.append((prompt, kwargs))
        return valid, {"tool_calls": 0}

    result, _ = ai.assess_event(
        event, content, {}, runner=runner, auth=lambda x: {"api_billed": False}
    )
    assert result["symbol"] == "AAPL"
    assert calls[0][1]["web_search"] == "disabled" and "UNTRUSTED DATA" in calls[0][0]
    assert "no numerical returns" in calls[0][0]


def test_subscription_auth_rejects_api_and_unavailable(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-a-key")
    with pytest.raises(ValueError, match="subscription-only"):
        ai.subscription_status()
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr(
        ai.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT", stderr=""),
    )
    assert not ai.subscription_status()["api_billed"]


def test_after_hours_holiday_and_early_close_schedule():
    friday = instant(date(2026, 11, 27), 14, 0)
    target = v6.eligible_slot(friday)
    local = datetime.fromtimestamp(target, v6.NY)
    assert local.date() == date(2026, 11, 30) and (local.hour, local.minute, local.second) == (
        9,
        46,
        45,
    )
    holiday = instant(date(2026, 12, 25), 10, 0)
    assert v6.session(v6.eligible_slot(holiday)) == date(2026, 12, 28)
    horizons = v6.horizon_targets(instant(date(2026, 11, 25)))
    # Thursday Thanksgiving is closed; Friday is the first next trading session, 13:00 close.
    assert datetime.fromtimestamp(horizons["1"] + 60, v6.NY).hour == 13
    assert datetime.fromtimestamp(horizons["1"], v6.NY).date() == date(2026, 11, 27)


def test_fast_independent_delayed_ai_shared_quote_and_restart(pipeline):
    p, clock, calls, _ = pipeline
    seed(p, clock[0])
    p.market_tick()
    fast = p.store.get("entry", "event-a:fast")
    assert fast["choices"] == {"fast": "LONG"} and len(calls) == 1
    assert not p.store.rows("analysis_started")
    p.assess_tick()
    completed = p.store.get("analysis", "event-a")["completed_at"]
    clock[0] = v6.eligible_slot(completed)
    p.market_tick()
    delayed = p.store.get("entry", "event-a:delayed")
    assert delayed["choices"] == {"delayed": "LONG", "quant_codex": "LONG"}
    assert delayed["target_at"] > fast["target_at"] and delayed["quote"]["observed_at"] == clock[0]
    count = len(calls)
    p.market_tick()
    p.assess_tick()
    assert len(calls) == count and len(p.store.rows("analysis_started")) == 1
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        p.store.db.execute("DELETE FROM event_records")


def test_codex_failure_keeps_baseline_not_favorable_cash(pipeline):
    p, clock, _, _ = pipeline
    seed(p, clock[0])
    p.market_tick()

    def failure(*a, **k):
        raise ValueError("auth fixture unavailable")

    p.assessor = failure
    p.assess_tick()
    clock[0] = v6.eligible_slot(p.store.get("analysis", "event-a")["completed_at"] + 0.01)
    p.market_tick()
    delayed = p.store.get("entry", "event-a:delayed")
    assert delayed["choices"]["delayed"] == "LONG"
    assert delayed["choices"]["quant_codex"] is None
    assert p.store.get("analysis", "event-a")["status"] == "UNAVAILABLE"


def test_missed_entry_is_not_backfilled(pipeline):
    p, clock, calls, _ = pipeline
    seed(p, clock[0])
    p.plan()
    target = p.store.get("plan", "event-a")["fast_target"]
    clock[0] = target + 61
    p.market_tick()
    assert p.store.get("entry", "event-a:fast")["status"] == "UNAVAILABLE"
    assert calls == []


def test_forward_primary_next_day_and_daily_reference_flags(pipeline):
    p, clock, _, prices = pipeline
    seed(p, clock[0])
    p.market_tick()
    p.assessor = lambda *a, **k: (assessment("WATCH")["assessments"][0], {"tool_calls": 0})
    p.assess_tick()
    clock[0] = v6.eligible_slot(clock[0] + 0.1)
    p.market_tick()
    entry = p.store.get("entry", "event-a:delayed")
    prices[0] = 103
    clock[0] = entry["horizons"]["1"] + 45
    p.market_tick()
    values = v6.outcomes(p.store)
    one = [r for r in values if r["horizon"] == 1]
    assert all(r["status"] == "COMPLETE" for r in one)
    assert next(r for r in one if r["arm"] == "quant_codex")["net"] == 0
    metric = compare(values)["1"]["primary_ai_minus_delayed"]
    assert (
        metric["opportunities"] == 1
        and metric["sessions"] == 1
        and metric["session_standard_error"] is None
    )
    assert metric["session_mean"] < 0
    # An intermediate session without a forward reference cannot be silently reconstructed later.
    clock[0] = entry["horizons"]["3"] + 45
    p.market_tick()
    assert all(
        r["status"] == "FLAGGED_CORPORATE_ACTION" for r in v6.outcomes(p.store) if r["horizon"] == 3
    )
    assert compare(v6.outcomes(p.store))["3"]["primary_ai_minus_delayed"]["opportunities"] == 0


def test_missing_exit_stays_unavailable_even_cash(pipeline):
    p, clock, _, _ = pipeline
    seed(p, clock[0])
    p.market_tick()
    p.assessor = lambda *a, **k: (assessment("WATCH")["assessments"][0], {})
    p.assess_tick()
    clock[0] = v6.eligible_slot(clock[0] + 0.01)
    p.market_tick()
    target = p.store.get("entry", "event-a:delayed")["horizons"]["1"]
    clock[0] = target + 61
    p.market_tick()
    rows = [r for r in v6.outcomes(p.store) if r["horizon"] == 1]
    assert rows and all(r["net"] is None and r["status"] == "UNAVAILABLE" for r in rows)


def test_daily_budget_cooldown_and_interrupted_claim(pipeline):
    p, clock, _, _ = pipeline
    event, content = seed(p, clock[0])
    for i in range(3):
        p.store.add(
            "analysis_started",
            f"old-{i}",
            {"event_id": f"old-{i}", "symbol": "OTHER", "started_at": clock[0] - 20 - i},
        )
        p.store.add(
            "analysis",
            f"old-{i}",
            {
                "event_id": f"old-{i}",
                "status": "UNAVAILABLE",
                "reason": "fixture",
                "completed_at": clock[0] - 10,
            },
        )
    p.assess_tick()
    assert p.store.get("analysis", event["id"])["reason"] == "DAILY_BUDGET"
    assert len(p.store.rows("analysis_started")) == 3


def test_after_hours_analysis_before_shared_next_session_entry(pipeline):
    p, clock, _, _ = pipeline
    clock[0] = instant(date(2026, 10, 19), 18, 0)
    seed(p, clock[0])
    p.assess_tick()
    assert p.store.get("analysis", "event-a")["status"] == "COMPLETED"
    clock[0] = p.store.get("plan", "event-a")["fast_target"]
    p.market_tick()
    fast = p.store.get("entry", "event-a:fast")
    delayed = p.store.get("entry", "event-a:delayed")
    assert fast["capture_id"] == delayed["capture_id"] and fast["quote"] == delayed["quote"]


def test_matched_primary_rejects_different_quote_cohort():
    base = {
        "event_id": "e",
        "horizon": 1,
        "status": "COMPLETE",
        "market_eligible": True,
        "entry_session": "2026-10-19",
        "entry_capture_id": "a",
        "entry_target_at": 1,
        "net": 0.01,
    }
    rows = [{**base, "arm": "delayed"}, {**base, "arm": "quant_codex", "entry_capture_id": "other"}]
    with pytest.raises(ValueError, match="cohorts differ"):
        compare(rows)


def test_report_missing_event_counts_and_no_fake_historical_opportunity(pipeline):
    p, clock, _, _ = pipeline
    historical = sec.metadata(submission(clock[0] - 86400), "AAPL", clock[0], p.store.activated_at)[
        0
    ]
    p.store.add("event", historical["id"], historical)
    result = report(p.store)
    assert result["prospective_events"] == 0 and result["codex_started"] == 0
    saved = json.loads((p.directory / "daily-status.json").read_text())
    assert saved["nonprospective_events"] == 1 and not saved["decisions"]


def test_v5_legacy_state_cannot_be_reused(tmp_path):
    from tradeagent.prospective.continuous import Pipeline

    old = Pipeline(tmp_path)
    old.store.close()
    before = (tmp_path / "experience.sqlite3").read_bytes()
    with pytest.raises(ValueError, match="independent"):
        Store(tmp_path)
    assert (tmp_path / "experience.sqlite3").read_bytes() == before


def test_html_untrusted_script_not_executed_and_exhibit_link_bounded():
    parser = sec.Text()
    parser.feed(
        '<html><head><script>erase_files()</script></head><body>Revenue grew. <a href="ex991.htm">99.1</a></body></html>'
    )
    assert "erase_files" not in parser.text and "Revenue grew." in parser.text
    assert parser.links == [("ex991.htm", "99.1")]


def test_reaction_does_not_gate_material_ai_eligibility(pipeline):
    p, clock, _, prices = pipeline
    event, _ = seed(p, clock[0])
    prices[0] = 100
    p.market_tick()
    fast = p.store.get("entry", "event-a:fast")
    assert fast["choices"]["fast"] == "CASH"
    p.assess_tick()
    assert p.store.get("analysis", event["id"])["status"] == "COMPLETED"


def test_cooldown_and_interrupted_claim_are_explicit_terminal_records(pipeline):
    p, clock, _, _ = pipeline
    event, _ = seed(p, clock[0])
    p.store.add(
        "analysis_started",
        "old",
        {"event_id": "old", "symbol": "AAPL", "started_at": clock[0] - 20},
    )
    p.assess_tick()
    assert p.store.get("analysis", "old")["reason"] == "INTERRUPTED_NO_RETRY"
    assert p.store.get("analysis", event["id"])["reason"] == "SYMBOL_COOLDOWN"
    assert len(p.store.rows("analysis_started")) == 1


def test_provider_adjustment_is_flagged_without_synthesizing_share_pnl(pipeline):
    p, clock, _, _ = pipeline
    seed(p, clock[0])
    p.market_tick()
    entry = p.store.get("entry", "event-a:fast")
    next_day = v6.session(entry["horizons"]["1"])
    p.store.add(
        "reference",
        next_day.isoformat() + ":AAPL",
        {
            "session": next_day.isoformat(),
            "symbol": "AAPL",
            "observed_at": entry["horizons"]["1"] - 60,
            "reference": {
                "previous_close": 100,
                "adjusted_previous_close": 50,
                "previous_close_date": entry["session"],
            },
        },
    )
    assert (
        v6.adjustment_status(p.store, entry, "AAPL", entry["horizons"]["1"])
        == "ADJUSTMENT_DETECTED"
    )


def test_forward_five_day_outcome_requires_all_intervening_sessions(pipeline):
    p, clock, _, prices = pipeline
    seed(p, clock[0])
    p.market_tick()
    p.assess_tick()
    clock[0] = v6.eligible_slot(p.store.get("analysis", "event-a")["completed_at"])
    p.market_tick()
    day = v6.session(clock[0])
    for _ in range(5):
        day += timedelta(days=1)
        while not session_bounds(day):
            day += timedelta(days=1)
        clock[0] = session_bounds(day)[0] + 16 * 60 + 45
        p.market_tick()
        clock[0] = session_bounds(day)[1] - 15
        prices[0] += 1
        p.market_tick()
    values = v6.outcomes(p.store)
    assert all(r["status"] == "COMPLETE" for r in values)
    assert compare(values)["5"]["primary_ai_minus_delayed"]["opportunities"] == 1


def test_installer_units_isolated_and_v5_guard_is_never_written():
    import runpy

    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/install_events.py"))
    definitions = module["units"](
        Path("/local/python"),
        Path("/local/v6"),
        Path("/local/sec.txt"),
        Path("/local/codex"),
        Path("/local/v5"),
    )
    assert len(definitions) == 8
    assert all(name.startswith("tradeagent-event-v6-") for name in definitions)
    assert "tradeagent-continuous-v5.service" in definitions["tradeagent-event-v6-market.service"]
    assert all(
        "Persistent=false" in text for name, text in definitions.items() if name.endswith(".timer")
    )
    assert all(
        "priority.conf" not in text and "capture_priority.py" not in text
        for text in definitions.values()
    )
    assert "06..21:02/15:00" in definitions["tradeagent-event-v6-poll.timer"]


def test_market_guard_treats_activating_and_unknown_as_busy(monkeypatch):
    monkeypatch.setattr(
        v6.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="inactive\nactivating\ninactive\n"),
    )
    assert not v6.worker_permitted("market", instant())
    monkeypatch.setattr(
        v6.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="inactive\ninactive\ninactive\n"),
    )
    assert v6.worker_permitted("market", instant())
    assert not v6.worker_permitted("market", instant(minute=5))
