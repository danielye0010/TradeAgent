"""Daily-price baseline replay; isolated historical evidence, never an AI backtest."""

import json
import math
import random
from datetime import date
from pathlib import Path

from . import ledger
from .market import Market, momentum, quality
from .protocol import (
    CONFIG,
    PROTOCOL,
    UNIVERSE,
    dec,
    digest,
    first_session,
    instant,
    quantity,
    sessions,
)
from .store import Store


def nominal(history):
    """Yahoo historical OHLC is split-adjusted; restore nominal prices before lot splits."""
    result = json.loads(json.dumps(history))
    splits = [a for a in result["actions"] if a["type"] == "split"]
    for bar in result["bars"]:
        factor = math.prod(a["ratio"] for a in splits if a["date"] > bar["date"])
        for name in ("open", "high", "low", "close"):
            bar[name] *= factor
    for action in result["actions"]:
        if action["type"] == "dividend":
            factor = math.prod(a["ratio"] for a in splits if a["date"] > action["date"])
            action["amount"] *= factor
    return result


def run(output, *, histories=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "weekly.sqlite3").exists():
        raise ValueError(
            "backtest output already contains a frozen run; use a new output directory"
        )
    original = histories or Market(output / "source-cache").histories([*UNIVERSE, "SPY"], years=10)
    checks = {s: quality(h) for s, h in original.items()}
    (output / "data-quality.json").write_text(json.dumps(checks, indent=2))
    usable = {s: h for s, h in original.items() if checks[s]["usable"] and len(h["bars"]) >= 253}
    if set([*UNIVERSE, "SPY"]) - set(usable):
        missing = sorted(set([*UNIVERSE, "SPY"]) - set(usable))
        raise ValueError(
            "historical quality gate failed; preserve source data, missing/invalid: "
            + ",".join(missing)
        )
    # Common complete interval, with a full 252-session lookback for every frozen name.
    start = max(h["bars"][252]["date"] for h in usable.values())
    end = min(h["bars"][-1]["date"] for h in usable.values())
    days = list(sessions(start, end))
    days = [d for d in days if d >= first_session(d)]
    while days and days[0] != first_session(days[0]):
        days.pop(0)
    if len(days) < 252:
        raise ValueError("no common multi-year baseline interval after quality gate")
    data = {s: nominal(h) for s, h in usable.items()}
    bars = {s: {b["date"]: b for b in h["bars"]} for s, h in data.items()}
    now = [instant(days[0], "09:00")]
    arm_names = ("quant", "random", "spy")
    manifest = {
        "protocol": PROTOCOL,
        "evidence_kind": "historical",
        "start": str(days[0]),
        "end": str(days[-1]),
        "sessions": len(days),
        "source_hashes": {s: h.get("raw_hash") for s, h in usable.items()},
        "execution_proxy": "weekly daily open, 5bp half-spread plus 5bp slippage each side; not a noon quote replay",
        "risk_proxy": "prior close threshold -> next open; not a 15:45 quote replay",
        "survivorship_bias": "current frozen equity universe; excludes delisted and prior constituents",
        "dividends": "known ex-date entitlements; payable dates pending; no reinvestment",
        "adjustment": "retrospective Yahoo adjusted-close signal; nominal OHLC reconstructed for lot splits",
        "ai_backtest": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    with Store(
        output, start_date=str(days[0]), evidence="historical", clock=lambda: now[0]
    ) as store:
        store.artifact(original)
        previous = None
        stopped = set()
        for day in days:
            today = str(day)
            now[0] = instant(day, "09:00")
            for history in data.values():
                for action in history["actions"]:
                    if action["date"] == today:
                        for arm in arm_names:
                            stable = {
                                k: v
                                for k, v in action.items()
                                if k not in {"source_url", "observed_at"}
                            }
                            ledger.corporate_action(store, "main:" + arm, stable)
            quotes = {
                s: {
                    "bid": bars[s][today]["open"] * (1 - float(CONFIG["historical_half_spread"])),
                    "ask": bars[s][today]["open"] * (1 + float(CONFIG["historical_half_spread"])),
                }
                for s in data
            }
            now[0] = instant(day, "12:00")
            if previous:
                for arm in arm_names[:-1]:
                    book = "main:" + arm
                    state = store.state(book)
                    for symbol in list(state["positions"]):
                        close = dec(bars[symbol][str(previous)]["close"])
                        if close <= ledger.wac(state, symbol) * dec("0.90"):
                            if ledger.sell(
                                store,
                                f"risk:{today}:{arm}:{symbol}",
                                book,
                                symbol,
                                ledger.shares(state, symbol),
                                quotes[symbol],
                                day,
                                at=now[0],
                                reason="historical-prior-close-proxy",
                            ):
                                stopped.add((arm, symbol))
            if day == first_session(day):
                stopped.clear()
                seed = digest(PROTOCOL + today)
                ranks = sorted(
                    (
                        (
                            s,
                            momentum(
                                h,
                                previous
                                or date.fromisoformat(
                                    h["bars"][
                                        next(
                                            i for i, b in enumerate(h["bars"]) if b["date"] == today
                                        )
                                        - 1
                                    ]["date"]
                                ),
                            ),
                        )
                        for s, h in usable.items()
                        if s in UNIVERSE
                    ),
                    key=lambda x: (-(x[1] if x[1] is not None else -math.inf), x[0]),
                )
                picks = {
                    "quant": [s for s, score in ranks[:3] if score is not None],
                    "random": random.Random(int(seed, 16)).sample(list(UNIVERSE), 3),
                    "spy": ["SPY"],
                }
                store.add(
                    "decision:" + today,
                    "historical_decision",
                    {"date": today, "picks": picks, "seed": seed},
                )
                for arm in arm_names:
                    if arm == "spy" and day != days[0]:
                        continue
                    book = "main:" + arm
                    state = ledger.mature(store.state(book), day)
                    prices = {s: bars[s][today]["open"] for s in data}
                    value = ledger.nav(state, prices)
                    targets = {s: value / (1 if arm == "spy" else 3) for s in picks[arm]}
                    for symbol in sorted(set(state["positions"]) | set(targets)):
                        difference = targets.get(symbol, dec(0)) - ledger.shares(
                            state, symbol
                        ) * dec(prices[symbol])
                        if (
                            symbol in targets
                            and ledger.shares(state, symbol)
                            and abs(difference) <= value * dec(".02")
                        ):
                            continue
                        if difference < 0:
                            ledger.sell(
                                store,
                                f"fill:{today}:{arm}:{symbol}:sell",
                                book,
                                symbol,
                                quantity(-difference / dec(prices[symbol])),
                                quotes[symbol],
                                day,
                                at=now[0],
                                reason="historical-weekly",
                            )
                    for symbol in picks[arm]:
                        state = store.state(book)
                        difference = targets[symbol] - ledger.shares(state, symbol) * dec(
                            prices[symbol]
                        )
                        if difference <= 0 or (
                            ledger.shares(state, symbol) and difference <= value * dec(".02")
                        ):
                            continue
                        qty = quantity(difference / (dec(quotes[symbol]["ask"]) * dec("1.0005")))
                        ledger.buy(
                            store,
                            f"fill:{today}:{arm}:{symbol}:buy",
                            book,
                            symbol,
                            qty,
                            quotes[symbol],
                            day,
                            at=now[0],
                        )
            now[0] = instant(day, "17:15")
            for arm in arm_names:
                book = "main:" + arm
                state = store.state(book)
                value = ledger.nav(state, {s: bars[s][today]["close"] for s in data})
                store.add(
                    f"valuation:{today}:{arm}",
                    "historical_valuation",
                    {
                        "arm": arm,
                        "date": today,
                        "nav": str(value),
                        "costs": state["costs"],
                        "cash_fraction": float(ledger.total_cash(state) / value),
                    },
                )
            previous = day
        summary = {}
        rows = list(store.rows("historical_valuation"))
        for arm in arm_names:
            marks = [r for r in rows if r["arm"] == arm]
            peak, dd = 10000, 0
            for r in marks:
                v = float(r["nav"])
                peak = max(peak, v)
                dd = min(dd, v / peak - 1)
            summary[arm] = {
                "final_nav": float(marks[-1]["nav"]),
                "total_return": float(marks[-1]["nav"]) / 10000 - 1,
                "drawdown": dd,
                "costs": float(marks[-1]["costs"]),
                "daily_observations": len(marks),
            }
        result = {**manifest, "status": "COMPLETED", "results": summary}
        (output / "summary.json").write_text(json.dumps(result, indent=2))
        (output / "RESULTS.md").write_text(
            "# Historical baseline replay (separate from prospective)\n\n"
            + json.dumps(result, indent=2)
            + "\n"
        )
    return result
