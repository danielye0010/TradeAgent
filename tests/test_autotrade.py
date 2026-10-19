"""`tradeagent autotrade`: the daily LIVE mirror of the news-Codex research decision."""

import json
from datetime import datetime

import pytest
from test_fractional_live import fractional_runtime

from tradeagent import autotrade
from tradeagent.autotrade import NY
from tradeagent.cli import main
from tradeagent.paths import default_config
from tradeagent.robinhood_login import write_starter_config

DAY = datetime(2026, 10, 15).date()
OPEN = datetime(2026, 10, 15, 10, 30, tzinfo=NY).timestamp()
SATURDAY = datetime(2026, 10, 17, 10, 30, tzinfo=NY).timestamp()


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def _config(amount="1", symbols='["QQQ", "IWM", "AAPL"]'):
    assert write_starter_config()
    path = default_config()
    text = path.read_text().replace('dollar_amount = "5"', f'dollar_amount = "{amount}"')
    path.write_text(text.replace('symbols = ["QQQ", "IWM"]', f"symbols = {symbols}"))
    return path


def _decision(follow_dir, decided_at, *, symbol="AAPL", status="VALID", name="d-1", **extra):
    entry = decided_at + 300
    value = {
        "decision_id": name,
        "research_protocol": "daily-news-v2",
        "session_open": datetime(
            *datetime.fromtimestamp(decided_at, NY).timetuple()[:3], 9, 30, tzinfo=NY
        ).timestamp(),
        "decision_time": decided_at,
        "holding_seconds": 3600,
        "research_window": {"valid": True},
        "entry_after": entry,
        "entry_deadline": entry + 120,
        "outcome_entry_time": entry + 60 - entry % 60,
        "outcome_exit_time": entry + 3660 - entry % 60,
        "skill_version": "trade-opportunity-analyst-v4",
        "comparisons": {
            "codex_news": {
                "symbol": symbol if status == "VALID" else None,
                "decision": "SHADOW_LONG" if symbol and status == "VALID" else "NO_TRADE",
                "status": status,
                "plan": None,
            },
            "quant_only": {"symbol": "QQQ", "decision": "SHADOW_LONG", "status": "VALID"},
        },
        **extra,
    }
    (follow_dir / "decisions").mkdir(parents=True, exist_ok=True)
    (follow_dir / "decisions" / f"{name}.json").write_text(json.dumps(value))
    return value


@pytest.fixture
def engine(monkeypatch):
    seen = []

    def execute(settings, plan, prediction):
        seen.append((plan, prediction))
        return engine.report

    engine.report = {
        "status": "COMPLETED",
        "orders": [],
        "flat_bot_position": True,
        "cash_reconciled": True,
        "reconciliation_status": "RECONCILED",
        "submission_status": "BROKER_CONFIRMED",
    }
    monkeypatch.setattr(autotrade, "finish_previous", lambda settings: seen.append("recover"))
    monkeypatch.setattr(autotrade, "archive_previous", lambda settings: seen.append("archive"))
    monkeypatch.setattr(autotrade, "execute", execute)
    engine.seen = seen
    return engine


def _record(state_dir):
    return json.loads((state_dir / f"{DAY.isoformat()}.json").read_text())


def test_units_run_at_the_research_window_without_catch_up(tmp_path):
    units = autotrade.units(
        "/venv/bin/python", tmp_path / "c.toml", tmp_path / "v2", tmp_path / "s"
    )
    service, timer = units["tradeagent-autotrade.service"], units["tradeagent-autotrade.timer"]
    assert f"--follow-dir {tmp_path / 'v2'}" in service
    assert "UMask=0077" in service and "TimeoutStartSec=3h" in service
    assert "OnCalendar=Mon..Fri *-*-* 10:30:00 America/New_York" in timer
    assert "Persistent" not in timer


def test_install_reports_the_followed_research_and_uncovered_symbols(tmp_path):
    (tmp_path / "v2").mkdir()
    summary = autotrade.install(
        _config(), tmp_path / "v2", tmp_path / "s", enable=True, unit_dir=tmp_path, systemctl=False
    )
    assert summary["amount_per_trade"] == "$1"
    assert summary["account_value_needed"] == "$10.00"
    assert summary["follows"].startswith("daily-news-v2 codex_news")
    assert "NVDA" in summary["research_symbols_not_allowed"]
    assert "AAPL" not in summary["research_symbols_not_allowed"]
    assert oct((tmp_path / "tradeagent-autotrade.service").stat().st_mode & 0o777) == "0o600"


