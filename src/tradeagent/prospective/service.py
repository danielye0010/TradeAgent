"""Durable zero-money shadow collection; strict decision cutoff, no broker boundary."""

import argparse
import fcntl
import json
import os
import signal
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..calendar import session_bounds
from ..model import Halt
from ..research.domain import canonical, iso
from ..research.lab import scan, seed
from ..research.learning import learn_daily
from ..research.outcomes import resolve
from ..research.store import Experience
from .collector import CUTOFF_BLOCKER, Collection
from .providers import ROBINHOOD_SOURCE, SYMBOLS, open_provider, provider_adapter, provider_source

NY = ZoneInfo("America/New_York")
FIRST_SESSION = date(2026, 10, 8)
CONFIG = {
    "symbols": list(SYMBOLS[:2]),
    "benchmark": "SPY",
    "source": ROBINHOOD_SOURCE,
    "market_data_provider": "robinhood",
    "broker_provider": "robinhood",
    "bar_seconds": 60,
    "decision": "09:33 America/New_York",
    "capture_window_seconds": 30,
    "horizon_seconds": 3600,
    "evidence_kind": "prospective",
    "options": False,
    "first_eligible_session": FIRST_SESSION.isoformat(),
    "learner_initialization": "cold",
}


def configuration(provider="robinhood"):
    result = {**CONFIG, "source": provider_source(provider), "market_data_provider": provider}
    if provider == "alpaca":
        result["feed"] = "sip"
    return result


def decision_for(day):
    if day < FIRST_SESSION or session_bounds(day) is None:
        return None
    return datetime(day.year, day.month, day.day, 9, 33, tzinfo=NY).timestamp()


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


def prospective_only(store, source=ROBINHOOD_SOURCE):
    if store.db.execute(
        "SELECT 1 FROM market_snapshots WHERE evidence_kind!='prospective' LIMIT 1"
    ).fetchone():
        raise ValueError("non-prospective evidence in shadow state; refusing to run")
    if store.db.execute("SELECT 1 FROM live_expressions LIMIT 1").fetchone():
        raise ValueError("broker evidence is forbidden in shadow state")
    if store.db.execute("SELECT 1 FROM observations WHERE source!=? LIMIT 1", (source,)).fetchone():
        raise ValueError("non-prospective source observations are forbidden")
    if store.db.execute(
        "SELECT 1 FROM market_snapshots WHERE source!=? LIMIT 1", (source,)
    ).fetchone():
        raise ValueError("different provider snapshot source in shadow state")
    for row in store.db.execute("SELECT configuration FROM learning_runs"):
        if json.loads(row[0]).get("evidence_kind") != "prospective":
            raise ValueError("non-prospective learner state is forbidden")
    for row in store.db.execute("SELECT evaluation_plan FROM mutations"):
        if json.loads(row[0]).get("evidence_kind") != "prospective":
            raise ValueError("non-prospective challenger state is forbidden")


