"""Generate deterministic minute input for implementation validation, never alpha evidence."""

import argparse
import json
import math
from dataclasses import asdict
from pathlib import Path

from tradeagent.research.domain import Bar, iso
from tradeagent.research.replay import session_window


def bundle(sessions=6, as_of="2026-01-12"):
    _, calendar = session_window(as_of, sessions)
    symbols = ["QQQ", "IWM", "SPY"]
    bars = {}
    for day, session in enumerate(calendar):
        for offset, symbol in enumerate(symbols):
            base = 100 + offset * 80 + day * 0.2
            previous = session["previous_close"]
            bars[(symbol, previous - 60)] = asdict(
                Bar(symbol, previous - 60, previous, previous, base, base, base, base, 10000)
            )
            price = base * (1.003 if symbol != "SPY" else 1.0001)
            for minute in range(62):
                begin = session["open"] + minute * 60
                move = (
                    0.00005
                    if symbol == "SPY"
                    else (0.0012 if minute < 2 else math.sin(day + offset) * 0.00015)
                )
                close = price * (1 + move)
                bars[(symbol, begin)] = asdict(
                    Bar(
                        symbol,
                        begin,
                        begin + 60,
                        begin + 60,
                        price,
                        max(price, close) * 1.0001,
                        min(price, close) * 0.9999,
                        close,
                        10000,
                    )
                )
                price = close
    return {
        "metadata": {
            "source": "fixture-commissioning-minute-v1",
            "frequency": "1Min",
            "adjustment": "raw",
            "symbols": symbols,
            "retrieval_start": iso(calendar[0]["previous_close"] - 60),
            "retrieval_end": iso(calendar[-1]["close"]),
            "availability": "synthetic_fixture",
            "retrieved_at": "2026-01-12T00:00:00Z",
        },
        "bars": list(bars.values()),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle(), indent=2) + "\n")
