"""Install one shadow owner with Robinhood default or explicit optional Alpaca."""

import argparse
import os
import subprocess
from pathlib import Path

from tradeagent.prospective.access import private_file

UNIT = "tradeagent-prospective.service"


def main():
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument(
        "--market-data-provider", choices=("robinhood", "alpaca"), default="robinhood"
    )
    parser.add_argument("--oauth-helper", type=Path)
    parser.add_argument("--daily", action="store_true", help="install the four-arm research timer")
    parser.add_argument(
        "--python", type=Path, help="installed-wheel interpreter for daily research"
    )
    parser.add_argument(
        "--protocol",
        choices=("daily-four-arm-v1", "daily-news-v2"),
        default="daily-four-arm-v1",
        help="daily research experiment; each protocol needs its own --state-dir",
    )
    args = parser.parse_args()
    state = (args.state_dir or repo / ("work/prospective-" + args.market_data_provider)).resolve()
    helper = args.oauth_helper or Path.home() / ".local/libexec/robinhood-mcp-oauth-helper"
    if any(c in str(helper) for c in '\n\r%"\\ '):
        raise ValueError("unsupported OAuth helper path")
    if any(c in str(state) for c in '\n\r%"\\ '):
        raise ValueError("unsupported state path")
    credential_line = ""
    helper_argument = ""
    if args.market_data_provider == "alpaca":
        credential = Path.home() / ".local/share/tradeagent/prospective/alpaca.cred"
        if credential.exists():
            private_file(credential)
        else:
            print("Optional Alpaca encrypted input pending; systemd will fail closed.")
        credential_line = (
            "LoadCredentialEncrypted=alpaca:%h/.local/share/tradeagent/prospective/alpaca.cred\n"
        )
    else:
        helper_argument = f" --oauth-helper {helper}"
    if args.daily:
        if args.market_data_provider != "robinhood" or not args.python:
            raise ValueError("daily research requires explicit installed Python and Robinhood")
        install_daily(state, helper, args.python.absolute(), args.protocol)
        return
    directory = Path.home() / ".config/systemd/user"
    directory.mkdir(parents=True, exist_ok=True)
    unit = directory / UNIT
    # Values are fixed local paths; no credentials occur in arguments/environment.
    if any(c in str(repo) for c in '\n\r%"\\'):
        raise ValueError("unsupported service path")
    unit.write_text(f"""[Unit]
Description=TradeAgent forward-only zero-money prospective shadow
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory={repo}
ExecStart={repo}/.venv/bin/python -m tradeagent.prospective.service --state-dir {state} --market-data-provider {args.market_data_provider}{helper_argument}
{credential_line}
Restart=always
RestartSec=15
UMask=0077
NoNewPrivileges=true
LimitCORE=0
StandardOutput=null
StandardError=journal

[Install]
WantedBy=default.target
""")
    unit.chmod(0o600)
    for command in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", UNIT],
        ["systemctl", "--user", "restart", UNIT],
    ):
        subprocess.run(command, check=True)
    print("User shadow service enabled; inspect service/status for credential and data readiness.")


DAILY = {
    "daily-four-arm-v1": {
        "name": "tradeagent-daily-research",
        "title": "daily four-arm",
        "times": ("Mon..Fri *-*-* 10:00:00", "Mon..Fri *-*-* 11:30:00", "*-*-* 17:00:00"),
        "argument": "",
    },
    "daily-news-v2": {
        "name": "tradeagent-daily-news",
        "title": "daily news-reading five-arm",
        "times": ("Mon..Fri *-*-* 10:30:00", "Mon..Fri *-*-* 12:00:00", "*-*-* 17:05:00"),
        "argument": " --protocol daily-news-v2",
    },
}


def install_daily(state, helper, python, protocol="daily-four-arm-v1"):
    if not python.is_file() or any(c in str(python) for c in '\n\r%"\\ '):
        raise ValueError("invalid installed interpreter")
    directory = Path.home() / ".config/systemd/user"
    directory.mkdir(parents=True, exist_ok=True)
    spec = DAILY[protocol]
    service, timer = spec["name"] + ".service", spec["name"] + ".timer"
    calendar = "".join(f"OnCalendar={t} America/New_York\n" for t in spec["times"])
    units = {
        service: f"""[Unit]
Description=TradeAgent {spec["title"]} SHADOW research (zero broker writes)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory={state.parent}
ExecStart={python} -m tradeagent.prospective.daily --state-dir {state} --oauth-helper {helper}{spec["argument"]}
TimeoutStartSec=15min
UMask=0077
NoNewPrivileges=true
LimitCORE=0
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
StandardOutput=null
StandardError=journal
""",
        timer: f"""[Unit]
Description=TradeAgent session capture and delayed outcome resolution

[Timer]
{calendar}AccuracySec=1s
RandomizedDelaySec=0
Persistent=true
Unit={service}

[Install]
WantedBy=timers.target
""",
    }
    state.parent.mkdir(parents=True, exist_ok=True)
    for name, text in units.items():
        path = directory / name
        path.write_text(text)
        path.chmod(0o600)
    for command in (
        ["systemd-analyze", "--user", "verify", str(directory / service), str(directory / timer)],
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", timer],
    ):
        subprocess.run(command, check=True)
    print("Daily SHADOW timer installed and enabled; LIVE configuration is unused.")


if __name__ == "__main__":
    os.umask(0o077)
    main()
