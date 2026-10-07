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


if __name__ == "__main__":
    os.umask(0o077)
    main()
