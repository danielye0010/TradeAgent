"""`tradeagent publish`: a public results page built from the paper research only."""

import json
import subprocess

import dashboard_fixtures as demo
import pytest

from tradeagent import publish
from tradeagent.cli import main
from tradeagent.dashboard import jobs


@pytest.fixture(scope="module")
def research(tmp_path_factory):
    return demo.build_duel(tmp_path_factory.mktemp("research"), sessions=3)


def test_snapshot_has_both_models_spy_and_the_latest_reasoning(research):
    v2, claude = research
    data = publish.snapshot(v2, claude, now=1)
    assert data["first_session"] == data["claude_since"]
    assert data["models"] == {"codex": "gpt-6.1-sol", "claude": "claude-opus-5-5"}
    assert len(data["sessions"]) == 3 and all(s["scored"] for s in data["sessions"])
    for session in data["sessions"]:
        assert set(session) == {"date", "scored", "codex", "claude", "quant", "random", "spy"}
        assert isinstance(session["spy"], float)
        for key in ("codex", "claude", "quant", "random"):
            assert set(session[key]) == {"symbol", "answered", "net"}
    latest = data["latest"]
    assert latest["date"] == data["sessions"][-1]["date"]
    assert latest["codex"]["thesis"] and latest["claude"]["thesis"]
    assert data["duel"]["scored_days"] == 3 and "series" not in data["duel"]


def test_snapshot_describes_the_frozen_research_design(research):
    method = publish.snapshot(*research, now=1)["method"]
    assert set(method) == {"universe", "shortlist", "costs_bps"}
    assert "SPY" in method["universe"] and method["shortlist"] == 3
    assert set(method["costs_bps"]) == {"half_spread_bps", "slippage_bps", "fee_bps"}


def test_snapshot_holds_nothing_from_the_live_account(research):
    text = json.dumps(publish.snapshot(*research, now=1)).lower()
    for word in ("nav", "cash_balance", "account", "order", "robinhood", "/home", "token"):
        assert word not in text


def test_codex_only_research_still_publishes(research):
    data = publish.snapshot(research[0], None, now=1)
    assert data["claude_since"] is None and data["duel"] is None
    assert all(s["claude"] is None for s in data["sessions"])


def test_build_writes_a_self_contained_page(research, tmp_path):
    data = publish.snapshot(*research, now=1)
    out = publish.build(tmp_path / "site", data)
    page = (out / "index.html").read_text()
    assert "__DATA__" not in page and '"schema":1' in page
    assert json.loads((out / "data.json").read_text()) == data
    assert {p.name for p in out.iterdir()} == set(publish.SITE_FILES)


