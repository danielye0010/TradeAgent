"""Exercise the published no-account journey and its isolated inspection target."""

import json

from robinhood_agent.cli import main


def test_demo_inspection_uses_synthetic_equity_journal(tmp_path, monkeypatch, capsys):
    def no_native_connection(*args, **kwargs):
        raise AssertionError("offline onboarding must not connect to a broker")

    monkeypatch.setattr("robinhood_agent.cli.CodexBridge", no_native_connection)
    directory = tmp_path / "demo"
    assert main(["validate"]) == 0
    capsys.readouterr()
    assert main(["simulate", "--demo-dir", str(directory)]) == 0
    demo = json.loads(capsys.readouterr().out)
    assert demo["account_and_orders"] == "SYNTHETIC"
    assert demo["real_review_place_cancel_calls"] == 0
    assert demo["equity"]["broker_order_count"] == 1
    assert demo["option"]["broker_order_count"] == 1
    assert main(["inspect", "--config", str(directory / "inspect.example.json")]) == 0
    inspection = json.loads(capsys.readouterr().out)
    assert inspection["integrity"] == "ok"
    assert {run["status"] for run in inspection["runs"]} == {
        "simulated_filled",
        "duplicate_suppressed",
    }
    assert len(inspection["intents"]) == 1
