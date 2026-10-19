"""Local dashboard: read-only views, unit discovery and a locked-down HTTP server."""

import http.client
import json
import os
import sqlite3
import threading
from pathlib import Path

import dashboard_fixtures as demo
import pytest

from tradeagent.dashboard import jobs, readers
from tradeagent.dashboard.server import Workspace, serve
from tradeagent.oneshot import run_paper


def tree(root):
    """Every path under root with its size and mtime: proof that reading wrote nothing."""
    found = {}
    for path in sorted(Path(root).rglob("*")):
        info = path.stat()
        found[str(path)] = (path.is_dir(), info.st_size, info.st_mtime_ns)
    return found


@pytest.fixture(scope="module")
def workspace_dirs(tmp_path_factory):
    root = tmp_path_factory.mktemp("dashboard")
    demo.build_daily(root / "daily", sessions=3)
    run_paper(root / "paper", "25", 3600, 3, "full_fill", "full_fill", None)
    from tradeagent.research.demo import run_demo

    run_demo(root / "lab")
    return root


def test_daily_state_reports_runs_arms_and_honest_verdicts(workspace_dirs):
    state = readers.daily_state(workspace_dirs / "daily")
    assert state["protocol"] == "daily-four-arm-v1"
    assert state["evidence_kind"] == "synthetic"
    assert len(state["runs"]) == 3 and all(r["decision"] for r in state["runs"])
    assert all(r["outcome"] for r in state["runs"]) and state["valid_paired_days"] == 3
    assert [a["arm"] for a in state["arms"]] == readers.ARMS["daily-four-arm-v1"]
    # Three sessions are far too few for any verdict.
    assert all(
        a["vs_baseline_verdict"]["state"] == "collecting"
        for a in state["arms"]
        if a["arm"] != "seeded_random"
    )
    decision = state["runs"][0]["decision"]
    assert len(decision["candidates"]) == 3
    assert decision["candidates"][0]["analysis"]["price"]["thesis"]
    assert state["orders_submitted"] == 0
    assert len(state["series"]) == 3


def test_verdict_needs_twenty_sessions_and_a_clear_interval():
    assert readers.verdict({"days": 19, "mean": 0.01, "standard_error": 0.0001})["state"] == (
        "collecting"
    )
    assert readers.verdict({"days": 25, "mean": 0.01, "standard_error": 0.001})["state"] == "ahead"
    assert readers.verdict({"days": 25, "mean": -0.01, "standard_error": 0.001})["state"] == (
        "behind"
    )
    assert readers.verdict({"days": 25, "mean": 0.001, "standard_error": 0.01})["state"] == (
        "unclear"
    )
    assert readers.verdict(None)["state"] == "collecting"


def test_readers_never_write_or_create_files(workspace_dirs):
    before = tree(workspace_dirs)
    ws = Workspace(
        [workspace_dirs / "daily", workspace_dirs / "paper", workspace_dirs / "lab"],
        auto=False,
        doctor=False,
    )
    for _ in range(2):
        payload = ws.overview()
    run = payload["research"][0]["runs"][-1]
    assert ws.run(payload["research"][0]["id"], run["decision"]["decision_id"])
    assert tree(workspace_dirs) == before
    db = readers.connect(workspace_dirs / "daily" / "experience.sqlite3")
    with pytest.raises(sqlite3.OperationalError):
        db.execute("CREATE TABLE intruder(x)")
    db.close()
    assert readers.connect(workspace_dirs / "missing.sqlite3") is None
    assert not (workspace_dirs / "missing.sqlite3").exists()


def test_overview_combines_research_trades_lab_and_activity(workspace_dirs):
    ws = Workspace(
        [workspace_dirs / "daily", workspace_dirs / "paper", workspace_dirs / "lab"],
        auto=False,
        doctor=False,
    )
    payload = ws.overview()
    assert payload["errors"] == []
    assert payload["orders_submitted_by_dashboard"] == 0
    assert payload["schedule"]["example"] is True
    trade = payload["executions"][0]
    assert trade["mode"] == "PAPER" and trade["synthetic"] and trade["status"] == "COMPLETED"
    assert [o["side"] for o in trade["orders"]] == ["buy", "sell"]
    assert trade["realized_pnl"] == pytest.approx(-0.04)
    assert payload["labs"][0]["predictions"] > 0
    kinds = {item["kind"] for item in payload["activity"]}
    assert kinds == {"session", "trade", "lab"}
    json.dumps(payload, allow_nan=False)


