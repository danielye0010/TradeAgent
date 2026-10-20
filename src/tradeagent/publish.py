"""`tradeagent publish`: a public results page for the daily AI research.

    tradeagent publish build --out DIR        # write the site locally to preview it
    tradeagent publish run --remote URL       # build and push it to the gh-pages branch
    tradeagent publish install --remote URL --enable   # do that every weekday after the close
    tradeagent publish uninstall

The page shows the paper research only: each session's frozen picks from Codex (GPT)
and Claude, their modeled results after costs, SPY over the same hour, Quant and Random,
and the models' own reasoning. It never reads or shows the live account, orders,
balances or settings. Pushing uses your normal git sign-in for the remote.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path

from .autotrade import discover_follow_dir
from .paths import state_home

UNIT = "tradeagent-publish"
V2 = "daily-news-v2"
CLAUDE = "daily-news-claude-v1"
BRANCH = "gh-pages"
REPO_URL = "https://github.com/danielye0010/TradeAgent"
# After both research services have scored the day (12:00/12:10) and written reports.
TIME = "Mon..Fri *-*-* 17:20:00"
SITE_FILES = ("index.html", "site.css", "site.js", "data.json", ".nojekyll")
ARMS = {
    "codex": "codex_news",
    "claude": "claude_news",
    "quant": "quant_only",
    "random": "seeded_random",
}


def site_dir():
    return state_home() / "public-site"


def _pick(run, arm):
    """The frozen pick and its scored result: symbol None = cash, status = answered or not."""
    pick = ((run.get("decision") or {}).get("arms") or {}).get(arm)
    if not pick:
        return None
    result = ((run.get("outcome") or {}).get("arms") or {}).get(arm) if run.get("outcome") else None
    answered = pick.get("status") != "UNAVAILABLE"
    return {
        "symbol": pick.get("symbol") if answered else None,
        "answered": answered,
        "net": result.get("net") if result else None,
    }


def _note(run, symbol, tag):
    """One model's reasoning for its pick (or its top-ranked stock when it held cash)."""
    candidates = (run.get("decision") or {}).get("candidates") or []
    notes = [(c["symbol"], (c.get("analysis") or {}).get(tag)) for c in candidates]
    notes = [(s, n) for s, n in notes if n]
    chosen = next((x for x in notes if x[0] == symbol), None) if symbol else None
    chosen = chosen or min(notes, key=lambda x: x[1].get("rank") or 99, default=None)
    if not chosen:
        return None
    sym, note = chosen
    return {
        "symbol": sym,
        "stance": note.get("stance"),
        "thesis": note.get("thesis"),
        "catalyst": note.get("catalyst"),
        "sources": [
            {"title": s.get("title"), "url": s.get("url")} for s in (note.get("sources") or [])
        ][:3],
    }


