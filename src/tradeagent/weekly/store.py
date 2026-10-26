"""Append-only, locked and hash-checked SHADOW journal. No execution imports."""

import fcntl
import json
import os
import sqlite3
import time
from pathlib import Path

from .protocol import CONFIG, CONFIG_HASH, PROTOCOL, canonical, digest, implementation_hash


class Store:
    def __init__(self, directory, *, start_date=None, evidence="prospective", clock=time.time):
        if digest(CONFIG) != CONFIG_HASH:
            raise ValueError("frozen CONFIG mutated")
        self.directory, self.clock = Path(directory).expanduser().resolve(), clock
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (self.directory / "worker.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise ValueError("weekly worker already owns state") from None
        self.db = sqlite3.connect(self.directory / "weekly.sqlite3", timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, kind TEXT NOT NULL, at REAL NOT NULL, payload TEXT NOT NULL, hash TEXT NOT NULL)"
        )
        for operation in ("UPDATE", "DELETE"):
            self.db.execute(
                f"CREATE TRIGGER IF NOT EXISTS immutable_{operation} BEFORE {operation} ON events BEGIN SELECT RAISE(ABORT,'immutable weekly evidence'); END"
            )
        self.db.commit()
        existing = self.get("activation")
        identity = implementation_hash()
        if existing:
            if (
                existing["config_hash"] != CONFIG_HASH
                or existing["implementation_hash"] != identity
                or existing["evidence_kind"] != evidence
            ):
                self.close()
                raise ValueError("weekly protocol/implementation/evidence identity changed")
            self.activation = existing
        else:
            if start_date is None:
                self.close()
                raise ValueError("new weekly state requires explicit start date")
            self.activation = {
                "protocol": PROTOCOL,
                "config": CONFIG,
                "config_hash": CONFIG_HASH,
                "implementation_hash": identity,
                "start_date": str(start_date),
                "evidence_kind": evidence,
                "activated_at": clock(),
            }
            self.add("activation", "activation", self.activation)
        self.evidence = evidence

    def close(self):
        self.db.close()
        self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def get(self, key):
        row = self.db.execute("SELECT payload,hash FROM events WHERE id=?", (key,)).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        if digest(value) != row[1]:
            raise ValueError("weekly journal hash mismatch")
        return value

    def rows(self, kind=None):
        sql, args = "SELECT id,kind,at,payload,hash FROM events", ()
        if kind:
            sql += " WHERE kind=?"
            args = (kind,)
        for key, tag, at, payload, hashed in self.db.execute(sql + " ORDER BY rowid", args):
            value = json.loads(payload)
            if digest(value) != hashed:
                raise ValueError("weekly journal hash mismatch")
            yield {"id": key, "kind": tag, "at": at, **value}

    def add(self, key, kind, value):
        old = self.get(key)
        if old is not None:
            if old != json.loads(canonical(value)):
                raise ValueError("conflicting append-only identity: " + key)
            return False
        previous = self.db.execute("SELECT at FROM events ORDER BY rowid DESC LIMIT 1").fetchone()
        now = self.clock()
        if previous and now < previous[0]:
            raise ValueError("weekly evidence clock regressed; no backdated append")
        with self.db:
            self.db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?)",
                (key, kind, now, canonical(value), digest(value)),
            )
        return True

    def artifact(self, value):
        key = digest(value)
        directory = self.directory / "evidence"
        directory.mkdir(exist_ok=True, mode=0o700)
        path = directory / (key + ".json")
        text = canonical(value)
        if path.exists() and path.read_text() != text:
            raise ValueError("immutable artifact conflict")
        if not path.exists():
            with path.open("x") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
        return key

    def state(self, book, before=None):
        sql = "SELECT payload,hash FROM events WHERE kind='ledger' AND json_extract(payload,'$.book')=?"
        args = [book]
        if before is not None:
            sql += " AND at<?"
            args.append(before)
        row = self.db.execute(sql + " ORDER BY rowid DESC LIMIT 1", args).fetchone()
        if row:
            value = json.loads(row[0])
            if digest(value) != row[1]:
                raise ValueError("weekly ledger hash mismatch")
            return value["after"]
        return {
            "settled": "10000",
            "pending": [],
            "positions": {},
            "costs": "0",
            "turnover": "0",
            "closed_lots": [],
        }
