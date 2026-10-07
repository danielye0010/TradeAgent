"""Credential boundaries use artificial sentinels only; never operator credentials."""

import getpass
import hashlib
import json
import os
import sys
import warnings
from contextlib import contextmanager
from urllib.error import HTTPError, URLError

import pytest

from tradeagent.cli import main
from tradeagent.research import history_data
from tradeagent.research.history_data import alpaca_credentials, load_history

KEY = "artificial-test-key-do-not-use"
SECRET = "artificial-test-secret-do-not-use"


@pytest.fixture(autouse=True)
def no_operator_credentials(monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)


def terminal(monkeypatch, enabled=True):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: enabled)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: enabled)


def forbid_input(*args, **kwargs):
    raise AssertionError("input must not be requested")


def assert_absent(text):
    for value in (KEY, SECRET):
        assert value not in text
        assert hashlib.sha256(value.encode()).hexdigest() not in text


def test_complete_environment_takes_precedence_even_without_terminal(monkeypatch, capsys):
    terminal(monkeypatch, False)
    monkeypatch.setenv("APCA_API_KEY_ID", KEY)
    monkeypatch.setenv("APCA_API_SECRET_KEY", SECRET)
    monkeypatch.setattr(getpass, "getpass", forbid_input)
    assert alpaca_credentials() == (KEY, SECRET)
    assert_absent(str(capsys.readouterr()))


@pytest.mark.parametrize("existing", ["key", "secret", "neither"])
def test_hidden_input_only_for_missing_values_and_no_environment_mutation(
    monkeypatch, capsys, existing
):
    terminal(monkeypatch)
    if existing == "key":
        monkeypatch.setenv("APCA_API_KEY_ID", KEY)
    if existing == "secret":
        monkeypatch.setenv("APCA_API_SECRET_KEY", SECRET)
    before = dict(os.environ)
    prompts = []

    def hidden(prompt):
        prompts.append(prompt)
        return KEY if "Key ID" in prompt else SECRET

    monkeypatch.setattr(getpass, "getpass", hidden)
    assert alpaca_credentials() == (KEY, SECRET)
    assert len(prompts) == (2 if existing == "neither" else 1)
    assert all("(hidden)" in p for p in prompts)
    assert dict(os.environ) == before
    assert_absent(str(capsys.readouterr()))


@pytest.mark.parametrize("existing", ["key", "secret", "neither"])
def test_noninteractive_missing_values_never_wait(monkeypatch, existing):
    terminal(monkeypatch, False)
    if existing == "key":
        monkeypatch.setenv("APCA_API_KEY_ID", KEY)
    if existing == "secret":
        monkeypatch.setenv("APCA_API_SECRET_KEY", SECRET)
    monkeypatch.setattr(getpass, "getpass", forbid_input)
    with pytest.raises(ValueError, match="interactive terminal") as error:
        alpaca_credentials()
    assert_absent(str(error.value))


@pytest.mark.parametrize("failure", [EOFError, KeyboardInterrupt, OSError])
def test_canceled_or_failed_prompt_redacts_error_values(monkeypatch, capsys, failure):
    terminal(monkeypatch)
    monkeypatch.setenv("APCA_API_KEY_ID", KEY)

    def broken(prompt):
        raise failure(SECRET)

    monkeypatch.setattr(getpass, "getpass", broken)
    with pytest.raises(ValueError, match="unavailable or canceled") as error:
        alpaca_credentials()
    assert_absent(str(error.value) + str(capsys.readouterr()))


def test_getpass_echo_fallback_is_blocked_before_input(monkeypatch, capsys):
    terminal(monkeypatch)
    fallback = []

    def no_echo_control(prompt):
        warnings.warn("echo cannot be controlled", getpass.GetPassWarning, stacklevel=2)
        fallback.append("unsafe fallback")
        return SECRET

    monkeypatch.setattr(getpass, "getpass", no_echo_control)
    with pytest.raises(ValueError, match="echo control") as error:
        alpaca_credentials()
    assert not fallback
    assert_absent(str(error.value) + str(capsys.readouterr()))


@pytest.mark.parametrize("blank", ["key", "secret"])
def test_empty_input_fails_without_credentials_in_error(monkeypatch, blank):
    terminal(monkeypatch)
    monkeypatch.setattr(
        getpass, "getpass", lambda prompt: "" if (("Key ID" in prompt) == (blank == "key")) else KEY
    )
    with pytest.raises(ValueError, match="missing Alpaca") as error:
        alpaca_credentials()
    assert_absent(str(error.value))


