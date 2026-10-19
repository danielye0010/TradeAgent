"""`tradeagent claude`: run Claude next to Codex on the daily news research.

    tradeagent claude doctor              # CLI installed, subscription login, v2 found
    tradeagent claude doctor --probe      # plus one real (unrecorded) assessment
    tradeagent claude install --enable    # daily-news-claude-v1 timer next to daily-news-v2
    tradeagent claude uninstall

Claude answers the same frozen daily-news-v2 question as Codex (see
``prospective/claude_compare.py``). It runs on the Claude Code CLI with the owner's
Claude subscription login, never an API key. To trade Claude's pick instead of
Codex's, install autotrade with ``--model claude`` afterwards.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .autotrade import discover_follow_dir
from .prospective import claude_assessor
from .prospective.claude_compare import PROTOCOL, Follow
from .prospective.daily import NEWS_PROTOCOL

UNIT = "tradeagent-claude-compare"
# Same start as daily-news-v2 (both answer in its window), then after its 12:00 scoring.
TIMES = ("Mon..Fri *-*-* 10:30:00", "Mon..Fri *-*-* 12:10:00", "*-*-* 17:10:00")
UNIT_DIR = Path.home() / ".config/systemd/user"


def follow_dir_or_error(follow_dir, unit_dir=None):
    found = follow_dir or discover_follow_dir(unit_dir, NEWS_PROTOCOL)
    if found is None:
        raise ValueError(
            "no daily-news-v2 research found; install it first or pass --follow-dir DIR "
            "(its --state-dir)"
        )
    Follow(found)  # must be a daily-news-v2 deployment
    return Path(found).resolve()


def doctor(follow_dir=None, *, probe=False, executable="claude", unit_dir=None):
    """Readiness checks; nothing is written. ``probe`` makes one real, unrecorded call."""
    checks = []

    def check(name, action):
        try:
            value = action()
        except Exception as error:  # report every check, not just the first failure
            checks.append({"check": name, "ok": False, "detail": str(error)})
            return None
        checks.append({"check": name, "ok": True, "detail": value})
        return value

    env = claude_assessor.environment()
    check("claude_cli", lambda: claude_assessor.version(executable, env))
    check("subscription_login", lambda: claude_assessor.login(executable, env))
    follow = check("daily_news_v2", lambda: str(follow_dir_or_error(follow_dir, unit_dir)))
    if probe:

        def ask():
            if follow is None:
                raise ValueError("needs the daily-news-v2 directory")
            scans = Follow(follow).query(
                "SELECT payload FROM opportunity_scans ORDER BY recorded_at DESC LIMIT 1"
            )
            if not scans:
                raise ValueError("daily-news-v2 has no saved scan yet; try after a session")
            _, provenance = claude_assessor.assess_news(scans[0], executable=executable)
            return {k: provenance[k] for k in ("model", "web_searches", "num_turns", "auth_method")}

        check("probe_news_assessment", ask)
    return {"ready": all(c["ok"] for c in checks), "checks": checks}


def units(python, follow_dir, state_dir):
    calendar = "".join(f"OnCalendar={t} America/New_York\n" for t in TIMES)
    service = f"""[Unit]
Description=TradeAgent Claude vs Codex SHADOW research ({PROTOCOL}; zero broker writes)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory={state_dir.parent}
ExecStart={python} -m tradeagent.prospective.claude_compare --state-dir {state_dir} --follow-dir {follow_dir}
TimeoutStartSec=15min
UMask=0077
NoNewPrivileges=true
LimitCORE=0
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
StandardOutput=null
StandardError=journal
"""
    timer = f"""[Unit]
Description=TradeAgent Claude vs Codex decision, scoring and report

[Timer]
{calendar}AccuracySec=1s
RandomizedDelaySec=0
Persistent=true
Unit={UNIT}.service

[Install]
WantedBy=timers.target
"""
    return {f"{UNIT}.service": service, f"{UNIT}.timer": timer}


def install(follow_dir, state_dir, *, enable, unit_dir=None, systemctl=True):
    follow_dir = follow_dir_or_error(follow_dir, unit_dir)
    state_dir = Path(state_dir or follow_dir.parent / PROTOCOL).resolve()
    if state_dir == follow_dir:
        raise ValueError("the Claude comparison needs its own --state-dir")
    for value in (str(follow_dir), str(state_dir), sys.executable):
        if any(c in value for c in '\n\r%"\\ '):
            raise ValueError(f"unsupported path for a systemd unit: {value}")
    directory = Path(unit_dir or UNIT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in units(sys.executable, follow_dir, state_dir).items():
        (directory / name).write_text(text)
        (directory / name).chmod(0o600)
    summary = {
        "timer": f"{UNIT}.timer",
        "protocol": PROTOCOL,
        "follows": str(follow_dir),
        "state_dir": str(state_dir),
        "runs": "weekdays 10:30 (decide) and 12:10 (score), daily 17:10 (report), New York",
        "model": claude_assessor.MODEL,
        "orders": "none; research only. To trade Claude's pick: "
        "tradeagent autotrade install --model claude --enable",
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
    """Stops the timer; collected research records are kept."""
    directory = Path(unit_dir or UNIT_DIR)
    if systemctl and shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"], check=False)
    for suffix in (".service", ".timer"):
        (directory / (UNIT + suffix)).unlink(missing_ok=True)
    if systemctl and shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    return {"removed": f"{UNIT}.timer", "records": "kept"}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="tradeagent claude",
        description="Run Claude next to Codex on the daily news research (subscription CLI).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("doctor", help="check the Claude CLI, login and research")
    check.add_argument("--probe", action="store_true", help="also make one real assessment")
    setup = commands.add_parser("install", help="install the daily Claude comparison timer")
    setup.add_argument("--enable", action="store_true", help="start the timer now")
    setup.add_argument("--state-dir", type=Path, default=None)
    for command in (check, setup):
        command.add_argument(
            "--follow-dir", type=Path, default=None, help="the daily-news-v2 --state-dir"
        )
    commands.add_parser("uninstall", help="remove the timer (records are kept)")
    args = parser.parse_args(argv)
    follow = Path(args.follow_dir).expanduser() if getattr(args, "follow_dir", None) else None
    try:
        if args.command == "doctor":
            result = doctor(follow, probe=args.probe)
            print(json.dumps(result, indent=2))
            return 0 if result["ready"] else 1
        if args.command == "install":
            state = Path(args.state_dir).expanduser() if args.state_dir else None
            print(json.dumps(install(follow, state, enable=args.enable), indent=2))
            return 0
        print(json.dumps(uninstall()))
        return 0
    except Exception as error:
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
