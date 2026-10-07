"""Separate versioned experience DB. Evidence is append-only even for raw SQL."""

import fcntl
import os
import sqlite3
import subprocess
from contextlib import contextmanager, nullcontext
from pathlib import Path

from .domain import canonical

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, description TEXT NOT NULL);
CREATE TABLE strategies(strategy_id TEXT PRIMARY KEY, family TEXT NOT NULL,
 champion_version TEXT, enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)));
CREATE TABLE strategy_versions(
 strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id), version TEXT NOT NULL,
 family TEXT NOT NULL, params TEXT NOT NULL, parent_version TEXT,
 registered_at REAL NOT NULL, generation INTEGER NOT NULL, role TEXT NOT NULL,
 implementation_hash TEXT NOT NULL,
 PRIMARY KEY(strategy_id,version),
 FOREIGN KEY(strategy_id,parent_version) REFERENCES strategy_versions(strategy_id,version));
CREATE TABLE market_snapshots(
 snapshot_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, decision_time REAL NOT NULL,
 captured_at REAL NOT NULL CHECK(captured_at>=decision_time), source TEXT NOT NULL,
 evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('prospective','synthetic','replay')),
 benchmark TEXT NOT NULL, regime TEXT NOT NULL, features TEXT NOT NULL, payload TEXT NOT NULL,
 UNIQUE(symbol,decision_time,source,evidence_kind));
CREATE TABLE predictions(
 prediction_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
 snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id), symbol TEXT NOT NULL,
 decision_time REAL NOT NULL, horizon INTEGER NOT NULL CHECK(horizon>0),
 direction INTEGER NOT NULL CHECK(direction IN (-1,0,1)), expected_return REAL NOT NULL,
 confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1), features TEXT NOT NULL,
 context TEXT NOT NULL, distribution TEXT NOT NULL, created_at REAL NOT NULL,
 model_version TEXT NOT NULL, CHECK(created_at>=decision_time AND created_at<decision_time+horizon),
 FOREIGN KEY(strategy_id,strategy_version) REFERENCES strategy_versions(strategy_id,version),
 UNIQUE(strategy_id,strategy_version,symbol,decision_time,horizon));
CREATE TABLE observations(
 observation_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
 available_at REAL NOT NULL, observed_at REAL NOT NULL, source TEXT NOT NULL,
 open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
 volume REAL NOT NULL, CHECK(start<end AND end<=available_at AND available_at<=observed_at),
 UNIQUE(symbol,start,end,source));
CREATE TABLE option_observations(
 quote_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, asof REAL NOT NULL,
 available_at REAL NOT NULL, observed_at REAL NOT NULL, source TEXT NOT NULL,
 bid REAL NOT NULL, ask REAL NOT NULL, payload TEXT NOT NULL,
 CHECK(asof<=available_at AND available_at<=observed_at), UNIQUE(contract_id,asof,source));