class Shadow:
    def __init__(self, directory, now, provider="robinhood"):
        self.configuration = configuration(provider)
        self.source = self.configuration["source"]
        self.directory = directory.resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Acquire the same lifetime lock as one-shot research commands before state writes.
        self._owner = (self.directory / "experience.lock").open("a+b")
        try:
            fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._open(now)
        except BaseException:
            if hasattr(self, "collection"):
                self.collection.db.close()
            if hasattr(self, "store"):
                self.store.close()
            self._owner.close()
            raise

    def _open(self, now):
        marker = self.directory / "deployment.json"
        if marker.exists():
            self.deployment = json.loads(marker.read_text())
            if self.deployment["configuration"] != self.configuration:
                raise ValueError("deployment configuration changed; refusing state reuse")
        else:
            if (self.directory / "experience.sqlite3").exists():
                raise ValueError("unmarked existing DB cannot initialize a cold shadow deployment")
            self.deployment = {"configuration": self.configuration, "started_at": now}
            atomic(marker, canonical(self.deployment))
        self.store = Experience(self.directory)
        prospective_only(self.store, self.source)
        self.collection = Collection(
            self.directory, provider_adapter(self.configuration["market_data_provider"])
        )
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
                if now - target > CONFIG["capture_window_seconds"]:
                    reason = (
                        "capture window passed; no late or backfilled scan permitted; "
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
                        with self.store.db:
                            self.store.db.execute("BEGIN IMMEDIATE")
                            for snapshot in snapshots:
                                scan(self.store, snapshot, now)
                        self.collection.step(label, "scan", now, "completed")
                    except ValueError:
                        # Data arriving after the fixed decision cannot repair this snapshot.
                        self.collection.step(label, "scan", now, "skipped", CUTOFF_BLOCKER)
            day += timedelta(days=1)

    def outcomes(self, now):
        earliest = self.store.db.execute(
            "SELECT MIN(p.decision_time) FROM predictions p "
            "LEFT JOIN outcomes o USING(prediction_id) "
            "WHERE o.prediction_id IS NULL AND p.decision_time+p.horizon<=?",
            (now,),
        ).fetchone()[0]
        if earliest is None:
            return
        # Resolve only pending forecasts; do not reimport the entire growing history per tick.
        resolve(self.store, self.collection.dataset(start=earliest), now)
        if self.store.db.execute("SELECT 1 FROM outcomes LIMIT 1").fetchone():
            # Research runs after durable outcomes. Failure does not undo the core path.
            label = (
                datetime.fromtimestamp(now, NY).date().isoformat()
                + ":"
                + str(self.store.db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0])
            )
            if not self.collection.done(label, "learn"):
                try:
                    result = learn_daily(self.store, now, kind="prospective")
                    if result["status"] != "unchanged":
                        self.collection.step(label, "learn", now, result["status"])
                except (ValueError, RuntimeError, ArithmeticError, OSError):
                    self.store.db.rollback()
                    self.collection.step(label, "learn", now, "failed", "optional learner failure")
            # Challenger creation/evaluation remains an explicit research command.
            # The unattended collector never promotes or changes champion pointers.

    def tick(self, now):
        prospective_only(self.store, self.source)
        if now < self.last_tick:
            raise ValueError("wall clock moved backwards; shadow halted")
        self.decisions(now)
        self.outcomes(now)
        self.last_tick = now

    def status(self, now, credential_audit=None, service="running", block=None):
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
            "mode": "SHADOW",
            "status": "SHADOW",
            "health": "blocked" if block else "degraded" if failures else "ok",
            "blocks": [block] if block else [],
            "latest_failure": failures[0]["reason"] if failures else None,
            "information_cutoff": "scheduled decision; actual receipt must be at or before cutoff",
            "configuration": self.configuration,
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
            data["status"] + "; health: " + data["health"],
            *data["blocks"],
            "",
            f"Service: {service}; PID {os.getpid()}; heartbeat {iso(now)}.",
            f"Next eligible decision target: {data['next_decision_new_york']} ({data['next_decision']}).",
            "Decisions use prior receipts only; capture is permitted for 30 seconds after target.",
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

    def report(self, now, service="running"):
        try:
            return self.status(now, service=service)
        except OSError:
            self.collection.failure(now, "optional status report failure")
            return None

    def close(self):
        self.collection.db.close()
        self.store.close()
        self._owner.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--market-data-provider", choices=("robinhood", "alpaca"), default="robinhood"
    )
    parser.add_argument("--oauth-helper", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    shadow = Shadow(args.state_dir, time.time(), args.market_data_provider)
    running = True

    def stop(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    report_at = 0
    try:
        shadow.initialize()
        try:
            with open_provider(
                args.market_data_provider, Path(__file__).resolve().parents[3], args.oauth_helper
            ) as source:
                while running:
                    now = time.time()
                    bounds = session_bounds(datetime.fromtimestamp(now, NY).date())
                    if bounds and bounds[0] <= now <= bounds[1] + 120:
                        try:
                            payload, started, received = source.fetch(
                                bounds,
                                shadow.collection.history_start(
                                    bounds, shadow.deployment["started_at"]
                                ),
                            )
                            shadow.collection.ingest(
                                payload,
                                started,
                                received,
                                bounds,
                                shadow.collection.history_start(
                                    bounds, shadow.deployment["started_at"]
                                ),
                            )
                        except (ValueError, Halt, OSError):
                            shadow.collection.failure(
                                time.time(), "market-data unavailable or invalid"
                            )
                    shadow.tick(time.time())
                    if now - report_at >= 30:
                        shadow.report(time.time())
                        report_at = now
                    time.sleep(2 if bounds and bounds[0] <= now <= bounds[1] + 120 else 10)
        except (ValueError, Halt, OSError):
            shadow.status(
                time.time(),
                service="blocked",
                block=f"{args.market_data_provider} market-data authentication or contract unavailable",
            )
            raise
        shadow.report(time.time(), service="stopped")
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