def test_broken_state_is_reported_without_hiding_the_rest(workspace_dirs, tmp_path):
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "daily-deployment.json").write_text("{}")
    (broken / "experience.sqlite3").write_text("not a database")
    ws = Workspace([broken, workspace_dirs / "paper"], auto=False, doctor=False)
    payload = ws.overview()
    assert len(payload["errors"]) == 1 and payload["executions"]


def test_live_history_is_listed_newest_first(tmp_path):
    live = tmp_path / "live"
    run_paper(live, "25", 3600, 3, "full_fill", "full_fill", None)
    archived = tmp_path / "live.history" / "old"
    run_paper(archived, "25", 3600, 3, "full_fill", "full_fill", None)
    runs = readers.execution_history(live)
    assert len(runs) == 2 and runs[0]["started"] >= runs[1]["started"]
    assert any(r.get("archived") for r in runs)
    assert readers.execution_history(tmp_path / "nothing") == []


def test_live_settings_expose_only_the_summary_fields(tmp_path):
    path = tmp_path / "tradeagent.toml"
    path.write_text(
        '[live]\nsymbols = ["QQQ", "IWM"]\nmax_notional = "25"\naccount_selector = "SECRET-123"\n'
        '[entry]\ndollar_amount = "5"\n[exit]\nhold_seconds = 3600\n'
    )
    settings = readers.live_settings(path)
    assert settings == {
        "symbols": ["QQQ", "IWM"],
        "max_notional": 25.0,
        "dollar_amount": 5.0,
        "hold_seconds": 3600,
    }
    assert "SECRET" not in json.dumps(settings)
    assert readers.live_settings(tmp_path / "absent.toml") is None


def test_unit_discovery_reads_state_dir_protocol_and_schedule(tmp_path):
    (tmp_path / "tradeagent-daily-news.service").write_text(
        "[Service]\nExecStart=/venv/bin/python -m tradeagent.prospective.daily "
        "--state-dir /srv/research/news --oauth-helper /x --protocol daily-news-v2\n"
    )
    (tmp_path / "tradeagent-daily-news.timer").write_text(
        "[Timer]\nOnCalendar=Mon..Fri *-*-* 10:30:00 America/New_York\n"
        "OnCalendar=Mon..Fri *-*-* 11:00..59:00 America/New_York\n"
        "OnCalendar=*-*-* 17:05:00 America/New_York\n"
    )
    (tmp_path / "tradeagent-other.service").write_text("[Service]\nExecStart=/bin/true\n")
    found = jobs.discover(tmp_path, systemctl=False)
    assert len(found) == 1
    job = found[0]
    assert job["protocol"] == "daily-news-v2" and job["state_dir"] == "/srv/research/news"
    assert job["schedule"] == [
        {"weekdays": True, "start": 630, "end": 630},
        {"weekdays": True, "start": 660, "end": 719},
        {"weekdays": False, "start": 1025, "end": 1025},
    ]
    assert jobs.discover(tmp_path / "absent") == []


def test_non_daily_jobs_are_listed_but_their_state_is_not_misread(tmp_path, workspace_dirs):
    (tmp_path / "tradeagent-continuous-v5.service").write_text(
        "[Service]\nExecStart=/venv/bin/python -m tradeagent.prospective.continuous "
        f"--state-dir {workspace_dirs / 'lab'}\n"
    )
    ws = Workspace([], unit_dir=tmp_path, doctor=False, live_dir=tmp_path / "live")
    payload = ws.overview()
    assert [j["protocol"] for j in payload["jobs"]] == ["continuous-quant-v5"]
    assert payload["labs"] == [] and payload["errors"] == []


