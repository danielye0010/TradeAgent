"""Provider-independent completed observations, decision cutoffs and durable collection."""

import json
import math
import sqlite3

from ..research.domain import Bar, MarketSnapshot, canonical, finite
from .providers import SYMBOLS

CUTOFF_BLOCKER = "insufficient fresh completed bars/quotes actually received by the decision"


class Collection:
    def __init__(self, directory, provider):
        self.provider = provider
        self.source = provider.source
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

    def history_start(self, bounds, startup):
        """Request the first missing minute, with one completed-minute overlap."""
        start = max(bounds[0], math.ceil(startup / 60) * 60)
        frontiers = []
        for symbol in SYMBOLS:
            frontier = start
            for row in self.db.execute(
                "SELECT start,end FROM bars WHERE symbol=? AND start>=? AND end<=? ORDER BY start",
                (symbol, start, bounds[1]),
            ):
                if row["start"] != frontier:
                    break
                frontier = row["end"]
            frontiers.append(frontier)
        return max(start, min(frontiers) - 60)

    def ingest(self, payload, started, received, bounds, startup):
        if received < started or not bounds:
            raise ValueError("invalid live collection clock/session")
        facts = self.provider.normalize(payload, received, bounds, startup)
        normalized, quotes, refs = facts["bars"], facts["quotes"], facts["references"]
        seen = set()
        for item in normalized:
            bar = Bar.from_dict(item)
            key = (bar.symbol, bar.start)
            if (
                key in seen
                or bar.symbol not in SYMBOLS
                or bar.end - bar.start != 60
                or bar.start % 60
                or bar.start < max(bounds[0], startup)
                or bar.end > bounds[1]
                or bar.available_at != received
            ):
                raise ValueError("invalid completed provider observation")
            seen.add(key)
        for symbol in SYMBOLS:
            own = [b for b in normalized if b["symbol"] == symbol]
            if not own or received - max(b["end"] for b in own) > 120:
                raise ValueError("missing/stale completed provider observations")
        if (
            len(quotes) != len(SYMBOLS)
            or len(refs) != len(SYMBOLS)
            or {q[0] for q in quotes} != set(SYMBOLS)
            or {r[0] for r in refs} != set(SYMBOLS)
        ):
            raise ValueError("missing/duplicate provider quotes or references")
        for symbol, receipt, qt, bid, ask in quotes:
            for value in (receipt, qt, bid, ask):
                finite(value)
            if (
                symbol not in SYMBOLS
                or receipt != received
                or qt > received
                or received - qt > 120
                or not 0 < bid <= ask
            ):
                raise ValueError("stale/invalid provider quote")
        for symbol, receipt, close, close_time in refs:
            for value in (receipt, close, close_time):
                finite(value)
            if (
                symbol not in SYMBOLS
                or receipt != received
                or not close > 0
                or close_time >= bounds[0]
            ):
                raise ValueError("invalid previous daily reference")
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
            self.source,
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
            "source": self.source,
            "bars": [
                json.loads(r[0])
                for r in self.db.execute(
                    "SELECT payload FROM bars WHERE start>=? ORDER BY start,symbol", (start,)
                )
            ],
        }
