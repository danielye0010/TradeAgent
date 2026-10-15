"""First-run paths a friend hits before any broker access exists."""

import json

from tradeagent import friendly
from tradeagent.cli import main
from tradeagent.paths import default_config, default_helper
from tradeagent.robinhood_login import write_starter_config


def test_bare_command_prints_grouped_help(capsys):
    assert main([]) == 0
    text = capsys.readouterr().out
    assert "login" in text and "run-once --paper" in text and "live-check" in text


def test_live_check_without_config_points_to_login(capsys):
    assert main(["live-check"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "HALTED"
    assert "tradeagent login" in result["reason"]


def test_live_check_with_starter_config_but_no_session_points_to_login(capsys):
    assert write_starter_config()
    assert oct(default_config().stat().st_mode & 0o777) == "0o600"
    assert not default_helper().exists()
    assert main(["live-check"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["live_ready"] is False
    assert result["real_review_place_cancel_calls"] == 0
    assert "tradeagent login" in result["connectivity_blocker"]


def test_starter_config_is_never_overwritten():
    assert write_starter_config()
    default_config().write_text(default_config().read_text().replace('"5"', '"7"'))
    assert write_starter_config() is False
    assert 'dollar_amount = "7"' in default_config().read_text()


def test_run_once_live_without_config_never_reaches_broker(capsys):
    assert main(["run-once", "--live"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "LIVE BLOCKED"
    assert "tradeagent login" in result["reason"]


def test_summaries_are_plain_and_honest():
    assert "not evidence of profit" in friendly.demo({"status": "passed"})
    text = friendly.paper(
        {
            "status": "COMPLETED",
            "orders": [{"side": "buy", "cumulative_quantity": "2", "symbol": "IWM"}],
            "realized_pnl": "-0.04",
        }
    )
    assert "-$0.04" in text and "IWM" in text
    blocked = friendly.live_check({"live_ready": False, "connectivity_blocker": "x"})
    assert blocked.startswith("NOT READY") and "- x" in blocked
