"""Install independent SEC, subscription-AI, market and report SHADOW workers."""

import argparse
import json
import subprocess
from pathlib import Path

from tradeagent.prospective.event_assessor import subscription_status
from tradeagent.prospective.event_store import PROTOCOL, VERSION

BASE = "tradeagent-event-v6-"
SCHEDULES = {
    "poll": ("Mon..Fri *-*-* 06..21:02/15:00",),
    "assess": ("Mon..Fri *-*-* 06..22:*:25",),
    "market": (
        "Mon..Fri *-*-* 09:46..59:45",
        "Mon..Fri *-*-* 10..15:*:45",
        "Mon..Fri *-*-* 16:00:45",
    ),
    "report": ("Mon..Fri *-*-* 06..22:05/15:10",),
}


def units(python, state, user_agent, codex, v5_state):
    for path in (python, state, user_agent, codex, v5_state):
        if not path.is_absolute() or any(c in str(path) for c in '\n\r%"\\ '):
            raise ValueError("absolute plain deployment paths required")
    result = {}
    for worker, times in SCHEDULES.items():
        name = BASE + worker
        extra = (
            f" --sec-user-agent-file {user_agent}"
            if worker == "poll"
            else f" --codex {codex}"
            if worker == "assess"
            else f" --v5-state-dir {v5_state}"
            if worker in {"report", "market"}
            else ""
        )
        ordering = "tradeagent-daily-research.service tradeagent-daily-news.service"
        if worker == "market":
            ordering += " tradeagent-continuous-v5.service"
        result[name + ".service"] = f"""[Unit]
Description=TradeAgent v6 {worker} SHADOW (zero broker writes)
After=network-online.target {ordering}
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory={state.parent}
ExecStart={python} -m tradeagent.prospective.events {worker} --state-dir {state}{extra}
TimeoutStartSec={240 if worker == "assess" else 150 if worker == "poll" else 90}
KillMode=control-group
UMask=0077
NoNewPrivileges=true
Nice=10
LimitCORE=0
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
StandardOutput=null
StandardError=journal
"""
        calendar = "".join(f"OnCalendar={t} America/New_York\n" for t in times)
        result[name + ".timer"] = f"""[Unit]
Description=TradeAgent isolated v6 {worker} schedule

[Timer]
{calendar}AccuracySec=1s
RandomizedDelaySec=0
Persistent=false
Unit={name}.service

[Install]
WantedBy=timers.target
"""
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--python", type=Path, required=True)
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--sec-user-agent-file", type=Path, required=True)
    p.add_argument("--codex", type=Path, default=Path.home() / ".local/bin/codex")
    p.add_argument("--v5-state-dir", type=Path, required=True)
    p.add_argument("--enable", action="store_true")
    p.add_argument("--readiness", type=Path)
    args = p.parse_args()
    if not args.python.is_file() or not args.sec_user_agent_file.is_file():
        raise ValueError("installed interpreter and genuine SEC contact file required")
    directory = Path.home() / ".config/systemd/user"
    directory.mkdir(parents=True, exist_ok=True)
    args.state_dir.parent.mkdir(parents=True, exist_ok=True)
    definitions = units(
        args.python, args.state_dir, args.sec_user_agent_file, args.codex, args.v5_state_dir
    )
    for name, text in definitions.items():
        path = directory / name
        path.write_text(text)
        path.chmod(0o600)
    subprocess.run(
        ["systemd-analyze", "--user", "verify", *[str(directory / n) for n in definitions]],
        check=True,
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    readiness = json.loads(args.readiness.read_text()) if args.readiness else {}
    sec_ready = (
        readiness.get("protocol") == PROTOCOL
        and readiness.get("version") == VERSION
        and readiness.get("poll_status") == "COMPLETE"
        and readiness.get("issuer_coverage") == 21
        and readiness.get("original_document_read") is True
    )
    auth_ready = False
    if args.enable:
        try:
            subscription_status(str(args.codex))
            auth_ready = True
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            print(f"AI timer blocked: {exc}")
    status = {}
    for worker in SCHEDULES:
        enable = args.enable and (
            sec_ready if worker == "poll" else auth_ready if worker == "assess" else True
        )
        subprocess.run(
            [
                "systemctl",
                "--user",
                "enable" if enable else "disable",
                "--now",
                BASE + worker + ".timer",
            ],
            check=True,
        )
        status[worker] = "enabled" if enable else "disabled"
    print(
        json.dumps(
            {"protocol": PROTOCOL, "version": VERSION, "timers": status, "sec_readiness": sec_ready}
        )
    )


if __name__ == "__main__":
    main()
