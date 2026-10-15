"""The offline setup checklist guides a new user without touching the broker."""

import json

from tradeagent import doctor
from tradeagent.cli import main
from tradeagent.paths import default_config
from tradeagent.robinhood_login import install_helper, write_starter_config


def by_name(results):
    return {row["check"]: row for row in results}


def test_fresh_machine_points_to_login(capsys):
    assert main(["doctor"]) == 1
    text = capsys.readouterr().out
    assert "✗ Robinhood sign-in" in text and "tradeagent login" in text
    assert "Fix the ✗ items" in text


def test_signed_in_machine_is_ready(capsys, tmp_path):
    install_helper()
    store = doctor.Path(default_config()).with_name("robinhood-oauth.json")
    store.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    store.write_text("{}")
    store.chmod(0o600)
    assert write_starter_config()
    results = by_name(doctor.run())
    assert results["Robinhood sign-in"]["status"] == "ok"
    assert results["Live settings"]["status"] == "ok"
    assert "$5 per entry" in results["Live settings"]["detail"]
    assert main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["check"] == "System"


def test_loose_permissions_and_bad_config_are_explained():
    install_helper()
    store = doctor.Path(default_config()).with_name("robinhood-oauth.json")
    store.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    store.write_text("{}")
    store.chmod(0o644)
    assert "chmod 600" in doctor.check_login()[2]
    default_config().write_text("[live]\nenabled = true\n")
    default_config().chmod(0o600)
    status, detail, fix = doctor.check_config()
    assert status == "fail" and fix


def test_windows_mount_is_rejected():
    status, detail, _ = doctor.check_storage("/mnt/c/Users/me/state")
    assert status == "fail" and "Windows" in detail
