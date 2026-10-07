"""Durable zero-money shadow collection; strict decision cutoff, no broker boundary."""

import argparse
import json
import os
import signal
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..calendar import session_bounds
from ..research.domain import canonical, iso
from ..research.evolution import evolve_weekly
from ..research.lab import scan, seed
from ..research.learning import learn_daily
from ..research.outcomes import resolve
from ..research.store import Experience
from . import access
from .collector import CUTOFF_BLOCKER, SOURCE, SYMBOLS, Alpaca, Collection

NY = ZoneInfo("America/New_York")
FIRST_SESSION = date(2026, 10, 8)
CONFIG = {
    "symbols": list(SYMBOLS[:2]),
    "benchmark": "SPY",
    "source": SOURCE,
    "feed": "sip",
    "bar_seconds": 60,
    "decision": "09:32 America/New_York",
    "horizon_seconds": 3600,
    "evidence_kind": "prospective",
    "options": False,
    "first_eligible_session": FIRST_SESSION.isoformat(),
    "learner_initialization": "cold",
}


def decision_for(day):
    if day < FIRST_SESSION or session_bounds(day) is None:
        return None
    return datetime(day.year, day.month, day.day, 9, 32, tzinfo=NY).timestamp()


def next_decision(now):
    day = max(datetime.fromtimestamp(now, NY).date(), FIRST_SESSION)
    for offset in range(370):
        target = decision_for(day + timedelta(days=offset))
        if target is not None and target > now:
            return target
    raise ValueError("no eligible exchange session in calendar range")


def atomic(path, content):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def prospective_only(store):
    if store.db.execute(
        "SELECT 1 FROM market_snapshots WHERE evidence_kind!='prospective' LIMIT 1"
    ).fetchone():
        raise ValueError("non-prospective evidence in shadow state; refusing to run")
    if store.db.execute("SELECT 1 FROM live_expressions LIMIT 1").fetchone():
        raise ValueError("broker evidence is forbidden in shadow state")
    if store.db.execute("SELECT 1 FROM observations WHERE source!=? LIMIT 1", (SOURCE,)).fetchone():
        raise ValueError("non-prospective source observations are forbidden")
    for row in store.db.execute("SELECT configuration FROM learning_runs"):
        if json.loads(row[0]).get("evidence_kind") != "prospective":
            raise ValueError("non-prospective learner state is forbidden")
    for row in store.db.execute("SELECT evaluation_plan FROM mutations"):
        if json.loads(row[0]).get("evidence_kind") != "prospective":
            raise ValueError("non-prospective challenger state is forbidden")


