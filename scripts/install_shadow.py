"""Install the narrow user service after operator-only encrypted input setup."""

import argparse
import os
import subprocess
from pathlib import Path

from tradeagent.prospective.access import private_file

UNIT = "tradeagent-prospective.service"


def main():
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=repo / "work/prospective-v2")
    state = parser.parse_args().state_dir.resolve()
    if any(c in str(state) for c in '\n\r%"\\ '):
        raise ValueError("unsupported state path")
    credential = Path.home() / ".local/share/tradeagent/prospective/alpaca.cred"
    if credential.exists():
        private_file(credential)
    else:
        print("Encrypted operator input pending; systemd will fail closed and retry.")
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
ExecStart={repo}/.venv/bin/python -m tradeagent.prospective.service --state-dir {state}
LoadCredentialEncrypted=alpaca:%h/.local/share/tradeagent/prospective/alpaca.cred
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
        ["systemctl", "--user", "enable", "--now", UNIT],
    ):
        subprocess.run(command, check=True)
    print("User shadow service enabled; inspect service/status for credential and data readiness.")


if __name__ == "__main__":
    os.umask(0o077)
    main()