def snapshot(v2_dir, claude_dir=None, *, now=None):
    """Public, paper-only summary of the daily research; nothing about the live account."""
    from .dashboard import readers

    v2 = readers.daily_state(v2_dir)
    if v2["protocol"] != V2:
        raise ValueError(f"{v2_dir} is not a {V2} research directory")
    kind = v2["evidence_kind"]
    claude = readers.daily_state(claude_dir) if claude_dir else None
    if claude and claude["protocol"] != CLAUDE:
        raise ValueError(f"{claude_dir} is not a {CLAUDE} research directory")
    if claude:
        # Only a comparison that follows this very v2 run shares its shortlists.
        config = (readers.read_json(Path(claude_dir) / "daily-deployment.json") or {}).get("config")
        followed = (config or {}).get("follow_dir")
        if not followed or Path(followed).resolve() != Path(v2_dir).resolve():
            raise ValueError(f"{claude_dir} follows {followed}, not {v2_dir}")
    claude_runs = {
        r["session_date"]: r for r in (claude["runs"] if claude else []) if r.get("decision")
    }
    sessions = []
    for run in v2["runs"]:
        if not run.get("decision"):
            continue
        other = claude_runs.get(run["session_date"])
        sessions.append(
            {
                "date": run["session_date"],
                "scored": bool(run.get("outcome")),
                "codex": _pick(run, ARMS["codex"]),
                "claude": _pick(other, ARMS["claude"]) if other else None,
                "quant": _pick(run, ARMS["quant"]),
                "random": _pick(run, ARMS["random"]),
                "spy": (run.get("outcome") or {}).get("spy"),
            }
        )
    latest = sessions[-1]["date"] if sessions else None
    picks = {}
    if latest:
        run = next(r for r in v2["runs"] if r["session_date"] == latest)
        picks["codex"] = _note(run, sessions[-1]["codex"]["symbol"], "news")
        other = claude_runs.get(latest)
        if other and sessions[-1]["claude"]:
            picks["claude"] = _note(other, sessions[-1]["claude"]["symbol"], "news")
    duel = None
    if claude and claude.get("duel"):
        news = claude["duel"]["modes"]["news"]
        duel = {k: v for k, v in news.items() if k != "series"}
    return {
        "schema": 1,
        "generated_at": time.time() if now is None else now,
        "evidence_kind": kind,
        "first_session": sessions[0]["date"] if sessions else None,
        "claude_since": next((s["date"] for s in sessions if s["claude"]), None),
        "models": {
            "codex": v2["config"].get("codex_model"),
            "claude": claude["config"].get("claude_model") if claude else None,
        },
        "holding_minutes": round((v2["config"].get("holding_seconds") or 3600) / 60),
        "method": _method(
            (readers.read_json(Path(v2_dir) / "daily-deployment.json") or {}).get("config") or {}
        ),
        "sessions": sessions,
        "latest": {"date": latest, **picks},
        "duel": duel,
        "repo": REPO_URL,
    }


def _method(config):
    """The frozen research design the page describes: universe, shortlist and costs."""
    costs = (config.get("costs") or {}).get("base") or {}
    return {
        "universe": list(config.get("universe") or []),
        "shortlist": config.get("candidates"),
        "costs_bps": {
            k: costs[k] for k in ("half_spread_bps", "slippage_bps", "fee_bps") if k in costs
        },
    }


def _template(name):
    return resources.files("tradeagent").joinpath("site", name).read_text(encoding="utf-8")


def build(out_dir, data):
    """Write a self-contained static site (data inlined; also saved as data.json)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(data, allow_nan=False, separators=(",", ":"))
    page = _template("index.html").replace("__DATA__", encoded.replace("</", "<\\/"))
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    for name in ("site.css", "site.js"):
        (out_dir / name).write_text(_template(name), encoding="utf-8")
    (out_dir / "data.json").write_text(encoded, encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return out_dir


def _git(directory, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(directory), *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=120,
    )


def push(data, remote, work_dir):
    """Commit the built site to the gh-pages branch of ``remote`` and push it."""
    work = Path(work_dir)
    if not (work / ".git").is_dir():
        work.mkdir(parents=True, exist_ok=True)
        _git(work, "init", "-q")
        _git(work, "remote", "add", "origin", remote)
    else:
        _git(work, "remote", "set-url", "origin", remote)
    fetched = _git(work, "fetch", "-q", "--depth", "1", "origin", BRANCH, check=False)
    if fetched.returncode == 0:
        _git(work, "checkout", "-q", "-B", BRANCH, "FETCH_HEAD")
    elif _git(work, "rev-parse", "--verify", "-q", BRANCH, check=False).returncode != 0:
        _git(work, "checkout", "-q", "--orphan", BRANCH)
    build(work, data)
    _git(work, "add", "--", *SITE_FILES)
    if _git(work, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return {"status": "UNCHANGED", "branch": BRANCH}
    day = data.get("latest", {}).get("date") or time.strftime("%Y-%m-%d")
    _git(
        work,
        "-c",
        "user.name=TradeAgent",
        "-c",
        "user.email=tradeagent@users.noreply.github.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        f"Results through {day}",
    )
    pushed = _git(work, "push", "-q", "origin", BRANCH, check=False)
    if pushed.returncode:
        raise RuntimeError(
            "git push failed; sign in for this remote (for example `gh auth login` and "
            "`gh auth setup-git`, or an SSH remote) and run again"
        )
    return {"status": "PUBLISHED", "branch": BRANCH, "through": day}


def research_dirs(v2_dir=None, claude_dir=None, unit_dir=None):
    v2_dir = v2_dir or discover_follow_dir(unit_dir, V2)
    if v2_dir is None:
        raise ValueError(f"no {V2} research found; pass --research-dir DIR (its --state-dir)")
    claude_dir = claude_dir or discover_follow_dir(unit_dir, CLAUDE)
    return Path(v2_dir).resolve(), Path(claude_dir).resolve() if claude_dir else None


def units(python, remote, v2_dir, claude_dir):
    extra = f" --claude-dir {claude_dir}" if claude_dir else ""
    service = f"""[Unit]
