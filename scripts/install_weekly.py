"""Install only weekly-hold-v1 SHADOW units after validation; no existing units changed."""

import argparse
import json
import sys
from pathlib import Path

from tradeagent.weekly.cli import default_state
from tradeagent.weekly.deployment import install


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--state-dir", type=Path, default=default_state())
    parser.add_argument("--protected", type=Path)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.enable and args.prepare_only:
        parser.error("choose --enable or --prepare-only")
    if not (args.state_dir / "weekly.sqlite3").is_file():
        parser.error("initialize the independent weekly state with the validated wheel first")
    result = install(
        args.state_dir, python=args.python, enable=args.enable, protected=args.protected
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
