"""`tradeagent autotrade`: one experimental LIVE trade per trading day, unattended.

`autotrade run` is the daily job. It finishes any interrupted lifecycle (exit only),
scans the market, freezes an experimental decision and hands an eligible plan to the
existing LIVE engine, which owns sizing, risk checks, the order, the timed exit and
reconciliation. Every engine limit still applies: the owner TOML amount (at most $5 for
experimental entries), the symbol allowlist, the daily new-entry allowance, KILL, and
the account risk fractions.

`autotrade install` writes a systemd user timer that runs the job each weekday morning
(New York time). It is installed disabled unless `--enable` is given.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .paths import default_config, state_home

NY = ZoneInfo("America/New_York")
UNIT = "tradeagent-autotrade"
DEFAULT_TIME = "10:15"
DECISION_DELAY = 300  # seconds between the frozen decision and the entry
CLOSED = {"COMPLETED", "CLOSED_PARTIAL", "NO_TRADE"}


def research_dir():
    return state_home() / "autotrade"


def log(stage, **values):
    print(json.dumps({"stage": stage, "at": time.time(), **values}, default=str), flush=True)


def _opportunity(state_dir, *args):
    from .opportunity_cli import main as opportunity_main

    return opportunity_main(["--state-dir", str(state_dir), *args])


def finish_previous(settings):
    """Close out an interrupted lifecycle before any new entry; exit-only."""
    from .execution_policy import check_run_state
    from .oneshot import run_live

    if not check_run_state(settings):
        return None
    report_path = settings.directory / "report.json"
    report = json.loads(report_path.read_text()) if report_path.is_file() else {}
    if report.get("status") in CLOSED and not report.get("outstanding_incident"):
        return report.get("status")
    log("RECOVERING_PREVIOUS")
    result = run_live(settings, recover=True)
    log("RECOVERED", status=result.get("status"))
    if result.get("status") not in CLOSED or result.get("outstanding_incident"):
        raise RuntimeError(f"previous run still open ({result.get('status')}); no new entry today")
    return result.get("status")


def run(config, state_dir, *, now=None):
    from .calendar import session_bounds
    from .execution_policy import load_live_config

    now = time.time() if now is None else now
    day = datetime.fromtimestamp(now, NY).date()
    bounds = session_bounds(day)
    if not bounds or not bounds[0] <= now < bounds[1]:
        log("MARKET_CLOSED", day=day.isoformat())
        return 0
    settings = load_live_config(config)
    if (settings.directory / "KILL").exists():
        log("KILL_SWITCH", path=str(settings.directory / "KILL"))
        return 0
    try:
        finish_previous(settings)
    except Exception as error:  # never open a new position on top of an unresolved one
        log("STOPPED", reason=str(error))
        return 2
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    common = ["--config", str(config)]
    # Market captures use the same broker login, and the frozen plan the same hold, as LIVE.
    helper = ["--oauth-helper", str(settings.oauth_helper)]
    hold = [
        "--hold-seconds",
        str(settings.options["hold_seconds"]),
        "--delay-seconds",
        str(DECISION_DELAY),
    ]
    if _opportunity(state_dir, "scan", "--candidates", "3", *helper, *common) != 0:
        log("STOPPED", reason="market scan incomplete")
        return 2
    decide = ["decide", "--refresh", "--policy", "experimental", *helper, *hold, *common]
    if _opportunity(state_dir, *decide) != 0:
        log("STOPPED", reason="decision could not be frozen")
        return 2
    plan = json.loads((state_dir / "latest-plan.json").read_text())
    if not (plan.get("experimental") or {}).get("plan"):
        log("NO_TRADE", decision_id=plan["decision_id"], reason="no eligible signal today")
        return 0
    log("EXECUTING", decision_id=plan["decision_id"])
    return _opportunity(
        state_dir,
        "execute",
        "--decision-id",
        plan["decision_id"],
        "--mode",
        "experimental",
        "--live",
        *common,
    )


def units(python, config, state_dir, at):
    hour, minute = at.split(":")
    service = f"""[Unit]
