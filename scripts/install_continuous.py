"""Prepare or enable isolated v5 SHADOW units after measured read-only validation."""

import argparse
import json
import subprocess
from pathlib import Path

from tradeagent.prospective.continuous import PROTOCOL, VERSION


def units(python, state, helper):
    for path in (python, state, helper):
        if not path.is_absolute() or any(c in str(path) for c in '\n\r%"\\ '):
            raise ValueError("absolute plain local paths required")
    service = f"""[Unit]
Description=TradeAgent continuous Quant v5 SHADOW (two market read tools only)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory={state.parent}
ExecStart={python} -m tradeagent.prospective.continuous --state-dir {state} --oauth-helper {helper}
TimeoutStartSec=100
UMask=0077
NoNewPrivileges=true
LimitCORE=0
StandardOutput=null
StandardError=journal
"""
    timer = """[Unit]
Description=TradeAgent v5 forward endpoints and five-minute discovery

[Timer]
OnCalendar=Mon..Fri *-*-* 09:45..59:00/15 America/New_York
OnCalendar=Mon..Fri *-*-* 10..15:*:00/15 America/New_York
OnCalendar=Mon..Fri *-*-* 16:00..02:00/15 America/New_York
OnCalendar=Mon..Fri *-*-* 17:20:00 America/New_York
AccuracySec=1s
RandomizedDelaySec=0
Persistent=false
Unit=tradeagent-continuous-v5.service

[Install]
WantedBy=timers.target
"""
    return service, timer


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--python", type=Path, required=True)
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument(
        "--oauth-helper",
        type=Path,
        default=Path.home() / ".local/libexec/robinhood-mcp-oauth-helper",
    )
    p.add_argument("--enable", action="store_true")
    p.add_argument("--measurement", type=Path)
    args = p.parse_args()
    if not args.python.is_file():
        raise ValueError("installed Python required")
    if args.enable:
        m = json.loads(args.measurement.read_text()) if args.measurement else {}
        if not (
            m.get("protocol") == PROTOCOL
            and m.get("version") == VERSION
            and m.get("complete")
            and 0 < m.get("elapsed_seconds", 1000) < 40
            and m.get("quote_coverage") == 31
            and m.get("bar_coverage") == 31
        ):
            raise ValueError("complete measured 31-symbol forward capture under 40s required")
    directory = Path.home() / ".config/systemd/user"
    directory.mkdir(parents=True, exist_ok=True)
    args.state_dir.parent.mkdir(parents=True, exist_ok=True)
    names = ["tradeagent-continuous-v5.service", "tradeagent-continuous-v5.timer"]
    for name, content in zip(
        names, units(args.python, args.state_dir, args.oauth_helper), strict=True
    ):
        path = directory / name
        path.write_text(content)
        path.chmod(0o600)
    subprocess.run(
        ["systemd-analyze", "--user", "verify", *[str(directory / n) for n in names]], check=True
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "--user", "enable" if args.enable else "disable", "--now", names[1]],
        check=True,
    )


if __name__ == "__main__":
    main()
