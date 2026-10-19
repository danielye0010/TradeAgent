"""`tradeagent doctor`: one offline checklist of what is set up and what to do next.

Nothing here contacts Robinhood or places orders. `live-check` is the online,
read-only follow-up once everything below passes.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .paths import default_config, default_helper, default_live_state

SUPPORTED_FS = {"ext4", "ext3", "ext2", "btrfs", "xfs"}
OK, WARN, FAIL = "ok", "warn", "fail"


def _fs_type(path):
    path = Path(path).expanduser().resolve()
    while not path.exists():
        path = path.parent
    try:
        return subprocess.run(
            ["findmnt", "-n", "-o", "FSTYPE", "--target", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _is_wsl():
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def check_system():
    if os.name != "posix" or sys.platform != "linux":
        return FAIL, f"{platform.system()} detected", "Run TradeAgent on Linux or in WSL2."
    if sys.version_info < (3, 12):
        return FAIL, f"Python {platform.python_version()}", "Install Python 3.12 or newer."
    where = "WSL2" if _is_wsl() else "Linux"
    return OK, f"{where}, Python {platform.python_version()}", None


def check_storage(directory=None):
    directory = Path(directory or default_live_state())
    if str(directory.expanduser().resolve()).startswith(("/mnt/", "//")):
        return FAIL, f"{directory} is on a Windows mount", "Keep state under your Linux home."
    fs = _fs_type(directory)
    if fs is None:
        return WARN, "could not determine the filesystem (findmnt missing?)", None
    if fs not in SUPPORTED_FS:
        return FAIL, f"{directory} is on {fs}", "Use a local ext4, btrfs or xfs directory."
    return OK, f"state directory on {fs}", None


def check_login():
    from .robinhood_login import HELPER_MARKER, store_path

    store, helper = store_path(), default_helper()
    if not helper.exists():
        return FAIL, "not signed in to Robinhood", "Run `tradeagent login`."
    info = helper.stat()
    if info.st_mode & 0o077 or not os.access(helper, os.X_OK):
        return FAIL, f"{helper} must be owner-only and executable", f"chmod 700 {helper}"
    if HELPER_MARKER not in helper.read_text(errors="replace"):
        return OK, "using your own token helper", None
    if not store.exists():
        return FAIL, "helper present but no saved session", "Run `tradeagent login`."
    if store.stat().st_mode & 0o077:
        return FAIL, f"{store} is readable by others", f"chmod 600 {store}"
    return OK, "signed in (session saved, owner-only)", None


def check_config(path=None):
    from .execution_policy import load_live_config
    from .model import Halt

    path = Path(path or default_config())
    if not path.exists():
        return FAIL, "no live settings file", "Run `tradeagent login` to create one."
    try:
        settings = load_live_config(path)
    except Halt as error:
        return FAIL, f"{path}: {error}", "Fix the setting named above."
    entry = settings.options.get("entry") or {}
    amount = entry.get("dollar_amount")
    symbols = ", ".join(settings.config.allowed_symbols)
    detail = f"symbols {symbols}; " + (f"${amount} per entry" if amount else "custom sizing")
    return OK, detail + f"; cap ${settings.options['max_notional']}", None


def check_codex():
    if shutil.which("codex") is None:
        return (
            WARN,
            "Codex CLI not found (needed for the analyst and autopilot)",
            "Install it from https://github.com/openai/codex and run `codex login`.",
        )
    return OK, "Codex CLI installed (sign in with `codex login` if you have not)", None


def check_claude():
    if shutil.which("claude") is None:
        return (
            OK,
            "Claude Code not installed (optional: Claude next to Codex in the daily research)",
            "Install Claude Code, run `claude` to sign in with your Claude subscription, "
            "then `tradeagent claude doctor`.",
        )
    return OK, "Claude Code installed; check the login with `tradeagent claude doctor`", None


def check_systemd():
    if shutil.which("systemctl") is None:
        return WARN, "systemd not available (needed only for the daily autopilot)", None
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is None or result.returncode != 0:
        hint = "Enable systemd in WSL: https://learn.microsoft.com/windows/wsl/systemd"
        return WARN, "user systemd not running (needed only for the daily autopilot)", hint
    return OK, "user systemd available for the daily autopilot", None


CHECKS = [
    ("System", check_system, True),
    ("Storage", check_storage, True),
    ("Robinhood sign-in", check_login, True),
    ("Live settings", check_config, True),
    ("Codex CLI", check_codex, False),
    ("Claude Code", check_claude, False),
    ("Autopilot timer", check_systemd, False),
]


def run():
    results = []
    for name, check, required in CHECKS:
        try:
            status, detail, fix = check()
        except Exception as error:  # a broken check must not hide the others
            status, detail, fix = FAIL, f"check crashed: {error.__class__.__name__}", None
        results.append(
            {"check": name, "status": status, "detail": detail, "fix": fix, "required": required}
        )
    return results


def render(results):
    marks = {OK: "✓", WARN: "!", FAIL: "✗"}
    lines = []
    for row in results:
        lines.append(f"{marks[row['status']]} {row['check']:18} {row['detail']}")
        if row["fix"] and row["status"] != OK:
            lines.append(f"  {'':18} → {row['fix']}")
    if any(r["status"] == FAIL and r["required"] for r in results):
        lines.append("\nFix the ✗ items above, then run `tradeagent doctor` again.")
    else:
        lines.append("\nReady. Next: `tradeagent live-check` (read-only; contacts Robinhood).")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="tradeagent doctor", description="Offline setup checklist; never contacts the broker."
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    results = run()
    print(json.dumps(results, indent=2) if args.json else render(results))
    return 1 if any(r["status"] == FAIL and r["required"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