@pytest.fixture
def server(workspace_dirs):
    ws = Workspace([workspace_dirs / "daily", workspace_dirs / "paper"], auto=False, doctor=False)
    httpd, port = serve(ws, 0, attempts=1)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield port
    httpd.shutdown()
    httpd.server_close()


def request(port, path, method="GET", host=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, headers={"Host": host or f"127.0.0.1:{port}", **(headers or {})})
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response, body


def test_server_serves_the_app_and_api_with_strict_headers(server):
    response, body = request(server, "/")
    assert response.status == 200 and b"TradeAgent" in body
    assert "default-src 'none'" in response.getheader("Content-Security-Policy")
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    for asset in ("/app.js", "/app.css", "/favicon.svg"):
        assert request(server, asset)[0].status == 200
    response, body = request(server, "/api/overview")
    payload = json.loads(body)
    assert response.status == 200 and payload["research"] and payload["executions"]
    tag = response.getheader("ETag")
    assert request(server, "/api/overview", headers={"If-None-Match": tag})[0].status == 304
    source = payload["research"][0]
    decision = source["runs"][0]["decision"]["decision_id"]
    response, body = request(server, f"/api/run?source={source['id']}&decision={decision}")
    assert response.status == 200 and json.loads(body)["decision"]["decision_id"] == decision
    assert request(server, "/api/run?source=nope&decision=x")[0].status == 404
    assert request(server, "/api/runs?source=" + source["id"])[0].status == 200
    assert request(server, "/../../etc/passwd")[0].status == 404


def test_server_rejects_foreign_hosts_and_writes(server):
    assert request(server, "/api/overview", host="attacker.example")[0].status == 403
    assert request(server, "/", host=f"evil.test:{server}")[0].status == 403
    assert request(server, "/api/overview", host=f"localhost:{server}")[0].status == 200
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        assert request(server, "/api/overview", method=method)[0].status == 405


def test_cli_dashboard_help_lists_the_command(capsys):
    from tradeagent import friendly
    from tradeagent.cli import main

    assert "dashboard" in friendly.HELP
    with pytest.raises(SystemExit) as stop:
        main(["dashboard", "--help"])
    assert stop.value.code == 0
    assert "read-only" in capsys.readouterr().out.lower()


def test_static_assets_avoid_inline_code_and_unsafe_html():
    root = Path(__file__).resolve().parents[1] / "src/tradeagent/dashboard/static"
    html = (root / "index.html").read_text()
    script = (root / "app.js").read_text()
    assert "<script>" not in html and "style=" not in html
    assert "innerHTML" not in script and "eval(" not in script
    assert os.path.getsize(root / "app.js") < 200_000


def test_live_directory_follows_the_configured_state_dir(tmp_path):
    config = tmp_path / "tradeagent.toml"
    custom = tmp_path / "elsewhere" / "live"
    config.write_text(f'[live]\nstate_dir = "{custom}"\nsymbols = ["QQQ"]\n')
    assert readers.configured_live_dir(config, tmp_path / "default") == custom.resolve()
    config.write_text('[live]\nstate_dir = "relative/path"\n')
    assert readers.configured_live_dir(config, tmp_path / "default") == tmp_path / "default"
    assert readers.configured_live_dir(tmp_path / "absent.toml", tmp_path / "d") == tmp_path / "d"
    config.write_text(f'[live]\nstate_dir = "{custom}"\n')
    ws = Workspace([], config_path=config, doctor=False, unit_dir=tmp_path / "units")
    assert ws.live_dir == custom.resolve()


def test_protocols_with_their_own_scoring_are_listed_not_misread(workspace_dirs, tmp_path):
    import shutil

    copy = tmp_path / "v3"
    shutil.copytree(workspace_dirs / "daily", copy)
    deployment = json.loads((copy / "daily-deployment.json").read_text())
    deployment["config"]["protocol"] = "daily-bidirectional-v3"
    (copy / "daily-deployment.json").write_text(json.dumps(deployment))
    state = readers.daily_state(copy)
    assert state["scored"] is False and state["arms"] == [] and state["series"] == []
    assert state["valid_paired_days"] == 0 and state["report"].endswith("daily-report.md")
    assert all(run["outcome"] is None for run in state["runs"])


