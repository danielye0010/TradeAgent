"""`tradeagent autotrade`: a small LIVE mirror of the daily news-reading AI decision.

Each trading day the `daily-news-v2` research service freezes one decision per arm in
its 10:30-10:37 New York window. `autotrade run` waits for the frozen record of the
chosen model (``--model codex``, the default: the `codex_news` arm, the same one the
paper account follows; or ``--model claude``: the `claude_news` arm of the
`daily-news-claude-v1` companion on the same shortlist) and, when it is a long position
in a symbol the owner TOML allows, buys the owner's dollar amount at the decision's own
entry minute and sells after the same one-hour hold. A cash day, a
missing or incomplete decision, or a symbol outside the allowlist is recorded as
NO_TRADE. Nothing is re-decided, re-ranked or substituted.

The existing LIVE engine owns sizing, every risk limit, the order, the timed exit and
reconciliation. On top of it: at most $5 per entry, one new entry per New York day,
exit-only recovery of any interrupted lifecycle first, and the KILL switch.

`autotrade install` writes a systemd user timer for 10:30 New York time each weekday.
"""

import argparse
import json
import os
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
FOLLOWED_PROTOCOL = "daily-news-v2"
FOLLOWED_ARM = "codex_news"
# The model whose news-reading pick is mirrored. Both answer the same frozen question.
MODELS = {
    "codex": {"protocol": FOLLOWED_PROTOCOL, "arm": FOLLOWED_ARM, "label": "Codex (GPT)"},
    "claude": {"protocol": "daily-news-claude-v1", "arm": "claude_news", "label": "Claude"},
}
DEFAULT_MODEL = "codex"
START = (10, 30)  # the followed protocol's decision window opens here (New York)
DECISION_WAIT_SECONDS = 9 * 60  # window is 7 minutes; allow the record to land
POLL_SECONDS = 15
HOLD_SECONDS = 3600  # the followed protocol's holding horizon
CLOSED = {"COMPLETED", "CLOSED_PARTIAL", "NO_TRADE"}


def research_dir():
    return state_home() / "autotrade"


def log(stage, **values):
    print(json.dumps({"stage": stage, "at": time.time(), **values}, default=str), flush=True)


def check_owner_settings(settings):
    """The owner TOML must size in dollars ($1-$5) and hold exactly as the research does."""
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
    if settings.options["hold_seconds"] != HOLD_SECONDS:
        raise ValueError(
            f"[exit] hold_seconds must be {HOLD_SECONDS} to match {FOLLOWED_PROTOCOL} "
            f"(now {settings.options['hold_seconds']})"
        )
    return amount


def find_decision(follow_dir, day, protocol=FOLLOWED_PROTOCOL):
    """The followed protocol's frozen decision for this New York session, if any."""
    found = []
    for path in sorted((Path(follow_dir) / "decisions").glob("*.json")):
        try:
            decision = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if (
            decision.get("research_protocol") == protocol
            and datetime.fromtimestamp(decision["session_open"], NY).date() == day
        ):
            found.append(decision)
    if len(found) > 1:
        raise RuntimeError(f"{len(found)} {protocol} decisions for {day}; refusing to pick")
    return found[0] if found else None


def selection(decision, allowed, name=FOLLOWED_ARM):
    """(symbol, reason): symbol is None for every no-trade outcome."""
    arm = (decision.get("comparisons") or {}).get(name) or {}
    if arm.get("status") != "VALID":
        return None, f"{name} assessment unavailable"
    if arm.get("decision") != "SHADOW_LONG" or not arm.get("symbol"):
        return None, f"{name} chose cash"
    if (
        not (decision.get("research_window") or {}).get("valid")
        or decision.get("outcome_entry_time") is None
    ):
        return None, "decision has no realizable entry/exit window"
    if decision.get("holding_seconds") != HOLD_SECONDS:
        return None, "decision horizon differs from the owner hold"
    if arm["symbol"] not in allowed:
        return None, f"{arm['symbol']} is not in the owner [live] symbols"
    return arm["symbol"], f"{name} selection"


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


def archive_previous(settings):
    """Archive a closed lifecycle with fresh broker proof; refuses open exposure."""
    from .oneshot import new_live_run

    return new_live_run(settings)