def test_install_finds_the_installed_news_research_directory(tmp_path):
    (tmp_path / "news").mkdir()
    (tmp_path / "tradeagent-daily-news.service").write_text(
        "[Service]\nExecStart=/py -m tradeagent.prospective.daily "
        f"--state-dir {tmp_path / 'news'} --protocol daily-news-v2\n"
    )
    summary = autotrade.install(
        _config(), None, tmp_path / "s", enable=False, unit_dir=tmp_path, systemctl=False
    )
    assert str(tmp_path / "news") in summary["follows"]


def test_install_without_a_news_research_directory_explains_the_flag(tmp_path):
    with pytest.raises(ValueError, match="--follow-dir"):
        autotrade.install(
            _config(), None, tmp_path / "s", enable=False, unit_dir=tmp_path, systemctl=False
        )


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('dollar_amount = "1"', 'dollar_amount = "6"', "dollar_amount"),
        ("hold_seconds = 3600", "hold_seconds = 30", "hold_seconds must be 3600"),
    ],
)
def test_install_rejects_owner_settings_that_cannot_mirror(tmp_path, old, new, message):
    config = _config()
    config.write_text(config.read_text().replace(old, new))
    (tmp_path / "v2").mkdir()
    with pytest.raises(ValueError, match=message):
        autotrade.install(
            config,
            tmp_path / "v2",
            tmp_path / "s",
            enable=False,
            unit_dir=tmp_path,
            systemctl=False,
        )


def test_dashboard_discovers_the_autotrade_timer(tmp_path):
    from tradeagent.dashboard import jobs

    units = autotrade.units("/py", tmp_path / "c.toml", tmp_path / "v2", tmp_path / "s")
    for name, text in units.items():
        (tmp_path / name).write_text(text)
    [job] = jobs.discover(tmp_path, systemctl=False)
    assert job["kind"] == "autotrade" and job["protocol"] == "live-mirror"


def test_closed_market_does_nothing(engine, tmp_path):
    assert autotrade.run(_config(), tmp_path / "v2", tmp_path / "s", clock=Clock(SATURDAY)) == 0
    assert engine.seen == []


def test_kill_switch_stops_before_reading_the_research(engine, tmp_path):
    from tradeagent.execution_policy import load_live_config

    config = _config()
    live = load_live_config(config).directory
    live.mkdir(parents=True, exist_ok=True)
    (live / "KILL").write_text("")
    _decision(tmp_path / "v2", OPEN + 60)
    assert autotrade.run(config, tmp_path / "v2", tmp_path / "s", clock=Clock(OPEN)) == 0
    assert engine.seen == ["recover"]  # exit-only recovery still runs under KILL


def test_waits_for_the_frozen_decision_then_buys_at_its_entry_minute(engine, tmp_path):
    clock = Clock(OPEN)
    decided = OPEN + 200
    late = {}

    def sleep(seconds):
        clock.sleep(seconds)
        if clock() >= decided and not late:
            late.update(_decision(tmp_path / "v2", decided))

    clock_sleep = sleep
    assert (
        autotrade.run(_config(), tmp_path / "v2", tmp_path / "s", clock=clock, sleep=clock_sleep)
        == 0
    )
    assert engine.seen[:2] == ["recover", "archive"]
    plan, prediction = engine.seen[2]
    assert plan.instrument == prediction.symbol == "AAPL" and plan.kind == "UNDERLYING"
    assert prediction.direction == 1 and prediction.horizon == 3600
    assert prediction.decision_time >= late["outcome_entry_time"]
    assert prediction.features.plain()["decision_id"] == "d-1"
    record = _record(tmp_path / "s")
    assert record["symbol"] == "AAPL" and record["status"] == "CLOSED"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"symbol": None}, "chose cash"),
        ({"status": "UNAVAILABLE"}, "assessment unavailable"),
        ({"symbol": "NVDA"}, "not in the owner [live] symbols"),
        ({"research_window": {"valid": False}}, "no realizable"),
    ],
)
def test_no_trade_outcomes_never_reach_the_engine(engine, tmp_path, kwargs, reason):
    _decision(tmp_path / "v2", OPEN + 60, **kwargs)
    assert autotrade.run(_config(), tmp_path / "v2", tmp_path / "s", clock=Clock(OPEN + 90)) == 0
    assert engine.seen == ["recover"]
    record = _record(tmp_path / "s")
    assert record["status"] == "NO_TRADE" and reason in record["reason"]