CREATE TABLE outcomes(
 prediction_id TEXT PRIMARY KEY REFERENCES predictions(prediction_id), outcome_time REAL NOT NULL,
 resolved_at REAL NOT NULL, raw_return REAL NOT NULL, benchmark_return REAL,
 residual_return REAL, mfe REAL NOT NULL, mae REAL NOT NULL, realized_volatility REAL NOT NULL,
 source TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE TABLE selections(
 prediction_id TEXT PRIMARY KEY REFERENCES predictions(prediction_id), selected INTEGER NOT NULL,
 rank INTEGER NOT NULL, score REAL NOT NULL, baseline_score REAL NOT NULL,
 baseline_rank INTEGER NOT NULL, baseline_selected INTEGER NOT NULL,
 state_asof REAL, rationale TEXT NOT NULL);
CREATE TABLE trade_plans(
 prediction_id TEXT PRIMARY KEY REFERENCES predictions(prediction_id), kind TEXT NOT NULL,
 instrument TEXT, entry_limit REAL, reason TEXT NOT NULL);
CREATE TABLE shadow_expressions(
 expression_id TEXT PRIMARY KEY, prediction_id TEXT NOT NULL REFERENCES predictions(prediction_id),
 kind TEXT NOT NULL, contract_id TEXT NOT NULL DEFAULT '', entry REAL,
 multiplier INTEGER NOT NULL, status TEXT NOT NULL, quote_snapshot TEXT NOT NULL,
 UNIQUE(prediction_id,kind,contract_id));
CREATE TABLE live_expressions(
 live_id TEXT PRIMARY KEY, prediction_id TEXT NOT NULL REFERENCES predictions(prediction_id),
 execution_key TEXT NOT NULL, observed_at REAL NOT NULL, expected_entry REAL NOT NULL,
 actual_entry REAL, actual_exit REAL, fees REAL, quantity REAL NOT NULL, status TEXT NOT NULL);
CREATE TABLE live_attributions(
 live_id TEXT PRIMARY KEY REFERENCES live_expressions(live_id), classification TEXT NOT NULL,
 slippage_fraction REAL, realized_pnl REAL, observed_at REAL NOT NULL, evidence TEXT NOT NULL);
CREATE TABLE counterfactuals(
 expression_id TEXT PRIMARY KEY REFERENCES shadow_expressions(expression_id),
 prediction_id TEXT NOT NULL REFERENCES predictions(prediction_id), kind TEXT NOT NULL,
 status TEXT NOT NULL, pnl REAL, return_fraction REAL, exit REAL,
 resolved_at REAL NOT NULL, metadata TEXT NOT NULL);
CREATE TABLE attributions(
 prediction_id TEXT PRIMARY KEY REFERENCES outcomes(prediction_id), alpha TEXT NOT NULL,
 calibration TEXT NOT NULL, timing TEXT NOT NULL, regime TEXT NOT NULL,
 expression TEXT NOT NULL, execution TEXT NOT NULL, evidence TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE learning_runs(
 run_id TEXT PRIMARY KEY, asof REAL NOT NULL, evidence_hash TEXT NOT NULL UNIQUE,
 algorithm TEXT NOT NULL, configuration TEXT NOT NULL);
CREATE TABLE strategy_scores(
 run_id TEXT NOT NULL REFERENCES learning_runs(run_id), strategy_id TEXT NOT NULL,
 version TEXT NOT NULL, n INTEGER NOT NULL, effective_n REAL NOT NULL, mean_return REAL NOT NULL,
 uncertainty REAL NOT NULL, weight REAL NOT NULL, calibration REAL NOT NULL,
 degradation REAL NOT NULL, PRIMARY KEY(run_id,strategy_id,version));
CREATE TABLE regime_scores(
 run_id TEXT NOT NULL REFERENCES learning_runs(run_id), strategy_id TEXT NOT NULL,
 version TEXT NOT NULL, regime TEXT NOT NULL, n INTEGER NOT NULL,
 mean_return REAL NOT NULL, compatibility REAL NOT NULL,
 PRIMARY KEY(run_id,strategy_id,version,regime));
CREATE TABLE lessons(
 lesson_id TEXT NOT NULL, revision INTEGER NOT NULL, scope TEXT NOT NULL, condition TEXT NOT NULL,
 observation TEXT NOT NULL, evidence_count INTEGER NOT NULL, supporting_events TEXT NOT NULL,
 contradicting_events TEXT NOT NULL, status TEXT NOT NULL, confidence REAL NOT NULL,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(lesson_id,revision));
CREATE TABLE mutations(
 mutation_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, parent_version TEXT NOT NULL,
 challenger_version TEXT NOT NULL, hypothesis TEXT NOT NULL, lesson_id TEXT,
 created_at REAL NOT NULL, evaluation_plan TEXT NOT NULL, replay_status TEXT NOT NULL,
 UNIQUE(strategy_id,challenger_version));
CREATE TABLE challenger_evaluations(
 mutation_id TEXT NOT NULL REFERENCES mutations(mutation_id), evidence_hash TEXT NOT NULL,
 evaluated_at REAL NOT NULL, n INTEGER NOT NULL, mean_delta REAL NOT NULL,
 lower_bound REAL NOT NULL, upper_bound REAL NOT NULL, decision TEXT NOT NULL,
 metadata TEXT NOT NULL, PRIMARY KEY(mutation_id,evidence_hash));
CREATE TABLE promotions(
 mutation_id TEXT PRIMARY KEY REFERENCES mutations(mutation_id), strategy_id TEXT NOT NULL,
 parent_version TEXT NOT NULL, challenger_version TEXT NOT NULL, created_at REAL NOT NULL,
 evidence_hash TEXT NOT NULL);
CREATE TABLE rejections(
 mutation_id TEXT PRIMARY KEY REFERENCES mutations(mutation_id), created_at REAL NOT NULL,
 evidence_hash TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE retirements(
 strategy_id TEXT NOT NULL, version TEXT NOT NULL, created_at REAL NOT NULL, reason TEXT NOT NULL,
 PRIMARY KEY(strategy_id,version),
 FOREIGN KEY(strategy_id,version) REFERENCES strategy_versions(strategy_id,version));
CREATE INDEX predictions_due ON predictions(decision_time,horizon);
CREATE INDEX observation_lookup ON observations(symbol,end,source);
CREATE INDEX version_predictions ON predictions(strategy_id,strategy_version,decision_time);
CREATE TRIGGER prediction_snapshot_guard BEFORE INSERT ON predictions BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM market_snapshots s JOIN strategy_versions v
 ON v.strategy_id=NEW.strategy_id AND v.version=NEW.strategy_version
 WHERE s.snapshot_id=NEW.snapshot_id AND s.symbol=NEW.symbol AND s.decision_time=NEW.decision_time
 AND v.registered_at<=NEW.decision_time AND s.captured_at<=NEW.created_at
 AND s.features=NEW.features) THEN RAISE(ABORT,'prediction identity/information mismatch') END;
END;
CREATE TRIGGER outcome_time_guard BEFORE INSERT ON outcomes BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM predictions p WHERE p.prediction_id=NEW.prediction_id
 AND NEW.outcome_time=p.decision_time+p.horizon AND NEW.outcome_time>p.created_at
 AND NEW.resolved_at>=NEW.outcome_time) THEN RAISE(ABORT,'outcome must follow prediction') END;
END;
CREATE TRIGGER champion_version_guard BEFORE UPDATE OF champion_version ON strategies BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM strategy_versions v
 WHERE v.strategy_id=NEW.strategy_id AND v.version=NEW.champion_version)
 THEN RAISE(ABORT,'unknown champion version') END;
 SELECT CASE WHEN OLD.champion_version IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM promotions p WHERE p.strategy_id=NEW.strategy_id
 AND p.parent_version=OLD.champion_version AND p.challenger_version=NEW.champion_version)
 THEN RAISE(ABORT,'champion change requires promotion evidence') END;