def test_ribbon_shows_only_enabled_daily_schedules(tmp_path):
    (tmp_path / "tradeagent-daily-research.service").write_text(
        "[Service]\nExecStart=/venv/bin/python -m tradeagent.prospective.daily "
        "--state-dir /srv/research/daily --oauth-helper /x\n"
    )
    (tmp_path / "tradeagent-daily-research.timer").write_text(
        "[Timer]\nOnCalendar=Mon..Fri *-*-* 10:00:00 America/New_York\n"
    )
    (tmp_path / "tradeagent-continuous-v5.service").write_text(
        "[Service]\nExecStart=/venv/bin/python -m tradeagent.prospective.continuous "
        "--state-dir /srv/research/v5\n"
    )
    (tmp_path / "tradeagent-continuous-v5.timer").write_text(
        "[Timer]\nOnCalendar=Mon..Fri *-*-* 09:31..59:00 America/New_York\n"
    )
    found = jobs.discover(tmp_path, systemctl=False)
    schedule = Workspace.schedule(None, found, [])
    assert schedule["example"] is False
    assert [m["unit"] for m in schedule["marks"]] == ["tradeagent-daily-research"]
    next(j for j in found if j["kind"] == "daily")["timer"] = {"enabled": False}
    disabled = Workspace.schedule(None, found, [])
    assert disabled == {"example": False, "marks": []}


def test_runs_show_the_starting_account_value_but_no_account_identity(tmp_path):
    live = tmp_path / "live"
    run_paper(live, "25", 3600, 3, "full_fill", "full_fill", None)
    [run] = readers.execution_history(live)
    assert run["account"]["nav"] > 0 and run["account"]["cash"] is not None
    assert set(run["account"]) == {"nav", "cash", "asof"}
    assert "account_number" not in json.dumps(run)


def test_an_open_run_shows_its_entry_fill_before_the_report_exists(tmp_path):
    run_dir = tmp_path / "live"
    (run_dir / "agent").mkdir(parents=True)
    db = sqlite3.connect(run_dir / "agent" / "state.sqlite3")
    db.execute("CREATE TABLE runs(id TEXT, started REAL, finished REAL, mode TEXT, status TEXT)")
    db.execute("CREATE TABLE events(seq INTEGER, run_id TEXT, time REAL, kind TEXT, payload TEXT)")
    db.execute("INSERT INTO runs VALUES('r', 100, NULL, 'LIVE', 'RUNNING')")
    order = {
        "side": "buy",
        "symbol": "TLT",
        "state": "filled",
        "cumulative_quantity": "0.011",
        "average_price": "89.62",
        "executions": [{"timestamp": "2026-10-19T14:36:00+00:00"}],
    }
    db.execute(
        "INSERT INTO events VALUES(1, 'r', 101, 'fill_reconciliation', ?)",
        (json.dumps({"order": order}),),
    )
    db.commit()
    db.close()
    run = readers.execution_run(run_dir)
    assert run["in_progress"] and run["orders"][0]["symbol"] == "TLT"
    assert run["orders"][0]["price"] == 89.62


def test_autotrade_records_reach_the_overview(tmp_path):
    records = tmp_path / "autotrade"
    records.mkdir()
    (records / "2026-10-19.json").write_text(
        json.dumps(
            {
                "session": "2026-10-19",
                "status": "NO_TRADE",
                "reason": "codex_news chose cash",
                "extra": "x",
            }
        )
    )
    (records / "notes.json").write_text("{}")
    (tmp_path / "tradeagent-autotrade.service").write_text(
        f"[Service]\nExecStart=/py -m tradeagent.autotrade run --config c --follow-dir f --state-dir {records}\n"
    )
    payload = Workspace([], unit_dir=tmp_path, doctor=False, live_dir=tmp_path / "live").overview()
    [record] = payload["autotrade"]["records"]
    assert record["status"] == "NO_TRADE" and "extra" not in record
    assert payload["errors"] == []