def reserve_day(settings, day, decision_id, now):
    """One new-entry attempt per New York day; failed or unknown attempts keep the claim."""
    directory = settings.directory.with_name(settings.directory.name + ".autotrade-days")
    created = not directory.exists()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{day.isoformat()}.json"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise RuntimeError(f"today's autotrade entry was already attempted ({path})") from error
    with os.fdopen(descriptor, "w") as stream:
        json.dump({"decision_id": decision_id, "reserved_at": now}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    # The claim must survive power loss before any order: persist its directory entry
    # (and the directory's own entry the first time it is created).
    for folder in (directory, directory.parent) if created else (directory,):
        descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return path


def mirror_order(decision, symbol, now, model=DEFAULT_MODEL):
    """A fresh owner prediction and plan that point at the frozen research decision."""
    from .research.domain import Payload, Prediction, identity
    from .research.expressions import TradePlan

    followed = MODELS[model]
    protocol, arm = followed["protocol"], followed["arm"]
    source = {
        "protocol": protocol,
        "arm": arm,
        "decision_id": decision["decision_id"],
        "decision_time": decision["decision_time"],
        "outcome_entry_time": decision["outcome_entry_time"],
        "outcome_exit_time": decision["outcome_exit_time"],
    }
    strategy = f"mirror:{protocol}:{arm}"
    version = str(decision.get("skill_version") or "unversioned")
    prediction = Prediction(
        identity([strategy, version, decision["decision_id"], symbol, now]),
        strategy,
        version,
        decision["decision_id"],
        symbol,
        now,
        HOLD_SECONDS,
        1,
        0.0,
        0.0,
        Payload.of(source),
        Payload.of({"evidence_kind": "live_mirror", "source": protocol}),
        Payload.of({"kind": "none", "calibrated_probability": False}),
        now,
    )
    plan = TradePlan(
        prediction.prediction_id,
        "UNDERLYING",
        symbol,
        None,
        f"mirror of frozen {protocol} {arm} selection",
    )
    return plan, prediction


def execute(settings, plan, prediction):
    from .oneshot import run_live

    return run_live(settings, plan=plan, prediction=prediction)


def record(state_dir, day, value):
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state_dir / f"{day.isoformat()}.json"
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")
    return path


def run(config, follow_dir, state_dir, *, model=DEFAULT_MODEL, clock=time.time, sleep=time.sleep):
    from .calendar import session_bounds
    from .execution_policy import load_live_config

    now = clock()
    day = datetime.fromtimestamp(now, NY).date()
    bounds = session_bounds(day)
    if not bounds or not bounds[0] <= now < bounds[1]:
        log("MARKET_CLOSED", day=day.isoformat())
        return 0
    settings = load_live_config(config)
    protocol, arm = MODELS[model]["protocol"], MODELS[model]["arm"]
    base = {"session": day.isoformat(), "followed": f"{protocol}/{arm}", "model": model}
    # Exit-only recovery runs every session, before any no-trade return and under KILL.
    try:
        finish_previous(settings)
    except Exception as error:  # never open a new position on top of an unresolved one
        record(state_dir, day, {**base, "status": "STOPPED", "reason": str(error)})
        log("STOPPED", reason=str(error))
        return 2
    if (settings.directory / "KILL").exists():
        log("KILL_SWITCH", path=str(settings.directory / "KILL"))
        return 0
    check_owner_settings(settings)

    opens = datetime(day.year, day.month, day.day, *START, tzinfo=NY).timestamp()
    decision = find_decision(follow_dir, day, protocol)
    while decision is None and clock() < opens + DECISION_WAIT_SECONDS:
        sleep(POLL_SECONDS)
        decision = find_decision(follow_dir, day, protocol)
    if decision is None:
        reason = f"no {protocol} decision today"
        record(state_dir, day, {**base, "status": "NO_TRADE", "reason": reason})
        log("NO_TRADE", reason=reason)
        return 0

    symbol, reason = selection(decision, set(settings.config.allowed_symbols), arm)
    base.update(decision_id=decision["decision_id"], symbol=symbol, reason=reason)
    if symbol is None:
        record(state_dir, day, {**base, "status": "NO_TRADE"})
        log("NO_TRADE", decision_id=decision["decision_id"], reason=reason)
        return 0

    try:
        archive_previous(settings)
        reserve_day(settings, day, decision["decision_id"], clock())
    except Exception as error:  # never open a new position on top of an unresolved one
        record(state_dir, day, {**base, "status": "STOPPED", "reason": str(error)})
        log("STOPPED", reason=str(error))
        return 2

    entry_at, deadline = decision["outcome_entry_time"], decision["entry_deadline"]
    while clock() < entry_at:
        sleep(min(POLL_SECONDS, max(0.0, entry_at - clock())))
    if clock() >= deadline:
        reason = "entry window passed before the order could start"
        record(state_dir, day, {**base, "status": "EXPIRED", "reason": reason})
        log("EXPIRED", reason=reason)
        return 0
    plan, prediction = mirror_order(decision, symbol, clock(), model)
    log("EXECUTING", decision_id=decision["decision_id"], symbol=symbol)
    try:
        report = execute(settings, plan, prediction)
    except Exception as error:
        record(state_dir, day, {**base, "status": "HALTED", "reason": str(error)})
        log("HALTED", reason=str(error))
        return 2
    from .opportunity_live import execution_result

    outcome = execution_result(report)
    record(
        state_dir,
        day,
        {**base, "status": outcome["status"], "prediction_id": prediction.prediction_id, **outcome},
    )
    log("FINISHED", status=outcome["status"], pnl=outcome["pnl"])
    return 0 if report.get("status") in CLOSED and not report.get("outstanding_incident") else 2


def discover_follow_dir(unit_dir=None, protocol=FOLLOWED_PROTOCOL):
    """State directory of the installed research timer for ``protocol``."""
    from .dashboard.jobs import discover

    for job in discover(unit_dir, systemctl=False):
        if job["kind"] == "daily" and job["protocol"] == protocol:
            return Path(job["state_dir"])
    return None


def units(python, config, follow_dir, state_dir, model=DEFAULT_MODEL):
    protocol, arm = MODELS[model]["protocol"], MODELS[model]["arm"]
    # The default model keeps the original command line, so existing installs read the same.
    option = "" if model == DEFAULT_MODEL else f" --model {model}"
    service = f"""[Unit]
Description=TradeAgent daily LIVE mirror of {protocol} {arm}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={python} -m tradeagent.autotrade run --config {config} --follow-dir {follow_dir} --state-dir {state_dir}{option}
TimeoutStartSec=3h
UMask=0077
NoNewPrivileges=true
LimitCORE=0
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
"""
    # No Persistent=: a missed morning must not trade late in the day.
    timer = f"""[Unit]
Description=TradeAgent daily LIVE mirror of {protocol} {arm}

[Timer]
OnCalendar=Mon..Fri *-*-* {START[0]:02d}:{START[1]:02d}:00 America/New_York
AccuracySec=1s
Unit={UNIT}.service

[Install]
WantedBy=timers.target
"""
    return {f"{UNIT}.service": service, f"{UNIT}.timer": timer}


def install(
    config,
    follow_dir,
    state_dir,
    *,
    enable,
    model=DEFAULT_MODEL,
    unit_dir=None,
    systemctl=True,
):
    from .execution_policy import load_live_config
    from .model import dec
    from .research.opportunities import UNIVERSE

    settings = load_live_config(config)
    amount = check_owner_settings(settings)
    if model not in MODELS:
        raise ValueError(f"unknown model {model!r}; choose one of {sorted(MODELS)}")
    protocol, arm = MODELS[model]["protocol"], MODELS[model]["arm"]
    follow_dir = follow_dir or discover_follow_dir(unit_dir, protocol)
    if follow_dir is None or not Path(follow_dir).is_dir():
        hint = (
            "the --state-dir of the installed daily-news timer"
            if model == "codex"
            else "install the Claude comparison first: tradeagent claude install --enable"
        )
        raise ValueError(f"no {protocol} research directory found; pass --follow-dir DIR ({hint})")
    follow_dir = Path(follow_dir).resolve()
    risk = settings.risk
    # Smallest account value at which the engine's sizing limits still admit the amount.
    needs = max(
        amount / dec(risk.max_new_exposure_fraction),
        amount / dec(risk.max_position_fraction),
        amount / dec(risk.max_daily_turnover_fraction),
        amount / (1 - dec(risk.min_cash_fraction)) if dec(risk.min_cash_fraction) < 1 else amount,
    )
    for value in (str(config), str(follow_dir), str(state_dir), sys.executable):
        if any(c in value for c in '\n\r%"\\ '):
            raise ValueError(f"unsupported path for a systemd unit: {value}")
    directory = Path(unit_dir or Path.home() / ".config/systemd/user")
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in units(sys.executable, config, follow_dir, state_dir, model).items():
        (directory / name).write_text(text)
        (directory / name).chmod(0o600)
    allowed = set(settings.config.allowed_symbols)
    summary = {
        "timer": f"{UNIT}.timer",
        "runs": f"weekdays {START[0]:02d}:{START[1]:02d} New York time",
        "model": MODELS[model]["label"],
        "follows": f"{protocol} {arm} in {follow_dir}",
        "amount_per_trade": f"${amount}",
        "hold_seconds": HOLD_SECONDS,
        "account_value_needed": f"${needs.quantize(dec('0.01'))}",
        "symbols": sorted(allowed),
        "research_symbols_not_allowed": [s for s in UNIVERSE if s not in allowed],
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
        description="Daily small LIVE mirror of a news-reading AI research decision.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "install"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, default=None)
        command.add_argument("--state-dir", type=Path, default=None)
        command.add_argument(
            "--follow-dir",
            type=Path,
            default=None,
            help="research directory of the followed model (install finds the installed one)",
        )
        command.add_argument(
            "--model",
            choices=sorted(MODELS),
            default=DEFAULT_MODEL,
            help="whose pick to mirror: codex (daily-news-v2, default) or claude "
            "(daily-news-claude-v1, same shortlist and window)",
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
    follow_dir = Path(args.follow_dir).expanduser().resolve() if args.follow_dir else None
    try:
        if args.command == "run":
            if follow_dir is None:
                raise ValueError("autotrade run needs --follow-dir")
            return run(config, follow_dir, state_dir, model=args.model)
        summary = install(config, follow_dir, state_dir, enable=args.enable, model=args.model)
        print(json.dumps(summary, indent=2))
        return 0
    except Exception as error:
        log("STOPPED", reason=f"{type(error).__name__}: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