def test_no_decision_by_the_end_of_the_window_is_a_quiet_no_trade(engine, tmp_path):
    clock = Clock(OPEN)
    assert (
        autotrade.run(_config(), tmp_path / "v2", tmp_path / "s", clock=clock, sleep=clock.sleep)
        == 0
    )
    assert engine.seen == ["recover"] and clock() >= OPEN + autotrade.DECISION_WAIT_SECONDS
    assert _record(tmp_path / "s")["reason"] == "no daily-news-v2 decision today"


def test_other_protocols_and_days_are_ignored(engine, tmp_path):
    _decision(tmp_path / "v2", OPEN - 86400 + 60, name="yesterday")
    _decision(tmp_path / "v2", OPEN + 60, name="v1", research_protocol="daily-four-arm-v1")
    clock = Clock(OPEN)
    assert (
        autotrade.run(_config(), tmp_path / "v2", tmp_path / "s", clock=clock, sleep=clock.sleep)
        == 0
    )
    assert engine.seen == ["recover"]


def test_unresolved_previous_run_blocks_a_new_entry(engine, monkeypatch, tmp_path):
    def still_open(settings):
        raise RuntimeError("previous run still open")

    monkeypatch.setattr(autotrade, "finish_previous", still_open)
    _decision(tmp_path / "v2", OPEN + 60)
    assert autotrade.run(_config(), tmp_path / "v2", tmp_path / "s", clock=Clock(OPEN + 90)) == 2
    assert engine.seen == [] and _record(tmp_path / "s")["status"] == "STOPPED"


def test_second_run_the_same_day_cannot_enter_again(engine, tmp_path):
    config = _config()
    _decision(tmp_path / "v2", OPEN + 60)
    clock = Clock(OPEN + 90)
    assert (
        autotrade.run(config, tmp_path / "v2", tmp_path / "s", clock=clock, sleep=clock.sleep) == 0
    )
    clock = Clock(OPEN + 90)
    assert (
        autotrade.run(config, tmp_path / "v2", tmp_path / "s", clock=clock, sleep=clock.sleep) == 2
    )
    assert sum(1 for s in engine.seen if isinstance(s, tuple)) == 1
    assert "already attempted" in _record(tmp_path / "s")["reason"]


def test_a_missed_entry_window_never_trades_late(engine, tmp_path):
    decision = _decision(tmp_path / "v2", OPEN + 60)
    clock = Clock(decision["entry_deadline"] + 1)
    assert (
        autotrade.run(_config(), tmp_path / "v2", tmp_path / "s", clock=clock, sleep=clock.sleep)
        == 0
    )
    assert not any(isinstance(s, tuple) for s in engine.seen)
    assert _record(tmp_path / "s")["status"] == "EXPIRED"


def test_two_same_day_decisions_are_refused(tmp_path):
    _decision(tmp_path / "v2", OPEN + 60, name="a")
    _decision(tmp_path / "v2", OPEN + 61, name="b")
    with pytest.raises(RuntimeError, match="refusing to pick"):
        autotrade.find_decision(tmp_path / "v2", DAY)


def test_mirror_buys_and_exits_through_the_real_engine(tmp_path, monkeypatch):
    """Offline mock broker: the frozen pick is bought for $1 and sold one hour later."""
    _, sim, _, target, config = fractional_runtime(tmp_path, monkeypatch, amount="1")
    config.write_text(config.read_text().replace("hold_seconds = 0", "hold_seconds = 3600"))
    now = sim.clock()
    decision = _decision(tmp_path / "v2", now - 240)
    assert (
        autotrade.run(
            config, tmp_path / "v2", tmp_path / "s", clock=sim.clock, sleep=sim.clock.advance
        )
        == 0
    )
    record = json.loads(next((tmp_path / "s").glob("*.json")).read_text())
    assert record["decision_id"] == decision["decision_id"] and record["symbol"] == "AAPL"
    assert record["status"] == "CLOSED"
    sides = [o["side"] for o in record["orders"]]
    assert sides == ["buy", "sell"]
    report = json.loads((target / "report.json").read_text())
    assert report["exit_due"] >= decision["outcome_entry_time"] + 3600
    sim.close()