Description=TradeAgent daily experimental LIVE trade
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={python} -m tradeagent.autotrade run --config {config} --state-dir {state_dir}
TimeoutStartSec=3h
UMask=0077
NoNewPrivileges=true
LimitCORE=0
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
"""
    # No Persistent=: a missed morning must not trade late in the day.
    timer = f"""[Unit]
Description=TradeAgent daily experimental LIVE trade

[Timer]
OnCalendar=Mon..Fri *-*-* {int(hour):02d}:{int(minute):02d}:00 America/New_York
AccuracySec=1s
Unit={UNIT}.service

[Install]
WantedBy=timers.target
"""
    return {f"{UNIT}.service": service, f"{UNIT}.timer": timer}


def install(config, state_dir, at, *, enable, unit_dir=None, systemctl=True):
    from .execution_policy import load_live_config

    settings = load_live_config(config)
    from .model import dec
    from .opportunity_live import EXPERIMENTAL_MAX_DOLLARS

    entry = settings.options.get("entry") or {}
    if "dollar_amount" not in entry or "quantity" in entry:
        raise ValueError("autotrade needs [entry] dollar_amount in the TOML")
    amount = dec(entry["dollar_amount"])
    if not dec("1") <= amount <= EXPERIMENTAL_MAX_DOLLARS:
        raise ValueError(
            f"[entry] dollar_amount must be between $1 and ${EXPERIMENTAL_MAX_DOLLARS}"
        )
    risk = settings.risk
    # Smallest account value at which the engine's sizing limits still admit the amount.
    needs = max(
        amount / dec(risk.max_new_exposure_fraction),
        amount / dec(risk.max_position_fraction),
        amount / dec(risk.max_daily_turnover_fraction),
        amount / (1 - dec(risk.min_cash_fraction)) if dec(risk.min_cash_fraction) < 1 else amount,
    )
    from .research.opportunity_workflow import ALPHA_HORIZONS

    hold = settings.options["hold_seconds"]
    if hold + DECISION_DELAY not in ALPHA_HORIZONS:
        allowed = sorted(h - DECISION_DELAY for h in ALPHA_HORIZONS if h > DECISION_DELAY)
        raise ValueError(f"[exit] hold_seconds must be one of {allowed} for autotrade (now {hold})")
    for value in (str(config), str(state_dir), sys.executable):
        if any(c in value for c in '\n\r%"\\ '):
            raise ValueError(f"unsupported path for a systemd unit: {value}")
    hour, _, minute = at.partition(":")
    if not (hour.isdigit() and minute.isdigit() and 9 <= int(hour) <= 14 and int(minute) < 60):
        raise ValueError("--at must be HH:MM New York time between 09:30 and 14:59")
    if (int(hour), int(minute)) < (9, 35):
        raise ValueError("--at must be at least five minutes after the 09:30 open")
    directory = Path(unit_dir or Path.home() / ".config/systemd/user")
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in units(sys.executable, config, state_dir, at).items():
        (directory / name).write_text(text)
        (directory / name).chmod(0o600)
    summary = {
        "timer": f"{UNIT}.timer",
        "runs": f"weekdays {at} New York time",
        "amount_per_trade": f"${amount}",
        "account_value_needed": f"${needs.quantize(dec('0.01'))}",
        "symbols": list(settings.config.allowed_symbols),
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
    return {"removed": f"{UNIT}.timer"}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="tradeagent autotrade",
        description="One experimental LIVE trade per trading day, on a timer.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "install"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, default=None)
        command.add_argument("--state-dir", type=Path, default=None)
    commands.choices["install"].add_argument(
        "--at", default=DEFAULT_TIME, help="New York time, HH:MM (default 10:15)"
    )
    commands.choices["install"].add_argument(
        "--enable", action="store_true", help="start the timer now (otherwise installed off)"
    )
    commands.add_parser("uninstall")
    args = parser.parse_args(argv)
    if args.command == "uninstall":
        print(json.dumps(uninstall()))
        return 0
    config = Path(args.config or default_config()).expanduser().resolve()
    state_dir = Path(args.state_dir or research_dir()).expanduser().resolve()
    try:
        if args.command == "run":
            return run(config, state_dir)
        print(json.dumps(install(config, state_dir, args.at, enable=args.enable), indent=2))
        return 0
    except Exception as error:
        log("STOPPED", reason=f"{type(error).__name__}: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