def test_push_commits_the_site_to_gh_pages(research, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    data = publish.snapshot(*research, now=1)
    first = publish.push(data, str(remote), tmp_path / "work")
    assert first["status"] == "PUBLISHED" and first["through"] == data["latest"]["date"]
    again = publish.push(data, str(remote), tmp_path / "work")
    assert again["status"] == "UNCHANGED"
    # A fresh checkout (e.g. a new computer) continues the same branch.
    newer = {**data, "generated_at": 2}
    assert publish.push(newer, str(remote), tmp_path / "other")["status"] == "PUBLISHED"
    log = subprocess.run(
        ["git", "--git-dir", str(remote), "log", "--format=%s", "gh-pages"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert len(log) == 2 and log[0].startswith("Results through")
    files = subprocess.run(
        ["git", "--git-dir", str(remote), "ls-tree", "--name-only", "gh-pages"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert set(files) == set(publish.SITE_FILES)


def test_push_failure_explains_how_to_sign_in(research, tmp_path):
    data = publish.snapshot(*research, now=1)
    with pytest.raises(RuntimeError, match="gh auth login"):
        publish.push(data, str(tmp_path / "missing.git"), tmp_path / "work")


def test_timer_publishes_after_the_close_and_finds_the_research(research, tmp_path):
    v2, claude = research
    units = tmp_path / "units"
    units.mkdir()
    for name, module, state, extra in (
        ("tradeagent-daily-news", "tradeagent.prospective.daily", v2, " --protocol daily-news-v2"),
        ("tradeagent-claude-compare", "tradeagent.prospective.claude_compare", claude, ""),
    ):
        (units / f"{name}.service").write_text(
            f"[Service]\nExecStart=/py -m {module} --state-dir {state}{extra}\n"
        )
    found = publish.research_dirs(unit_dir=units)
    assert found == (v2.resolve(), claude.resolve())
    summary = publish.install(
        "git@github.com:me/TradeAgent.git", *found, enable=False, unit_dir=units, systemctl=False
    )
    assert summary["runs"] == "weekdays 17:20 New York time"
    service = (units / "tradeagent-publish.service").read_text()
    assert "-m tradeagent.publish run --remote git@github.com:me/TradeAgent.git" in service
    assert "Persistent=true" in (units / "tradeagent-publish.timer").read_text()
    # The dashboard does not mistake the publisher for a research job.
    assert all(j["unit"] != "tradeagent-publish" for j in jobs.discover(units, systemctl=False))
    assert publish.uninstall(units, systemctl=False)["published_site"] == "kept"


def test_cli_dispatches_publish(capsys):
    with pytest.raises(SystemExit):
        main(["publish", "--help"])
    assert "results page" in capsys.readouterr().out


def test_claude_research_must_follow_the_same_v2_run(research, tmp_path):
    other = tmp_path / "other-claude"
    other.mkdir()
    deployment = json.loads((research[1] / "daily-deployment.json").read_text())
    deployment["config"]["follow_dir"] = str(tmp_path / "another-v2")
    (other / "daily-deployment.json").write_text(json.dumps(deployment))
    with pytest.raises(ValueError, match="follows"):
        publish.snapshot(research[0], other, now=1)


def test_spy_as_a_candidate_reports_its_own_move():
    from tradeagent.dashboard import readers

    outcome = {
        "decision_id": "d",
        "comparisons": {},
        "candidate_outcomes": {
            "SPY": {"gross": 0.01, "base_net": 0.009, "benchmark": "QQQ", "benchmark_gross": 0.02}
        },
    }
    assert readers.summarize_outcome(outcome)["spy"] == 0.01


def test_unified_home_has_two_horizons_and_legacy_redirect(research, tmp_path):
    daily = publish.snapshot(*research, now=1)
    weekly = {
        "protocol": "weekly-hold-v1",
        "evidence_kind": "prospective",
        "status": "PENDING_START",
        "start_date": "2026-11-02",
        "books": {"main:codex": {"nav": 10000}},
        "weeks": [],
    }
    out = publish.build(tmp_path / "unified", daily, weekly)
    html = (out / "index.html").read_text()
    assert 'role="tablist"' in html and 'id="weekly-root"' in html and 'id="intraday-shell"' in html
    assert "<iframe" not in html
    assert json.loads((out / "data.json").read_text()) == daily
    assert json.loads((out / "weekly.json").read_text()) == weekly
    assert "url=./#weekly" in (out / "weekly.html").read_text()
    component = (out / "weekly-view.js").read_text()
    assert "attachShadow" in component and "TradeAgentWeekly.mount" in component
    assert "embedded: true" in component and "Five $10,000 portfolios" in component
    assert "五个 1 万美元组合" in component  # both languages ship in one component


def test_unified_public_site_rejects_historical_weekly_data(research, tmp_path):
    with pytest.raises(ValueError, match="nonprospective"):
        publish.build(
            tmp_path / "bad", publish.snapshot(*research, now=1), {"evidence_kind": "historical"}
        )


def test_unified_publish_preserves_weekly_across_both_daily_refreshes(research, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    daily = publish.snapshot(*research, now=1)
    weekly = {
        "protocol": "weekly-hold-v1",
        "evidence_kind": "prospective",
        "status": "PENDING_START",
        "start_date": "2026-11-02",
        "books": {},
        "weeks": [],
    }
    work = tmp_path / "public"
    publish.push(daily, str(remote), work, weekly)
    publish.push({**daily, "generated_at": 2}, str(remote), work, weekly)
    assert json.loads((work / "weekly.json").read_text()) == weekly
    assert 'role="tablist"' in (work / "index.html").read_text()


def test_public_header_follows_the_page_language(research, tmp_path):
    import re

    out = publish.build(tmp_path / "site", publish.snapshot(*research, now=1))
    tabs = (out / "index.html").read_text().split('id="tabs"', 1)[1].split("</nav>", 1)[0]
    assert not re.search(r"[一-鿿]", tabs)  # no hard-coded mixed-language labels
    portal = (out / "portal.js").read_text()
    assert "周度持仓" in portal and "Weekly portfolios" in portal and "ta:lang" in portal
    assert "ta:lang" in (out / "site.js").read_text()
