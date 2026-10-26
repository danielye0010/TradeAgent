"""Only tradeagent-weekly-* units are installed; existing protocols are untouched."""

import subprocess
import sys
from pathlib import Path

SCHEDULES = {
    "history": "Mon..Fri *-*-* 11:20:00 America/New_York",
    "packet": "Mon..Fri *-*-* 11:29:00 America/New_York",
    "decision": "Mon..Fri *-*-* 11:30..54:00 America/New_York",
    "fill": "Mon..Fri *-*-* 12:00..10:00,15,30,45 America/New_York",
    "risk": "Mon..Fri *-*-* 15:45:00 America/New_York",
    "report": "Mon..Fri *-*-* 17:15:00 America/New_York",
    "publish": "Mon..Fri *-*-* 17:25:00 America/New_York",
}


def units(python, state, *, protected=None):
    for value in (python, str(state), str(protected or "")):
        if any(c in value for c in '\n\r%"\\ '):
            raise ValueError("unsupported systemd path")
    result = {}
    for phase, schedule in SCHEDULES.items():
        name = "tradeagent-weekly-" + phase
        protection = f"ReadOnlyPaths={protected}\n" if protected else ""
        result[name + ".service"] = f"""[Unit]
Description=weekly-hold-v1 SHADOW {phase}, no broker writes
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={python} -m tradeagent.weekly {phase} --state-dir {state}
TimeoutStartSec={"26min" if phase == "decision" else "10min"}
UMask=0077
NoNewPrivileges=true
RestrictSUIDSGID=true
LimitCORE=0
Nice=10
InaccessiblePaths=-%h/.config/tradeagent/tradeagent.toml
{protection}Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
StandardOutput=journal
StandardError=journal
"""
        result[name + ".timer"] = f"""[Unit]
Description=weekly-hold-v1 {phase} (XNYS gate; New York time)

[Timer]
OnCalendar={schedule}
AccuracySec=1s
RandomizedDelaySec=0
Persistent=false
Unit={name}.service

[Install]
WantedBy=timers.target
"""
    result["tradeagent-weekly-dashboard.service"] = f"""[Unit]
Description=weekly-hold-v1 read-only localhost portfolio view
After=network.target

[Service]
ExecStart={python} -m tradeagent.weekly serve --state-dir {state} --port 8643
UMask=0077
NoNewPrivileges=true
Restart=on-failure
RestartSec=5
InaccessiblePaths=-%h/.config/tradeagent/tradeagent.toml

[Install]
WantedBy=default.target
"""
    return result


def install(state, *, python=sys.executable, unit_dir=None, enable=False, protected=None):
    directory = Path(unit_dir or Path.home() / ".config/systemd/user")
    directory.mkdir(parents=True, exist_ok=True)
    rendered = units(python, state, protected=protected)
    for name, text in rendered.items():
        path = directory / name
        if path.exists() and path.read_text() != text:
            raise ValueError("existing weekly unit differs; refusing implicit replacement")
        path.write_text(text)
        path.chmod(0o600)
    subprocess.run(
        [
            "systemd-analyze",
            "--user",
            "verify",
            *[str(directory / name) for name in rendered if name.endswith(".service")],
        ],
        check=True,
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    if enable:
        subprocess.run(
            [
                "systemctl",
                "--user",
                "enable",
                "--now",
                *["tradeagent-weekly-" + p + ".timer" for p in SCHEDULES],
                "tradeagent-weekly-dashboard.service",
            ],
            check=True,
        )
    return {
        "status": "ENABLED" if enable else "PREPARED",
        "units": list(rendered),
        "timezone": "America/New_York",
    }