def test_prompted_request_headers_only_and_cache_excludes_credentials(
    tmp_path, monkeypatch, capsys
):
    terminal(monkeypatch)
    monkeypatch.setattr(getpass, "getpass", lambda p: KEY if "Key ID" in p else SECRET)
    seen = []

    @contextmanager
    def data_only(request, timeout):
        assert request.full_url.startswith("https://data.alpaca.markets/v2/stocks/bars?")
        assert KEY not in request.full_url and SECRET not in request.full_url
        headers = {k.lower(): v for k, v in request.header_items()}
        assert headers["apca-api-key-id"] == KEY
        assert headers["apca-api-secret-key"] == SECRET
        seen.append(request.full_url)
        import io

        yield io.StringIO(
            json.dumps(
                {
                    "bars": {
                        "QQQ": [
                            {
                                "t": "2026-01-05T14:30:00Z",
                                "o": 100,
                                "h": 101,
                                "l": 99,
                                "c": 100.5,
                                "v": 10000,
                            }
                        ]
                    },
                    "next_page_token": None,
                }
            )
        )

    monkeypatch.setattr(history_data, "urlopen", data_only)
    history, _, cache = load_history(tmp_path, ["QQQ"], 1767623400, 1767623460)
    assert history.metadata["source"] == "alpaca-sip-raw" and len(seen) == 1
    assert not os.getenv("APCA_API_KEY_ID") and not os.getenv("APCA_API_SECRET_KEY")
    assert_absent(cache.read_text() + str(capsys.readouterr()))
    monkeypatch.setattr(getpass, "getpass", forbid_input)
    monkeypatch.setattr(history_data, "urlopen", forbid_input)
    load_history(tmp_path, ["QQQ"], 1767623400, 1767623460)  # cached: no prompt/network


@pytest.mark.parametrize("kind", ["http", "network"])
def test_network_errors_never_echo_response_headers_or_body(monkeypatch, capsys, kind):
    monkeypatch.setenv("APCA_API_KEY_ID", KEY)
    monkeypatch.setenv("APCA_API_SECRET_KEY", SECRET)

    def failure(request, timeout):
        if kind == "http":
            raise HTTPError(request.full_url, 403, SECRET, {"example": KEY}, None)
        raise URLError(SECRET + KEY)

    monkeypatch.setattr(history_data, "urlopen", failure)
    with pytest.raises(ValueError) as error:
        history_data.AlpacaHistory().fetch(["QQQ"], 1767623400, 1767623460)
    assert_absent(str(error.value) + str(capsys.readouterr()))


def test_noninteractive_cli_actionable_summary_no_database(tmp_path, monkeypatch, capsys):
    terminal(monkeypatch, False)
    monkeypatch.setattr(getpass, "getpass", forbid_input)
    assert main(["replay-history", "--as-of", "2026-10-07", "--output", str(tmp_path)]) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["status"] == "REVIEW"
    assert "interactive terminal" in result["reasons"][0]
    assert not (tmp_path / "experience.sqlite3").exists()
    for file in tmp_path.rglob("*"):
        if file.is_file():
            assert_absent(file.read_text())
    assert_absent(str(output))


def test_cli_prompted_run_neither_outputs_nor_persists_credentials(tmp_path, monkeypatch, capsys):
    # Exercise the real adapter, cache, SQLite and CLI summary with synthetic minute responses.
    from test_commissioning import fixture_module

    terminal(monkeypatch)
    monkeypatch.setattr(getpass, "getpass", lambda p: KEY if "Key ID" in p else SECRET)
    bundle = fixture_module.bundle()
    grouped = {}
    for b in bundle["bars"]:
        from tradeagent.research.domain import iso

        grouped.setdefault(b["symbol"], []).append(
            {
                "t": iso(b["start"]),
                "o": b["open"],
                "h": b["high"],
                "l": b["low"],
                "c": b["close"],
                "v": b["volume"],
            }
        )

    @contextmanager
    def response(request, timeout):
        import io

        yield io.StringIO(json.dumps({"bars": grouped, "next_page_token": None}))

    monkeypatch.setattr(history_data, "urlopen", response)
    assert (
        main(
            [
                "replay-history",
                "--sessions",
                "6",
                "--as-of",
                "2026-01-12",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out)["integrity"]["predictions_resolved"] == 134
    assert_absent(str(captured))
    for file in tmp_path.rglob("*"):
        if file.is_file():
            assert_absent(file.read_bytes().decode("latin1"))
    assert not os.getenv("APCA_API_KEY_ID") and not os.getenv("APCA_API_SECRET_KEY")