Description=TradeAgent public results page (paper research only)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={python} -m tradeagent.publish run --remote {remote} --research-dir {v2_dir}{extra}
TimeoutStartSec=10min
UMask=0077
NoNewPrivileges=true
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
"""
    timer = f"""[Unit]
Description=TradeAgent public results page, after the close

[Timer]
OnCalendar={TIME} America/New_York
Persistent=true
Unit={UNIT}.service

[Install]
WantedBy=timers.target
"""
    return {f"{UNIT}.service": service, f"{UNIT}.timer": timer}


def install(remote, v2_dir, claude_dir, *, enable, unit_dir=None, systemctl=True):
    for value in (remote, str(v2_dir), str(claude_dir or ""), sys.executable):
        if any(c in value for c in '\n\r%"\\ '):
            raise ValueError(f"unsupported value for a systemd unit: {value}")
    directory = Path(unit_dir or Path.home() / ".config/systemd/user")
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in units(sys.executable, remote, v2_dir, claude_dir).items():
        (directory / name).write_text(text)
        (directory / name).chmod(0o600)
    summary = {
        "timer": f"{UNIT}.timer",
        "runs": "weekdays 17:20 New York time",
        "publishes": f"{remote} branch {BRANCH}",
        "research": str(v2_dir),
        "claude": str(claude_dir) if claude_dir else None,
        "enabled": False,
    }
    if systemctl:
        if shutil.which("systemctl") is None:
            raise RuntimeError("systemd is not available; enable it in WSL first")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        action = ["enable", "--now"] if enable else ["disable", "--now"]
        subprocess.run(["systemctl", "--user", *action, f"{UNIT}.timer"], check=True)
        summary["enabled"] = enable
    return summary


def uninstall(unit_dir=None, *, systemctl=True):
    directory = Path(unit_dir or Path.home() / ".config/systemd/user")
    if systemctl and shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"], check=False)
    for suffix in (".service", ".timer"):
        (directory / (UNIT + suffix)).unlink(missing_ok=True)
    if systemctl and shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    return {"removed": f"{UNIT}.timer", "published_site": "kept"}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="tradeagent publish",
        description="Publish the daily paper research as a public results page.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    local = commands.add_parser("build", help="write the site to a folder to preview it")
    local.add_argument("--out", type=Path, required=True)
    once = commands.add_parser("run", help="build and push to the gh-pages branch")
    setup = commands.add_parser("install", help="publish every weekday after the close")
    setup.add_argument("--enable", action="store_true")
    for command in (once, setup):
        command.add_argument("--remote", required=True, help="git remote URL of the site repo")
    for command in (local, once, setup):
        command.add_argument("--research-dir", type=Path, help="the daily-news-v2 --state-dir")
        command.add_argument("--claude-dir", type=Path, help="the Claude comparison --state-dir")
    commands.add_parser("uninstall")
    args = parser.parse_args(argv)
    try:
        if args.command == "uninstall":
            print(json.dumps(uninstall()))
            return 0
        v2_dir, claude_dir = research_dirs(args.research_dir, args.claude_dir)
        if args.command == "install":
            print(
                json.dumps(install(args.remote, v2_dir, claude_dir, enable=args.enable), indent=2)
            )
            return 0
        data = snapshot(v2_dir, claude_dir)
        if args.command == "build":
            print(json.dumps({"built": str(build(args.out, data).resolve())}))
            return 0
        print(json.dumps(push(data, args.remote, site_dir())))
        return 0
    except Exception as error:
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
