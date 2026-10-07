"""Forward-only Alpaca SIP snapshots with actual local receipt times."""

import json
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..calendar import session_bounds
from ..research.domain import Bar, MarketSnapshot, canonical, timestamp

SYMBOLS = ("QQQ", "IWM", "SPY")
SOURCE = "alpaca-sip-prospective-minute-v2"
URL = "https://data.alpaca.markets/v2/stocks/snapshots?symbols=QQQ%2CIWM%2CSPY&feed=sip"
CUTOFF_BLOCKER = "insufficient fresh completed bars/quotes actually received by the decision"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Alpaca:
    def __init__(self, values):
        self._key, self._secret = values
        self._opener = build_opener(NoRedirect())

    def fetch(self):
        started = time.time()
        request = Request(
            URL,
            headers={"APCA-API-KEY-ID": self._key, "APCA-API-SECRET-KEY": self._secret},
        )
        try:
            with self._opener.open(request, timeout=8) as response:
                payload = json.load(response)
            return payload, started, time.time()
        except HTTPError as error:
            raise ValueError(f"Alpaca market-data HTTP {error.code}") from None
        except (OSError, ValueError):
            raise ValueError("Alpaca market-data transport/JSON failure") from None


class Collection:
    def __init__(self, directory):
        self.db = sqlite3.connect(directory / "collection.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS receipts(
                id INTEGER PRIMARY KEY, requested_at REAL, received_at REAL,
                payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bars(
                symbol TEXT, start REAL, end REAL, available_at REAL, payload TEXT,
                PRIMARY KEY(symbol,start));
            CREATE TABLE IF NOT EXISTS quotes(
                symbol TEXT, received_at REAL, quote_time REAL, bid REAL, ask REAL,
                PRIMARY KEY(symbol,received_at));
            CREATE TABLE IF NOT EXISTS refs(
                symbol TEXT, received_at REAL, previous_close REAL, previous_close_time REAL,
                PRIMARY KEY(symbol,received_at));
            CREATE TABLE IF NOT EXISTS steps(
                session TEXT, step TEXT, recorded_at REAL, status TEXT, reason TEXT,
                PRIMARY KEY(session,step));
            CREATE TABLE IF NOT EXISTS failures(
                id INTEGER PRIMARY KEY, recorded_at REAL, reason TEXT);
        """)
        # Source facts/receipts and scheduler decisions cannot be edited on restart.
        for table in ("receipts", "bars", "quotes", "refs", "steps", "failures"):
            for operation in ("UPDATE", "DELETE"):
                self.db.execute(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation} "
                    f"BEFORE {operation} ON {table} BEGIN "
                    "SELECT RAISE(ABORT,'immutable forward collection'); END"
                )
        self.db.commit()

    def ingest(self, payload, started, received, bounds, startup):
        if received < started or not bounds:
            raise ValueError("invalid live collection clock/session")
        normalized, quotes, refs = [], [], []
        try:
            for symbol in SYMBOLS:
                item = payload[symbol]
                raw, quote, previous = item["minuteBar"], item["latestQuote"], item["prevDailyBar"]
                start = timestamp(raw["t"])
                bar = Bar(
                    symbol,
                    start,
                    start + 60,
                    received,
                    *[float(raw[k]) for k in ("o", "h", "l", "c", "v")],
                )
                # Deployment-day health never imports a bar from before startup.
                if start < max(bounds[0], startup) or bar.end > bounds[1]:
                    raise ValueError("minute bar is outside forward collection interval")
                qt, bid, ask = timestamp(quote["t"]), float(quote["bp"]), float(quote["ap"])
                if not 0 < bid <= ask or qt > received or received - qt > 120:
                    raise ValueError("stale/invalid live quote")
                if received - bar.end > 120:
                    raise ValueError("stale live minute bar")
                close = float(previous["c"])
                previous_start = timestamp(previous["t"])
                previous_bounds = session_bounds(
                    datetime.fromtimestamp(previous_start, timezone.utc).date()
                )
                if not previous_bounds:
                    raise ValueError("invalid previous daily session")
                close_time = previous_bounds[1]
                if not close > 0 or close_time >= bounds[0]:
                    raise ValueError("invalid previous daily reference")
                normalized.append(asdict(bar))
                quotes.append((symbol, received, qt, bid, ask))
                refs.append((symbol, received, close, close_time))
        except (KeyError, TypeError, OverflowError, ValueError):
            raise ValueError("missing/stale/invalid forward SIP snapshot") from None
        # Whitelist normalized numeric facts; never persist vendor error bodies/headers.
        facts = {"source": SOURCE, "bars": normalized, "quotes": quotes, "references": refs}
        with self.db:
            self.db.execute(
                "INSERT INTO receipts(requested_at,received_at,payload) VALUES(?,?,?)",
                (started, received, canonical(facts)),
            )
            for bar in normalized:
                self.db.execute(
                    "INSERT OR IGNORE INTO bars VALUES(?,?,?,?,?)",
                    (bar["symbol"], bar["start"], bar["end"], received, canonical(bar)),
                )
            self.db.executemany("INSERT OR IGNORE INTO quotes VALUES(?,?,?,?,?)", quotes)
            self.db.executemany("INSERT OR IGNORE INTO refs VALUES(?,?,?,?)", refs)

    def snapshot(self, symbol, decision, bounds):
        bars = self.db.execute(
            "SELECT payload FROM bars WHERE symbol=? AND start>=? AND end<=? "
            "AND available_at<=? ORDER BY start",
            (symbol, bounds[0], decision, decision),
        ).fetchall()
        benchmark = self.db.execute(
            "SELECT payload FROM bars WHERE symbol='SPY' AND start>=? AND end<=? "
            "AND available_at<=? ORDER BY start",
            (bounds[0], decision, decision),
        ).fetchall()
        quote = self.db.execute(
            "SELECT * FROM quotes WHERE symbol=? AND received_at<=? ORDER BY received_at DESC LIMIT 1",
            (symbol, decision),
        ).fetchone()
        ref = self.db.execute(
            "SELECT * FROM refs WHERE symbol=? AND received_at<=? ORDER BY received_at DESC LIMIT 1",
            (symbol, decision),
        ).fetchone()
        if len(bars) < 2 or len(benchmark) < 2 or not quote or not ref:
            raise ValueError(CUTOFF_BLOCKER)
        own = tuple(Bar.from_dict(json.loads(b[0])) for b in bars)
        bench = tuple(Bar.from_dict(json.loads(b[0])) for b in benchmark)
        if (
            own[0].start != bounds[0]
            or bench[0].start != bounds[0]
            or own[-1].end != bench[-1].end
            or any(
                a.end != b.start
                for history in (own, bench)
                for a, b in zip(history, history[1:], strict=False)
            )
        ):
            raise ValueError("missing or unaligned opening minute history")
        return MarketSnapshot(
            symbol,
            decision,
            SOURCE,
            "prospective",
            "SPY",
            own,
            bench,
            quote["bid"],
            quote["ask"],
            quote["quote_time"],
            quote["received_at"],
            own[0].open,
            ref["previous_close"],
            (),
            bounds[0],
            ref["previous_close_time"],
        )

    def step(self, session, step, now, status, reason=""):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO steps VALUES(?,?,?,?,?)",
                (session, step, now, status, reason),
            )

    def done(self, session, step):
        return (
            self.db.execute(
                "SELECT 1 FROM steps WHERE session=? AND step=?", (session, step)
            ).fetchone()
            is not None
        )

    def failure(self, now, reason):
        with self.db:
            self.db.execute("INSERT INTO failures(recorded_at,reason) VALUES(?,?)", (now, reason))

    def dataset(self, start=0):
        return {
            "source": SOURCE,
            "bars": [
                json.loads(r[0])
                for r in self.db.execute(
                    "SELECT payload FROM bars WHERE start>=? ORDER BY start,symbol", (start,)
                )
            ],
        }
