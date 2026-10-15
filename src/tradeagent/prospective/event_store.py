"""Independent append-only v6 journal and short named process locks."""

import fcntl
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from ..research.domain import canonical, identity
from ..research.store import Experience

PROTOCOL = "event-driven-v6"
CONFIG = {
    "protocol": PROTOCOL,
    "mode": "SHADOW",
    "hypothesis": "sec-event-price-v1",
    "issuer_scope": "21 operating companies from measured 31-symbol universe; no ETFs",
    "poll": "weekdays 06:02..21:47 ET, every 15 minutes",
    "sec_minimum_request_seconds": 0.75,
    "daily_codex_budget": 3,
    "symbol_cooldown_seconds": 1800,
    "codex_timeout_seconds": 180,
    "entry": "first minute :45 strictly after availability; skip minute%5=0 for v5; 09:46 ET through close-75s",
    "quote_window_seconds": 60,
    "quote_max_age_seconds": 15,
    "exit": "1/3/5 trading sessions AFTER actual entry session; last 60 seconds before close",
    "long_rule": "own reaction>0 and relative reaction>0; uncalibrated 0.5*relative exceeds spread+10bp slippage+10bp buffer",
    "costs": {"slippage_per_side": 0.0005, "fees_fraction": 0.0},
    "bearish": "directional diagnostic only; no short/Put/leverage",
    "corporate_actions": "daily provider adjusted-vs-raw previous-close checks required across holding; gaps/actions flagged; price return excludes cash distributions",
    "primary": "AI-minus-Delayed net, matched eligible event/entry cohort, entry-session clusters",
}
VERSION = identity(CONFIG)


class Store:
    def __init__(self, directory, clock=time.time):
        self.directory, self.clock = Path(directory), clock
        path = self.directory / "experience.sqlite3"
        if path.exists():
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as check:
                if not check.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='event_records'"
                ).fetchone():
                    raise ValueError("v6 requires independent state")
        self.experience = Experience(self.directory)
        self.db = self.experience.db
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS event_records "
            "(kind TEXT, key TEXT, at REAL, payload TEXT, PRIMARY KEY(kind,key))"
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS event_record_time ON event_records(kind,at)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS sec_cache "
            "(url TEXT PRIMARY KEY, etag TEXT, modified TEXT, body_hash TEXT, received REAL)"
        )
        for operation in ("UPDATE", "DELETE"):
            self.db.execute(
                f"CREATE TRIGGER IF NOT EXISTS event_records_{operation} "
                f"BEFORE {operation} ON event_records BEGIN "
                "SELECT RAISE(ABORT,'immutable event evidence'); END"
            )
        self.db.commit()
        old = self.get("config", PROTOCOL)
        if old and old["version"] != VERSION:
            self.close()
            raise ValueError("frozen v6 configuration differs; use independent state")
        self.add(
            "config",
            PROTOCOL,
            {"protocol": PROTOCOL, "config": CONFIG, "version": VERSION, "activated_at": clock()},
        )

    @property
    def activated_at(self):
        return self.get("config", PROTOCOL)["activated_at"]

    def close(self):
        self.experience.close()

    def add(self, kind, key, value):
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO event_records VALUES(?,?,?,?)",
                (kind, str(key), self.clock(), canonical(value)),
            )
        return cursor.rowcount == 1

    def get(self, kind, key):
        row = self.db.execute(
            "SELECT payload FROM event_records WHERE kind=? AND key=?", (kind, str(key))
        ).fetchone()
        return json.loads(row[0]) if row else None

    def rows(self, kind):
        return [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT payload FROM event_records WHERE kind=? ORDER BY at,key", (kind,)
            )
        ]

    def blob(self, body, suffix=".bin"):
        import hashlib

        digest = hashlib.sha256(body).hexdigest()
        directory = self.directory / "evidence"
        directory.mkdir(exist_ok=True)
        path = directory / (digest + suffix)
        if not path.exists():
            with path.open("xb") as handle:
                handle.write(body)
        return digest

    @contextmanager
    def lock(self, name):
        with (self.directory / (name + ".lock")).open("a+b") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError("v6 worker already running") from exc
            yield
