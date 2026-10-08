"""Local durable journal plus upstream fenced lease and a process-lifetime lock."""

import json
import os
import sqlite3
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .model import Halt, dec, digest
from .risk import TERMINAL
from .vendor import run_lock


def dumps(value):
    return json.dumps(value, sort_keys=True, default=str, allow_nan=False)


class State:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        if os.name != "posix" or str(self.directory).startswith(("/mnt/", "//")):
            raise Halt("SQLite and locks require the WSL Linux filesystem")
        ancestor = self.directory
        while not ancestor.exists():
            ancestor = ancestor.parent
        fs = subprocess.run(
            ["findmnt", "-n", "-o", "FSTYPE", "--target", str(ancestor)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if fs not in {"ext4", "ext3", "ext2", "btrfs", "xfs"}:
            raise Halt("runtime filesystem is not a supported local Linux filesystem")
        os.umask(0o077)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self.path = self.directory / "state.sqlite3"
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS runs(
          id TEXT PRIMARY KEY, started REAL NOT NULL, finished REAL, mode TEXT NOT NULL,
          config_hash TEXT NOT NULL, strategy_version TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, time REAL NOT NULL,
          kind TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS intents(
          key TEXT PRIMARY KEY, run_id TEXT NOT NULL, ref_id TEXT NOT NULL UNIQUE,
          payload TEXT NOT NULL, status TEXT NOT NULL, broker_id TEXT, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS baseline(
          account_key TEXT NOT NULL, day TEXT NOT NULL, nav TEXT NOT NULL,
          PRIMARY KEY(account_key, day));
        """)
        if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise Halt("state integrity failure")

    def close(self):
        self.db.close()

    def export_log(self):
        # SQLite is authoritative. Atomically regenerate a readable log after a crash/run.
        temporary = self.directory / "events.jsonl.tmp"
        with temporary.open("w") as handle:
            for row in self.db.execute("SELECT * FROM events ORDER BY seq"):
                item = dict(row)
                item["payload"] = json.loads(item["payload"])
                handle.write(dumps(item) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.directory / "events.jsonl")
        descriptor = os.open(self.directory, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def event(self, run_id, kind, payload):
        with self.db:
            self.db.execute(
                "INSERT INTO events(run_id,time,kind,payload) VALUES(?,?,?,?)",
                (run_id, time.time(), kind, dumps(payload)),
            )

    def start(self, c, r):
        run_id = str(uuid.uuid4())
        with self.db:
            self.db.execute(
                "INSERT INTO runs VALUES(?,?,NULL,?,?,?,?)",
                (
                    run_id,
                    time.time(),
                    c.mode,
                    digest([asdict(c), asdict(r)]),
                    c.strategy_version,
                    "running",
                ),
            )
        self.event(run_id, "configuration", {"config": asdict(c), "risk": asdict(r)})
        return run_id

    def finish(self, run_id, status):
        with self.db:
            self.db.execute(
                "UPDATE runs SET status=?,finished=? WHERE id=?", (status, time.time(), run_id)
            )

    def baseline(self, s, day):
        if dec(s.nav) <= 0:
            return str(s.nav)  # An unfunded account must not poison a later positive baseline.
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO baseline VALUES(?,?,?)", (s.account_key, day, str(s.nav))
            )
        return self.db.execute(
            "SELECT nav FROM baseline WHERE account_key=? AND day=?", (s.account_key, day)
        ).fetchone()[0]

    def prepare(self, key, run_id, intent):
        ref_id = str(uuid.uuid4())
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO intents VALUES(?,?,?,?,?,NULL,?)",
                (key, run_id, ref_id, dumps(intent.payload()), "prepared", time.time()),
            )
        return ref_id if cursor.rowcount == 1 else None

    def update(self, key, status, broker_id=None):
        row = self.db.execute("SELECT status FROM intents WHERE key=?", (key,)).fetchone()
        transitions = {
            "prepared": {"reviewed", "shadow_recorded", "abandoned", "risk_rejected"},
            "reviewed": {"submitting", "abandoned"},
            "submitting": {"pending", "unknown"} | TERMINAL,
            "unknown": {"pending"} | TERMINAL,
            "pending": {"pending"} | TERMINAL,
        }
        if row is None or status not in transitions.get(row["status"], set()):
            raise Halt("invalid intent lifecycle transition")
        with self.db:
            self.db.execute(
                "UPDATE intents SET status=?,broker_id=COALESCE(?,broker_id),updated=? WHERE key=?",
                (status, broker_id, time.time(), key),
            )

    def recover(self, run_id, snapshot):
        # Never replay submissions. Reconcile an exact broker ID or unique ref_id.
        by_id = {o["id"]: o for o in snapshot.orders}
        if len(by_id) != len(snapshot.orders) or any(not key for key in by_id):
            raise Halt("duplicate/missing broker order identity")
        by_ref = {}
        for order in snapshot.orders:
            if order.get("ref_id"):
                if order["ref_id"] in by_ref:
                    raise Halt("duplicate broker idempotency reference")
                by_ref[order["ref_id"]] = order
        rows = self.db.execute(
            "SELECT * FROM intents WHERE status IN ('submitting','unknown','pending')"
        ).fetchall()
        unresolved = []
        for row in rows:
            by_broker_id = by_id.get(row["broker_id"])
            by_client_ref = by_ref.get(row["ref_id"])
            if (
                (by_broker_id and by_client_ref and by_broker_id["id"] != by_client_ref["id"])
                or (
                    by_broker_id
                    and by_broker_id.get("ref_id")
                    and by_broker_id["ref_id"] != row["ref_id"]
                )
                or (row["broker_id"] and by_client_ref and by_client_ref["id"] != row["broker_id"])
            ):
                raise Halt("broker ID and client reference disagree")
            order = by_broker_id or by_client_ref
            if order is None:
                unresolved.append(row["key"])
                continue
            self.event(run_id, "recovered_order", order)
            payload = json.loads(row["payload"])
            expected_symbol = (
                payload["legs"][0]["option_id"] if "legs" in payload else payload["symbol"]
            )
            expected_side = payload["legs"][0]["side"] if "legs" in payload else payload["side"]
            expected_price = payload.get("limit_price", payload.get("price"))
            from .model import Intent

            if (
                not Intent.from_payload(payload).matches_order(order)
                if "legs" not in payload
                else (
                    order.get("symbol") != expected_symbol
                    or order.get("side") != expected_side
                    or dec(order.get("quantity")) != dec(payload["quantity"])
                    or (
                        order.get("price") is not None
                        and dec(order["price"]) != dec(expected_price)
                    )
                )
            ):
                raise Halt("broker order does not match persisted intent")
            self.update(
                row["key"], order["state"] if order["state"] in TERMINAL else "pending", order["id"]
            )
            if order["state"] not in TERMINAL:
                unresolved.append(row["key"])
        if unresolved:
            raise Halt("ambiguous/pending prior submission: operator reconciliation required")
        for row in self.db.execute(
            "SELECT * FROM intents WHERE status IN (" + ",".join("?" for _ in TERMINAL) + ")",
            tuple(TERMINAL),
        ):
            order = by_id.get(row["broker_id"]) or by_ref.get(row["ref_id"])
            if (
                order
                and order["state"] != row["status"]
                and not ({order["state"], row["status"]} <= {"cancelled", "canceled"})
            ):
                raise Halt("terminal local and broker state diverged")
        for order in snapshot.orders:
            if order["state"] not in TERMINAL:
                raise Halt(
                    "broker order missing locally or divergent: operator reconciliation required"
                )
        with self.db:
            self.db.execute(
                "UPDATE intents SET status='abandoned' WHERE status IN ('prepared','reviewed') AND run_id != ?",
                (run_id,),
            )
            self.db.execute(
                "UPDATE runs SET status='interrupted',finished=? WHERE status='running' AND id != ?",
                (time.time(), run_id),
            )
        self.event(
            run_id,
            "startup_reconciliation",
            {
                "account_key": snapshot.account_key,
                "orders": snapshot.orders,
                "positions": snapshot.positions,
            },
        )

    @contextmanager
    def lock(self, seconds):
        path = self.directory / "process.lock"
        with path.open("a+b") as handle:
            handle.seek(0)
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise Halt("another run holds the process lock") from exc
            lease_path = str(self.directory / "lease.sqlite3")
            lease = run_lock.acquire(lease_path, lease_seconds=seconds)
            if not lease["ok"]:
                raise Halt("another run holds the lease (or a crashed lease has not expired)")

            def fence():
                receipt = run_lock.renew(lease["token"], lease_path, lease_seconds=seconds)
                if not receipt["ok"]:
                    raise Halt("run lease ownership lost")

            try:
                yield fence
            finally:
                run_lock.release(lease["token"], lease_path)
                # The OS releases process locks even after a crash.
