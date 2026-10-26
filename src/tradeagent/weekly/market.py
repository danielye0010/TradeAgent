"""Official quote capability and explicit Yahoo chart history/corporate-action fallback."""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..research.opportunities import capture
from .protocol import NY, UNIVERSE, digest, session_bounds, sessions


class Market:
    def __init__(self, directory, *, clock=time.time):
        self.directory, self.clock = Path(directory), clock
        self.directory.mkdir(parents=True, exist_ok=True)

    def quotes(self, symbols):
        data = capture(list(dict.fromkeys(symbols)), quotes_only=True, deadline=self.clock() + 90)
        return data

    def chart(self, symbol, *, years=10, refresh=False):
        day = datetime.fromtimestamp(self.clock(), NY).date()
        path = self.directory / f"{symbol}-{day}-{years}y.json"
        if path.exists() and not refresh:
            return json.loads(path.read_text())
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            + symbol
            + "?"
            + urlencode({"range": f"{years}y", "interval": "1d", "events": "div,splits"})
        )
        with urlopen(
            Request(url, headers={"User-Agent": "TradeAgent weekly-hold-v1 public research"}),
            timeout=30,
        ) as response:
            data = json.load(response)
        result = data["chart"]["result"][0]
        observed = self.clock()
        q = result["indicators"]["quote"][0]
        adjusted = result["indicators"]["adjclose"][0]["adjclose"]
        bars = []
        for index, at in enumerate(result["timestamp"]):
            date = datetime.fromtimestamp(at, NY).date()
            bounds = session_bounds(date)
            if not bounds or bounds[1] > observed:
                continue
            values = {k: q[k][index] for k in ("open", "high", "low", "close", "volume")}
            if (
                any(values[k] is None or values[k] <= 0 for k in ("open", "high", "low", "close"))
                or adjusted[index] is None
            ):
                continue
            bars.append(
                {"date": str(date), "at": bounds[1], **values, "adjusted_close": adjusted[index]}
            )
        actions = []
        for tag, values in result.get("events", {}).items():
            for value in values.values():
                date = datetime.fromtimestamp(value["date"], NY).date()
                action = {
                    "symbol": symbol,
                    "date": str(date),
                    "source_url": url,
                    "observed_at": observed,
                }
                if tag == "splits":
                    actions.append(
                        {
                            **action,
                            "type": "split",
                            "ratio": value["numerator"] / value["denominator"],
                        }
                    )
                elif tag == "dividends":
                    actions.append(
                        {**action, "type": "dividend", "amount": value["amount"], "pay_date": None}
                    )
        value = {
            "source": "Yahoo public chart",
            "source_url": url,
            "observed_at": observed,
            "symbol": symbol,
            "bars": bars,
            "actions": actions,
            "raw": data,
            "raw_hash": digest(data),
        }
        if path.exists():
            path = self.directory / (symbol + "-" + digest(value) + ".json")
        if not path.exists():
            path.write_text(json.dumps(value, allow_nan=False))
        return value

    def histories(self, symbols=UNIVERSE, *, years=10, refresh=False):
        from concurrent.futures import ThreadPoolExecutor

        def one(symbol):
            try:
                return symbol, self.chart(symbol, years=years, refresh=refresh)
            except Exception as error:
                return symbol, {
                    "symbol": symbol,
                    "bars": [],
                    "actions": [],
                    "missing": type(error).__name__ + ": " + str(error)[:160],
                    "observed_at": self.clock(),
                }

        with ThreadPoolExecutor(max_workers=4) as executor:
            return dict(executor.map(one, symbols))


def quality(history):
    bars = history.get("bars", [])
    if not bars:
        return {"usable": False, "reason": history.get("missing", "empty daily history")}
    dates = [b["date"] for b in bars]
    expected = {str(x) for x in sessions(dates[0], dates[-1])}
    missing = sorted(expected - set(dates))
    invalid = [
        b["date"]
        for b in bars
        if not (
            0 < b["low"] <= min(b["open"], b["close"]) <= max(b["open"], b["close"]) <= b["high"]
        )
        or b["adjusted_close"] <= 0
        or b["volume"] < 0
    ]
    return {
        "usable": not missing and not invalid and len(set(dates)) == len(dates),
        "first": dates[0],
        "last": dates[-1],
        "bars": len(bars),
        "missing_sessions": missing,
        "invalid": invalid,
        "adjustment": "vendor adjusted close total-return proxy; retrospective",
        "splits": sum(a["type"] == "split" for a in history.get("actions", [])),
        "source_hash": history.get("raw_hash"),
    }


def momentum(history, previous_day):
    bars = {b["date"]: b for b in history.get("bars", []) if b["date"] <= str(previous_day)}
    start = previous_day - timedelta(days=430)
    ordered = [str(x) for x in sessions(start, previous_day)]
    required = ordered[-253:]
    if len(required) != 253 or any(day not in bars for day in required):
        return None
    return bars[required[-22]]["adjusted_close"] / bars[required[-253]]["adjusted_close"] - 1