def test_cli_dispatches_autotrade(capsys):
    assert main(["autotrade", "install", "--config", "/nonexistent/tradeagent.toml"]) == 2
    assert "STOPPED" in capsys.readouterr().out


def test_open_exposure_is_recovered_even_on_a_cash_day(engine, tmp_path):
    _decision(tmp_path / "v2", OPEN + 60, symbol=None)
    assert autotrade.run(_config(), tmp_path / "v2", tmp_path / "s", clock=Clock(OPEN + 90)) == 0
    assert engine.seen == ["recover"]
    assert _record(tmp_path / "s")["status"] == "NO_TRADE"


def test_the_daily_claim_directory_is_persisted(engine, tmp_path, monkeypatch):
    synced = []
    real = autotrade.os.fsync
    monkeypatch.setattr(autotrade.os, "fsync", lambda fd: (synced.append(fd), real(fd)))
    _decision(tmp_path / "v2", OPEN + 60)
    clock = Clock(OPEN + 90)
    autotrade.run(_config(), tmp_path / "v2", tmp_path / "s", clock=clock, sleep=clock.sleep)
    assert len(synced) == 3  # claim file, its new directory, and that directory's parent


def _claude_decision(follow_dir, decided_at, symbol="IWM"):
    """A daily-news-claude-v1 record: the same window, Claude's own news pick."""
    value = _decision(follow_dir, decided_at, symbol="AAPL", name="c-1")
    value["research_protocol"] = "daily-news-claude-v1"
    value["comparisons"]["claude_news"] = {
        "symbol": symbol,
        "decision": "SHADOW_LONG",
        "status": "VALID",
        "plan": None,
    }
    (follow_dir / "decisions" / "c-1.json").write_text(json.dumps(value))
    return value


def test_claude_can_be_chosen_and_mirrors_its_own_pick(engine, tmp_path):
    clock = Clock(OPEN)
    _decision(tmp_path / "v2", OPEN + 100)  # Codex picked AAPL in v2...
    _claude_decision(tmp_path / "claude", OPEN + 100)  # ...Claude picked IWM.
    code = autotrade.run(
        _config(),
        tmp_path / "claude",
        tmp_path / "s",
        model="claude",
        clock=clock,
        sleep=clock.sleep,
    )
    assert code == 0
    plan, prediction = engine.seen[2]
    assert plan.instrument == "IWM"
    assert prediction.strategy_id == "mirror:daily-news-claude-v1:claude_news"
    record = _record(tmp_path / "s")
    assert record["model"] == "claude" and record["followed"] == "daily-news-claude-v1/claude_news"


def test_claude_never_reads_a_codex_record(engine, tmp_path):
    clock = Clock(OPEN)
    _decision(tmp_path / "claude", OPEN + 100)  # a v2 record in the wrong directory
    autotrade.run(
        _config(),
        tmp_path / "claude",
        tmp_path / "s",
        model="claude",
        clock=clock,
        sleep=clock.sleep,
    )
    assert _record(tmp_path / "s")["status"] == "NO_TRADE"
    assert len(engine.seen) == 1  # recovery only


def test_the_default_install_is_unchanged_and_claude_is_opt_in(tmp_path):
    codex = autotrade.units("/py", tmp_path / "c.toml", tmp_path / "v2", tmp_path / "s")
    assert "--model" not in codex["tradeagent-autotrade.service"]
    claude = autotrade.units("/py", tmp_path / "c.toml", tmp_path / "cl", tmp_path / "s", "claude")
    assert claude["tradeagent-autotrade.service"].rstrip().count("--model claude") == 1
    assert "daily-news-claude-v1 claude_news" in claude["tradeagent-autotrade.timer"]
    (tmp_path / "cl").mkdir()
    summary = autotrade.install(
        _config(),
        tmp_path / "cl",
        tmp_path / "s",
        enable=False,
        model="claude",
        unit_dir=tmp_path,
        systemctl=False,
    )
    assert summary["model"] == "Claude"
    assert summary["follows"].startswith("daily-news-claude-v1 claude_news")


def test_claude_install_without_the_comparison_points_to_its_installer(tmp_path):
    with pytest.raises(ValueError, match="tradeagent claude install"):
        autotrade.install(
            _config(),
            None,
            tmp_path / "s",
            enable=False,
            model="claude",
            unit_dir=tmp_path,
            systemctl=False,
        )
