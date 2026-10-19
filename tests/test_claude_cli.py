"""`tradeagent claude`: doctor, install and uninstall of the Claude comparison timer."""

import json

import pytest
from test_claude_assessor import fake_claude

from tradeagent import claude_cli
from tradeagent.cli import main
from tradeagent.dashboard import jobs


def v2_dir(tmp_path):
    path = tmp_path / "data" / "daily-news-v2"
    path.mkdir(parents=True)
    (path / "daily-deployment.json").write_text(
        json.dumps({"config": {"protocol": "daily-news-v2", "evidence_kind": "prospective"}})
    )
    return path


def test_install_writes_a_timer_next_to_v2_and_the_dashboard_finds_it(tmp_path):
    follow = v2_dir(tmp_path)
    units = tmp_path / "units"
    summary = claude_cli.install(follow, None, enable=False, unit_dir=units, systemctl=False)
    assert summary["state_dir"] == str(tmp_path / "data" / "daily-news-claude-v1")
    assert "autotrade install --model claude" in summary["orders"]
    service = (units / "tradeagent-claude-compare.service").read_text()
    timer = (units / "tradeagent-claude-compare.timer").read_text()
    assert (
        f"--follow-dir {follow}" in service
        and "-m tradeagent.prospective.claude_compare" in service
    )
    assert "%h/.local/bin" in service  # where the Claude Code CLI installs
    assert "OnCalendar=Mon..Fri *-*-* 10:30:00 America/New_York" in timer
    assert "Persistent=true" in timer
    (job,) = jobs.discover(units, systemctl=False)
    assert job["kind"] == "daily" and job["protocol"] == "daily-news-claude-v1"
    assert job["state_dir"] == summary["state_dir"]


def test_install_finds_the_installed_v2_timer(tmp_path):
    follow = v2_dir(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    (units / "tradeagent-daily-news.service").write_text(
        "[Service]\nExecStart=/py -m tradeagent.prospective.daily "
        f"--state-dir {follow} --protocol daily-news-v2\n"
    )
    summary = claude_cli.install(None, None, enable=False, unit_dir=units, systemctl=False)
    assert summary["follows"] == str(follow)


def test_install_refuses_without_v2_or_into_the_v2_directory(tmp_path):
    with pytest.raises(ValueError, match="daily-news-v2"):
        claude_cli.install(None, None, enable=False, unit_dir=tmp_path, systemctl=False)
    follow = v2_dir(tmp_path)
    with pytest.raises(ValueError, match="own --state-dir"):
        claude_cli.install(follow, follow, enable=False, unit_dir=tmp_path, systemctl=False)


def test_uninstall_keeps_records(tmp_path):
    follow = v2_dir(tmp_path)
    claude_cli.install(follow, None, enable=False, unit_dir=tmp_path, systemctl=False)
    assert claude_cli.uninstall(tmp_path, systemctl=False)["records"] == "kept"
    assert not (tmp_path / "tradeagent-claude-compare.timer").exists()


def test_doctor_reports_every_check(tmp_path):
    executable, _ = fake_claude(tmp_path, [])
    result = claude_cli.doctor(v2_dir(tmp_path), executable=str(executable))
    assert result["ready"] is True
    assert [c["check"] for c in result["checks"]] == [
        "claude_cli",
        "subscription_login",
        "daily_news_v2",
    ]
    (tmp_path / "x").mkdir()
    api_key, _ = fake_claude(tmp_path / "x", [], login={"loggedIn": True, "authMethod": "api_key"})
    result = claude_cli.doctor(None, executable=str(api_key), unit_dir=tmp_path / "none")
    assert result["ready"] is False
    failed = {c["check"] for c in result["checks"] if not c["ok"]}
    assert failed == {"subscription_login", "daily_news_v2"}


def test_cli_dispatches_claude(capsys):
    with pytest.raises(SystemExit):
        main(["claude", "--help"])
    assert "subscription" in capsys.readouterr().out