END;
CREATE TRIGGER strategy_identity_guard BEFORE UPDATE OF strategy_id,family ON strategies BEGIN
 SELECT RAISE(ABORT,'strategy identity is immutable');
END;
CREATE TRIGGER strategy_delete_guard BEFORE DELETE ON strategies BEGIN
 SELECT RAISE(ABORT,'strategy history cannot be deleted');
END;
"""


class Experience:
    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        if os.name != "posix" or str(self.directory).startswith(("/mnt/", "//")):
            raise ValueError("experience SQLite requires persistent local Linux storage")
        ancestor = self.directory
        while not ancestor.exists():
            ancestor = ancestor.parent
        fs = subprocess.check_output(
            ["findmnt", "-n", "-o", "FSTYPE", "--target", str(ancestor)], text=True
        ).strip()
        if fs not in {"ext4", "ext3", "ext2", "btrfs", "xfs"}:
            raise ValueError("unsupported experience filesystem")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "experience.sqlite3"
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA recursive_triggers=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        try:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version > SCHEMA_VERSION or (version == 0 and tables):
                raise ValueError("unknown experience schema; use a new experience directory")
            if version == 0:
                # DDL and version are one transaction; interrupted migrations roll back.
                guards = ""
                for table in (
                    "schema_migrations",
                    "strategy_versions",
                    "market_snapshots",
                    "predictions",
                    "observations",
                    "option_observations",
                    "outcomes",
                    "selections",
                    "trade_plans",
                    "shadow_expressions",
                    "live_expressions",
                    "live_attributions",
                    "counterfactuals",
                    "attributions",
                    "learning_runs",
                    "strategy_scores",
                    "regime_scores",
                    "lessons",
                    "mutations",
                    "challenger_evaluations",
                    "promotions",
                    "rejections",
                    "retirements",
                ):
                    for operation in ("UPDATE", "DELETE"):
                        guards += (
                            f"CREATE TRIGGER immutable_{table}_{operation} BEFORE {operation} "
                            f"ON {table} BEGIN SELECT RAISE(ABORT,'append-only evidence'); END;\n"
                        )
                primary_keys = {
                    "schema_migrations": ("version",),
                    "strategies": ("strategy_id",),
                    "strategy_versions": ("strategy_id", "version"),
                    "market_snapshots": ("snapshot_id",),
                    "predictions": ("prediction_id",),
                    "observations": ("observation_id",),
                    "option_observations": ("quote_id",),
                    "outcomes": ("prediction_id",),
                    "selections": ("prediction_id",),
                    "trade_plans": ("prediction_id",),
                    "shadow_expressions": ("expression_id",),
                    "live_expressions": ("live_id",),
                    "live_attributions": ("live_id",),
                    "counterfactuals": ("expression_id",),
                    "attributions": ("prediction_id",),
                    "learning_runs": ("run_id",),
                    "strategy_scores": ("run_id", "strategy_id", "version"),
                    "regime_scores": ("run_id", "strategy_id", "version", "regime"),
                    "lessons": ("lesson_id", "revision"),
                    "mutations": ("mutation_id",),
                    "challenger_evaluations": ("mutation_id", "evidence_hash"),
                    "promotions": ("mutation_id",),
                    "rejections": ("mutation_id",),
                    "retirements": ("strategy_id", "version"),
                }
                other_unique_keys = {
                    "market_snapshots": ("symbol", "decision_time", "source", "evidence_kind"),
                    "predictions": (
                        "strategy_id",
                        "strategy_version",
                        "symbol",
                        "decision_time",
                        "horizon",
                    ),
                    "observations": ("symbol", "start", "end", "source"),
                    "option_observations": ("contract_id", "asof", "source"),
                    "shadow_expressions": ("prediction_id", "kind", "contract_id"),
                    "learning_runs": ("evidence_hash",),
                    "mutations": ("strategy_id", "challenger_version"),
                }
                for table, keys in primary_keys.items():
                    key_sets = [keys] + (
                        [other_unique_keys[table]] if table in other_unique_keys else []
                    )
                    match = " OR ".join(
                        "(" + " AND ".join(f"{key}=NEW.{key}" for key in key_set) + ")"
                        for key_set in key_sets
                    )
                    guards += (
                        f"CREATE TRIGGER no_replace_{table} BEFORE INSERT ON {table} "
                        f"WHEN EXISTS(SELECT 1 FROM {table} WHERE {match}) "
                        "BEGIN SELECT RAISE(ABORT,'append-only evidence'); END;\n"
                    )
                self.db.executescript(
                    "BEGIN IMMEDIATE;"
                    + SCHEMA
                    + guards
                    + "INSERT INTO schema_migrations VALUES(1,'RSI experience foundation');"
                    + "PRAGMA user_version=1; COMMIT;"
                )
            if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("experience integrity failure")
            if self.db.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("experience foreign-key integrity failure")
            names = {
                r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            required = {
                "schema_migrations",
                "strategies",
                "strategy_versions",
                "predictions",
                "outcomes",
                "market_snapshots",
                "trade_plans",
                "live_attributions",
                "mutations",
            }
            if not required <= names:
                raise ValueError("incomplete experience schema; use a new experience directory")
            self._columns = {
                name: {r[1] for r in self.db.execute(f"PRAGMA table_info({name})")}
                for name in names
            }
            self.path.chmod(0o600)
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    @contextmanager
    def lock(self):
        with (self.directory / "experience.lock").open("a+b") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError("another research operation holds the lock") from exc
            yield

    def insert(self, table, item):
        if table not in self._columns or not set(item) <= self._columns[table]:
            raise ValueError("unknown experience table/columns")
        # Names are schema-checked; values remain parameterized.
        columns = ",".join(item)
        self.db.execute(
            f"INSERT INTO {table}({columns}) VALUES({','.join('?' for _ in item)})",
            tuple(item.values()),
        )

    def inspect(self):
        from .reporting import performance

        tables = (
            "strategies",
            "strategy_versions",
            "predictions",
            "outcomes",
            "attributions",
            "lessons",
            "learning_runs",
            "mutations",
            "promotions",
            "rejections",
            "retirements",
        )
        return {
            "schema_version": SCHEMA_VERSION,
            **performance(self),
            "integrity": self.db.execute("PRAGMA integrity_check").fetchone()[0],
            "counts": {
                t: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables
            },
            "strategies": [dict(r) for r in self.db.execute("SELECT * FROM strategies")],
            "scores": [
                dict(r)
                for r in self.db.execute(
                    "SELECT * FROM strategy_scores WHERE run_id="
                    "(SELECT run_id FROM learning_runs ORDER BY asof DESC LIMIT 1)"
                )
            ],
            "evaluations": [
                dict(r) for r in self.db.execute("SELECT * FROM challenger_evaluations")
            ],
        }

    def register(
        self, strategy_id, version, family, params, registered_at, parent=None, role="champion"
    ):
        if role not in {"champion", "challenger", "control"}:
            raise ValueError("unknown strategy role")
        from ..strategy import implementation_hash, validate_params

        validate_params(params)
        code_hash = implementation_hash()
        with nullcontext() if self.db.in_transaction else self.db:
            existing = self.db.execute(
                "SELECT * FROM strategies WHERE strategy_id=?", (strategy_id,)
            ).fetchone()
            if existing is None:
                if parent:
                    raise ValueError("challenger has no parent")
                self.insert("strategies", {"strategy_id": strategy_id, "family": family})
            elif existing["family"] != family:
                raise ValueError("family cannot change inside a strategy identity")
            old = self.db.execute(
                "SELECT * FROM strategy_versions WHERE strategy_id=? AND version=?",
                (strategy_id, version),
            ).fetchone()
            if old:
                if (
                    old["family"] != family
                    or old["params"] != canonical(params)
                    or old["parent_version"] != parent
                    or old["implementation_hash"] != code_hash
                ):
                    raise ValueError("strategy version cannot be overwritten")
                return
            generation = 0
            if parent:
                row = self.db.execute(
                    "SELECT generation FROM strategy_versions WHERE strategy_id=? AND version=?",
                    (strategy_id, parent),
                ).fetchone()
                if row is None or role != "challenger":
                    raise ValueError("new variant must be a challenger with an existing parent")
                generation = row[0] + 1
            elif existing is not None:
                raise ValueError("new version requires parent and prospective challenger")
            self.insert(
                "strategy_versions",
                {
                    "strategy_id": strategy_id,
                    "version": version,
                    "family": family,
                    "params": canonical(params),
                    "parent_version": parent,
                    "registered_at": registered_at,
                    "generation": generation,
                    "role": role,
                    "implementation_hash": code_hash,
                },
            )
            if existing is None:
                self.db.execute(
                    "UPDATE strategies SET champion_version=? WHERE strategy_id=?",
                    (version, strategy_id),
                )
