"""`tradeagent autotrade`: the unattended daily experimental LIVE job and its timer."""

import json
from datetime import datetime

import pytest

from tradeagent import autotrade
from tradeagent.autotrade import NY
from tradeagent.cli import main
from tradeagent.paths import default_config
from tradeagent.robinhood_login import write_starter_config

OPEN = datetime(2026, 10, 15, 10, 15, tzinfo=NY).timestamp()
SATURDAY = datetime(2026, 10, 17, 10, 15, tzinfo=NY).timestamp()


def _config(amount="1"):
    assert write_starter_config()
    path = default_config()
    path.write_text(path.read_text().replace('dollar_amount = "5"', f'dollar_amount = "{amount}"'))
    return path


@pytest.fixture
def calls(monkeypatch, tmp_path):
    seen = []

    def opportunity(state_dir, *args):
        seen.append(args[0] if args[0] != "execute" else args)
        calls.args[args[0]] = args
        if args[0] == "decide":
            plan = {"decision_id": "d-1", "experimental": {"plan": calls.plan}}
            (state_dir / "latest-plan.json").write_text(json.dumps(plan))
        return calls.codes.get(args[0], 0)

    calls.plan, calls.codes, calls.args = [{"symbol": "QQQ"}], {}, {}
    monkeypatch.setattr(autotrade, "_opportunity", opportunity)
    monkeypatch.setattr(autotrade, "finish_previous", lambda settings: None)
    calls.seen = seen
    return calls


def test_units_run_the_job_on_weekday_mornings_without_catch_up(tmp_path):
    units = autotrade.units("/venv/bin/python", tmp_path / "c.toml", tmp_path / "s", "10:15")
    service, timer = units["tradeagent-autotrade.service"], units["tradeagent-autotrade.timer"]
    assert "-m tradeagent.autotrade run --config" in service
    assert "UMask=0077" in service and "TimeoutStartSec=3h" in service
    assert "OnCalendar=Mon..Fri *-*-* 10:15:00 America/New_York" in timer
    assert "Persistent" not in timer


def test_install_writes_units_and_reports_the_account_size_needed(tmp_path):
    config = _config("1")
    summary = autotrade.install(
        config, tmp_path / "state", "10:15", enable=True, unit_dir=tmp_path, systemctl=False
    )
    assert summary["amount_per_trade"] == "$1"
    assert summary["account_value_needed"] == "$10.00"
    assert summary["enabled"] is False  # systemctl skipped in tests
    assert (tmp_path / "tradeagent-autotrade.timer").is_file()
    assert oct((tmp_path / "tradeagent-autotrade.service").stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("at", ["9:00", "09:30", "15:00", "noon", "10:75"])
def test_install_rejects_times_outside_the_safe_window(tmp_path, at):
    with pytest.raises(ValueError, match="--at"):
        autotrade.install(
            _config(), tmp_path / "s", at, enable=False, unit_dir=tmp_path, systemctl=False
        )


@pytest.mark.parametrize("amount", ["0.5", "6"])
def test_install_rejects_amounts_outside_one_to_five_dollars(tmp_path, amount):
    with pytest.raises(ValueError, match=r"dollar_amount|at least \$1"):
        autotrade.install(
            _config(amount),
            tmp_path / "s",
            "10:15",
            enable=False,
            unit_dir=tmp_path,
            systemctl=False,
        )


def test_dashboard_discovers_the_autotrade_timer(tmp_path):
    from tradeagent.dashboard import jobs

    for name, text in autotrade.units("/py", tmp_path / "c.toml", tmp_path / "s", "10:15").items():
        (tmp_path / name).write_text(text)
    [job] = jobs.discover(tmp_path, systemctl=False)
    assert job["kind"] == "autotrade" and job["protocol"] == "experimental-live"
    assert job["state_dir"] == str(tmp_path / "s")


def test_closed_market_does_nothing(calls, tmp_path):
    assert autotrade.run(_config(), tmp_path / "s", now=SATURDAY) == 0
    assert calls.seen == []


def test_kill_switch_stops_before_any_scan(calls, tmp_path):
    from tradeagent.execution_policy import load_live_config

    config = _config()
    live = load_live_config(config).directory
    live.mkdir(parents=True, exist_ok=True)
    (live / "KILL").write_text("")
    assert autotrade.run(config, tmp_path / "s", now=OPEN) == 0
    assert calls.seen == []


def test_unresolved_previous_run_blocks_a_new_entry(calls, monkeypatch, tmp_path):
    def still_open(settings):
        raise RuntimeError("previous run still open")

    monkeypatch.setattr(autotrade, "finish_previous", still_open)
    assert autotrade.run(_config(), tmp_path / "s", now=OPEN) == 2
    assert calls.seen == []


def test_scan_failure_never_executes(calls, tmp_path):
    calls.codes = {"scan": 2}
    assert autotrade.run(_config(), tmp_path / "s", now=OPEN) == 2
    assert calls.seen == ["scan"]


def test_no_eligible_signal_is_a_quiet_no_trade(calls, tmp_path, capsys):
    calls.plan = []
    assert autotrade.run(_config(), tmp_path / "s", now=OPEN) == 0
    assert calls.seen == ["scan", "decide"]
    assert '"NO_TRADE"' in capsys.readouterr().out


def test_eligible_plan_goes_to_the_experimental_live_engine(calls, tmp_path):
    calls.codes = {"execute": 0}
    assert autotrade.run(_config(), tmp_path / "s", now=OPEN) == 0
    assert calls.seen[:2] == ["scan", "decide"]
    execute = calls.seen[2]
    assert execute[:3] == ("execute", "--decision-id", "d-1")
    assert "--live" in execute and execute[execute.index("--mode") + 1] == "experimental"


def test_cli_dispatches_autotrade(capsys):
    assert main(["autotrade", "install", "--config", "/nonexistent/tradeagent.toml"]) == 2
    assert "STOPPED" in capsys.readouterr().out


def test_market_captures_use_the_configured_helper_and_hold(calls, tmp_path):
    from tradeagent.execution_policy import load_live_config

    config = _config()
    config.write_text(
        config.read_text()
        .replace("hold_seconds = 3600", "hold_seconds = 900")
        .replace('# oauth_helper = "', 'oauth_helper = "')
    )
    settings = load_live_config(config)
    calls.plan = []
    assert autotrade.run(config, tmp_path / "s", now=OPEN) == 0
    for command in ("scan", "decide"):
        args = calls.args[command]
        assert args[args.index("--oauth-helper") + 1] == str(settings.oauth_helper)
    decide = calls.args["decide"]
    assert decide[decide.index("--hold-seconds") + 1] == "900"


def test_install_rejects_a_hold_the_frozen_horizons_cannot_use(tmp_path):
    config = _config()
    config.write_text(config.read_text().replace("hold_seconds = 3600", "hold_seconds = 30"))
    with pytest.raises(ValueError, match="hold_seconds must be one of"):
        autotrade.install(
            config, tmp_path / "s", "10:15", enable=False, unit_dir=tmp_path, systemctl=False
        )