class Shadow:
    def __init__(self, directory, now):
        self.directory = directory.resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        marker = self.directory / "deployment.json"
        if marker.exists():
            self.deployment = json.loads(marker.read_text())
            if self.deployment["configuration"] != CONFIG:
                raise ValueError("deployment configuration changed; refusing state reuse")
        else:
            if (self.directory / "experience.sqlite3").exists():
                raise ValueError("unmarked existing DB cannot initialize a cold shadow deployment")
            self.deployment = {"configuration": CONFIG, "started_at": now}
            atomic(marker, canonical(self.deployment))
        self.store = Experience(self.directory)
        prospective_only(self.store)
        self.collection = Collection(self.directory)
        self.last_tick = max(
            self.deployment["started_at"],
            json.loads((self.directory / "status.json").read_text())["heartbeat"]
            if (self.directory / "status.json").exists()
            else now,
        )

    def initialize(self):
        # Existing seed defaults only. No other state, source DB or initialization path.
        seed(self.store, self.deployment["started_at"])

    def decisions(self, now):
        day = max(datetime.fromtimestamp(self.last_tick, NY).date(), FIRST_SESSION)
        today = datetime.fromtimestamp(now, NY).date()
        while day <= today:
            target = decision_for(day)
            label = day.isoformat()
            if target is not None and target <= now and not self.collection.done(label, "scan"):
                if now != target:
                    reason = (
                        "decision cutoff passed; no late or backfilled scan permitted; "
                        + CUTOFF_BLOCKER
                    )
                    self.collection.step(label, "scan", now, "skipped", reason)
                else:
                    try:
                        snapshots = [
                            self.collection.snapshot(s, target, session_bounds(day))
                            for s in SYMBOLS[:2]
                        ]
                        # Validate both before creating any prediction. Core scan is immutable.
                        for snapshot in snapshots:
                            scan(self.store, snapshot, now)
                        self.collection.step(label, "scan", now, "completed")
                    except ValueError:
                        self.collection.step(label, "scan", now, "skipped", CUTOFF_BLOCKER)
            day += timedelta(days=1)

    def outcomes(self, now):
        if not self.store.db.execute(
            "SELECT 1 FROM predictions WHERE decision_time+horizon<=? LIMIT 1", (now,)
        ).fetchone():
            return
        # Only first-observed, forward-collected bars enter the exact-horizon resolver.
        resolve(self.store, self.collection.dataset(), now)
        if self.store.db.execute("SELECT 1 FROM outcomes LIMIT 1").fetchone():
            result = learn_daily(self.store, now, kind="prospective")
            label = datetime.fromtimestamp(now, NY).date().isoformat()
            self.collection.step(label, "learn", now, result["status"])
            # Existing UTC ISO-week identity and challenger rules are unchanged.
            week = datetime.fromtimestamp(now, ZoneInfo("UTC")).strftime("%G-W%V")
            if not self.collection.done(week, "evolve"):
                evolve_weekly(self.store, now, kind="prospective")
                self.collection.step(week, "evolve", now, "completed")

    def tick(self, now):
        prospective_only(self.store)
        if now < self.last_tick:
            raise ValueError("wall clock moved backwards; shadow halted")
        self.decisions(now)
        self.outcomes(now)
        self.last_tick = now

    def status(self, now, credential_audit=None, service="running"):
        db = self.store.db

        def count(table):
            return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        def last(table, field):
            return db.execute(f"SELECT MAX({field}) FROM {table}").fetchone()[0]

        scores = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM strategy_scores WHERE run_id=(SELECT run_id FROM learning_runs "
                "ORDER BY asof DESC LIMIT 1) ORDER BY strategy_id,version"
            )
        ]
        failures = [
            dict(r)
            for r in self.collection.db.execute(
                "SELECT recorded_at,reason FROM failures ORDER BY id DESC LIMIT 10"
            )
        ]
        last_collection = self.collection.db.execute(
            "SELECT MAX(received_at) FROM receipts"
        ).fetchone()[0]
        data = {
            "status": "NOT ARMED — " + CUTOFF_BLOCKER,
            "configuration": CONFIG,
            "service": service,
            "pid": os.getpid(),
            "heartbeat": now,
            "next_decision": iso(next_decision(now)),
            "next_decision_new_york": datetime.fromtimestamp(next_decision(now), NY).isoformat(),
            "deployment_started_at": self.deployment["started_at"],
            "last_successful_collection": last_collection,
            "last_prediction": last("predictions", "created_at"),
            "last_resolution": last("outcomes", "resolved_at"),
            "last_learner_update": last("learning_runs", "asof"),
            "predictions": count("predictions"),
            "resolved": count("outcomes"),
            "unresolved": count("predictions") - count("outcomes"),
            "weights": scores,
            "cold_default_weight": 1.0,
            "challengers": count("mutations"),
            "promotions": count("promotions"),
            "source_failures_total": self.collection.db.execute(
                "SELECT COUNT(*) FROM failures"
            ).fetchone()[0],
            "recent_source_failures": failures,
            "steps": [
                dict(r)
                for r in self.collection.db.execute(
                    "SELECT * FROM steps ORDER BY recorded_at DESC LIMIT 20"
                )
            ],
            "broker_counts": {"reads": 0, "reviews": 0, "placements": 0, "cancellations": 0},
            "integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_keys": len(db.execute("PRAGMA foreign_key_check").fetchall()),
            "collection_integrity": self.collection.db.execute("PRAGMA integrity_check").fetchone()[
                0
            ],
            "credential_audit": credential_audit,
        }
        atomic(self.directory / "status.json", canonical(data))
        lines = [
            "# Prospective shadow status",
            "",
            data["status"],
            "",
            f"Service: {service}; PID {os.getpid()}; heartbeat {iso(now)}.",
            f"Next eligible decision target: {data['next_decision_new_york']} ({data['next_decision']}).",
            "This is a target, not an armed prediction promise. October 7 collection is health only.",
            f"Last successful collection: {iso(last_collection) if last_collection else 'none'}.",
            f"Predictions/resolved/unresolved: {data['predictions']}/{data['resolved']}/{data['unresolved']}.",
            f"Last prediction/resolution/learner: {data['last_prediction']}/{data['last_resolution']}/{data['last_learner_update']}.",
            f"Learned weights: {len(scores)}; cold default 1.0; challengers/promotions: {data['challengers']}/{data['promotions']}.",
            f"Source failures: {data['source_failures_total']}; latest: {failures[0]['reason'] if failures else 'none'}.",
            "Broker reads/reviews/placements/cancellations: 0/0/0/0.",
            "Credential values and fingerprints are excluded. Details: status.json and collection.sqlite3.",
        ]
        atomic(self.directory / "STATUS.md", "\n".join(lines) + "\n")
        return data

    def close(self):
        self.collection.db.close()
        self.store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    values = access.load()
    source = Alpaca(values)
    shadow = Shadow(args.state_dir, time.time())
    running = True

    def stop(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    audit_at, checked = 0, None
    try:
        with shadow.store.lock():
            shadow.initialize()
            while running:
                now = time.time()
                bounds = session_bounds(datetime.fromtimestamp(now, NY).date())
                # Pre-decision collection begins at the open; two complete minutes required.
                if bounds and bounds[0] <= now <= bounds[1] + 120:
                    try:
                        payload, started, received = source.fetch()
                        shadow.collection.ingest(
                            payload, started, received, bounds, shadow.deployment["started_at"]
                        )
                    except ValueError as error:
                        # Only our fixed, credential-free messages cross this boundary.
                        shadow.collection.failure(time.time(), str(error))
                shadow.tick(time.time())
                if now - audit_at >= 300:
                    checked = access.audit(Path.cwd(), shadow.directory, values)
                    if not all(checked.values()):
                        raise ValueError("credential isolation check failed; shadow stopped")
                    audit_at = now
                shadow.status(time.time(), checked)
                time.sleep(2 if bounds and bounds[0] <= now <= bounds[1] + 120 else 10)
            shadow.status(time.time(), checked, service="stopped")
    finally:
        shadow.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never include exceptions/tracebacks from credential-bearing network objects.
        raise SystemExit(
            "shadow service stopped; inspect sanitized status and service health"
        ) from None
