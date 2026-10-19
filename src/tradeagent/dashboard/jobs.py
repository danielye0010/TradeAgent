"""Scheduled TradeAgent jobs: find the installed systemd units and read their state.

Only ``systemctl --user show`` is called, which reads state and changes nothing.
"""

import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

UNIT_DIR = Path.home() / ".config/systemd/user"
MODULES = {
    "tradeagent.prospective.daily": "daily",
    "tradeagent.prospective.service": "prospective",
    "tradeagent.prospective.continuous": "continuous",
    "tradeagent.prospective.events": "events",
    "tradeagent.autotrade": "autotrade",
    "tradeagent.prospective.claude_compare": "daily",
}
# Modules that run one fixed protocol and take no --protocol option.
MODULE_PROTOCOL = {"tradeagent.prospective.claude_compare": "daily-news-claude-v1"}
DEFAULT_PROTOCOL = {
    "daily": "daily-four-arm-v1",
    "continuous": "continuous-quant-v5",
    "events": "event-driven-v6",
    "prospective": "prospective-shadow",
    "autotrade": "live-mirror",
}
_TIMER = re.compile(r"^OnCalendar=(.+)$", re.M)
_cache = {}


def _arguments(text):
    match = re.search(r"^ExecStart=(.+)$", text, re.M)
    if not match:
        return None
    try:
        words = shlex.split(match.group(1))
    except ValueError:
        return None
    module = words[words.index("-m") + 1] if "-m" in words[:-1] else None

    def option(name):
        return words[words.index(name) + 1] if name in words[:-1] else None

    return module, option("--state-dir"), option("--protocol"), option("--model")


def parse_calendar(line):
    """'Mon..Fri *-*-* 10:30..59:00 America/New_York' -> (weekdays_only, [(start, end)])."""
    parts = line.split()
    weekdays = parts[0].startswith("Mon..Fri")
    clock = next((p for p in parts if re.match(r"^\d{1,2}:", p)), None)
    if clock is None:
        return None
    match = re.match(r"^(\d{1,2}):(\d{2})(?:\.\.(\d{2}))?:\d{2}$", clock)
    if not match:
        return None
    hour, start = int(match.group(1)), int(match.group(2))
    end = int(match.group(3)) if match.group(3) else start
    return {"weekdays": weekdays, "start": hour * 60 + start, "end": hour * 60 + end}


def _show(unit, properties):
    try:
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                unit,
                "--timestamp=unix",
                *[f"--property={p}" for p in properties],
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    values = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


def _unix(value):
    if value and value.startswith("@"):
        try:
            return float(value[1:])
        except ValueError:
            return None
    return None


def discover(unit_dir=None, *, systemctl=True):
    """Installed TradeAgent units with their state directory, protocol and timer state."""
    unit_dir = Path(unit_dir or UNIT_DIR)
    if not unit_dir.is_dir():
        return []
    available = systemctl and shutil.which("systemctl") is not None
    jobs = []
    for service in sorted(unit_dir.glob("tradeagent-*.service")):
        try:
            parsed = _arguments(service.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not parsed or parsed[0] not in MODULES:
            continue
        module, state_dir, protocol, model = parsed
        kind = MODULES[module]
        timer_file = service.with_suffix(".timer")
        schedule = []
        if timer_file.is_file():
            try:
                for line in _TIMER.findall(timer_file.read_text(encoding="utf-8")):
                    item = parse_calendar(line)
                    if item:
                        schedule.append(item)
            except OSError:
                pass
        job = {
            "unit": service.stem,
            "kind": kind,
            "protocol": protocol or MODULE_PROTOCOL.get(module) or DEFAULT_PROTOCOL[kind],
            "state_dir": state_dir,
            # Which AI an autotrade timer mirrors; units without --model follow Codex.
            "model": (model or "codex") if kind == "autotrade" else None,
            "schedule": schedule,
            "timer": None,
            "service": None,
        }
        if available:
            if timer_file.is_file():
                t = _show(
                    timer_file.name,
                    ["ActiveState", "UnitFileState", "NextElapseUSecRealtime", "LastTriggerUSec"],
                )
                job["timer"] = {
                    "enabled": t.get("UnitFileState") == "enabled",
                    "active": t.get("ActiveState") == "active",
                    "next_run": _unix(t.get("NextElapseUSecRealtime")),
                    "last_run": _unix(t.get("LastTriggerUSec")),
                }
            s = _show(
                service.name,
                ["ActiveState", "SubState", "Result", "ExecMainExitTimestamp", "ExecMainStatus"],
            )
            if s:
                job["service"] = {
                    "running": s.get("ActiveState") in {"active", "activating"}
                    and s.get("SubState") in {"running", "start", "start-pre"},
                    "result": s.get("Result"),
                    "exit_status": s.get("ExecMainStatus"),
                    "finished": _unix(s.get("ExecMainExitTimestamp")),
                }
        jobs.append(job)
    return jobs


def cached_discover(max_age=10, unit_dir=None, **kwargs):
    now = time.monotonic()
    key = str(unit_dir or UNIT_DIR)
    hit = _cache.get(key)
    if hit is None or now - hit[0] > max_age:
        hit = (now, discover(unit_dir, **kwargs))
        _cache[key] = hit
    return hit[1]
