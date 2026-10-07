"""One service credential, encrypted by systemd; no plaintext persistent copy."""

import getpass
import os
import stat
import subprocess
import sys
import warnings
from pathlib import Path


def private_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("service credential must be a private regular file owned by this user")


def load():
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory:
        raise ValueError("start through the installed systemd shadow service")
    path = Path(directory) / "alpaca"
    private_file(path)
    parts = path.read_bytes().splitlines()
    if len(parts) != 2 or not all(parts):
        raise ValueError("invalid service credential; repeat hidden operator setup")
    return tuple(p.decode("ascii") for p in parts)


def setup():
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError("credential setup requires an interactive terminal with hidden input")
    path = Path.home() / ".local/share/tradeagent/prospective/alpaca.cred"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.exists():
        private_file(path)
        raise ValueError("encrypted service credential already exists; no automatic overwrite")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("Alpaca API Key ID (hidden): ")
            secret = getpass.getpass("Alpaca API Secret (hidden): ")
        if not key or not secret or any(c in key + secret for c in "\r\n\x00"):
            raise ValueError("empty/invalid hidden input")
        payload = (key + "\n" + secret + "\n").encode("ascii")
        result = subprocess.run(
            ["systemd-creds", "encrypt", "--user", "--name=alpaca", "-", str(path)],
            input=payload,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode:
            path.unlink(missing_ok=True)
            raise ValueError("systemd credential encryption failed")
        path.chmod(0o600)
        private_file(path)
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt, UnicodeError, OSError):
        raise ValueError("hidden credential setup canceled or unavailable") from None
    print("Encrypted service credential installed outside the repository. No values emitted.")


def audit(repo, state, values, extra_paths=()):
    """Compare in memory; persist booleans only, never values or their fingerprints."""
    result = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, check=True)
    paths = [repo / os.fsdecode(p) for p in result.stdout.split(b"\x00") if p]
    paths += [p for p in state.rglob("*") if p.is_file()]
    paths += list(extra_paths)
    paths += [Path.home() / ".config/systemd/user/tradeagent-prospective.service"]
    needles = [value.encode("ascii") for value in values]
    clean = all(not any(n in p.read_bytes() for n in needles) for p in paths if p.exists())
    args_clean = not any(n in Path("/proc/self/cmdline").read_bytes() for n in needles)
    journal = subprocess.run(
        ["journalctl", "--user", "-u", "tradeagent-prospective.service", "--no-pager", "-o", "cat"],
        capture_output=True,
        check=True,
    )
    journal_clean = not any(n in journal.stdout + journal.stderr for n in needles)
    return {
        "repository_and_reports_clean": clean,
        "service_arguments_clean": args_clean,
        "service_journal_clean": journal_clean,
    }


if __name__ == "__main__":
    try:
        setup()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
