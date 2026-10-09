"""Local owner one-shot policy; broker authentication and permissions remain external."""

import hashlib
import json
import os
import sqlite3
import stat
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields, replace
from importlib.resources import files
from pathlib import Path

from .model import Config, Halt, Risk, dec, digest
from .risk import check_order
from .schema import Contracts
from .state import dumps
from .supervised import SupervisedLifecycle

VERSION = "local-owner-one-shot-v2"
STATE_SCHEMA_VERSION = 1
EXECUTION_PROTOCOL_VERSION = 2
COMPATIBLE_EXECUTION_PROTOCOLS = {1, 2}


def package_hash():
    root = Path(str(files("tradeagent")))
    return digest(
        {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".json"}
        }
    )


def live_config(directory, symbols):
    return Config(
        mode="LIVE",
        live_enabled=True,
        strategy_version=VERSION,
        allowed_symbols=symbols,
        state_dir=str(Path(directory).resolve() / "agent"),
    )


def owner_file(path):
    path = Path(path)
    info = path.lstat()
    if (
        os.name != "posix"
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise Halt("owner configuration/state files require owner-only regular local files")
    return path


@dataclass(frozen=True)
class LocalSettings:
    path: Path
    config: Config
    risk: Risk
    options: dict
    oauth_helper: Path
    account_sha256: str | None
    timeout: int
    file_hash: str
    bound: bool = False

    @property
    def directory(self):
        return Path(self.config.state_dir).parent

    @property
    def receipt(self):
        return self.path.with_name(self.path.name + ".run.json")

    def validate(self):
        current = load_live_config(self.path)
        if current.file_hash != self.file_hash:
            raise Halt(
                "owner LIVE configuration changed during execution; active parameters remain journaled, use recover"
            )
        if not self.bound and self != current:
            raise Halt("in-memory owner policy differs from TOML configuration")
        self.config.validate()
        self.risk.validate()


def load_live_config(path):
    import tomllib

    path = Path(path).resolve()
    if os.name != "posix" or str(path).startswith(("/mnt/", "//")):
        raise Halt("owner configuration requires local Linux storage")
    raw = owner_file(path).read_bytes()
    try:
        document = tomllib.loads(raw.decode())
    except (ValueError, UnicodeError) as exc:
        raise Halt("invalid owner TOML configuration") from exc
    allowed = {
        "live": {"enabled", "symbols", "state_dir", "max_notional", "max_order_value"},
        "broker": {"oauth_helper", "account_sha256", "account_number", "timeout_seconds"},
        "risk": {
            f.name
            for f in fields(Risk)
            if not f.name.startswith("max_option")
            and not f.name.startswith("min_option")
            and f.name not in {"version", "max_total_option_premium_fraction"}
        },
        "exit": {
            "hold_seconds",
            "polls",
            "order_type",
            "session_buffer_seconds",
            "poll_interval_seconds",
            "max_exit_attempts",
            "risk_reduction_on_limits",
        },
        "entry": {
            "order_type",
            "dollar_amount",
            "quantity",
            "limit_price",
            "symbol",
            "preferred_symbols",
        },
    }
    if set(document) - set(allowed) or set(allowed) - {"entry"} - set(document):
        raise Halt("owner TOML requires live, broker, risk and exit sections; entry is optional")
    document.setdefault("entry", {})
    for name, allowed_fields in allowed.items():
        if not isinstance(document[name], dict) or set(document[name]) - allowed_fields:
            raise Halt("unknown owner configuration field or credential: " + name)
    live, broker, limits, exit_policy, entry = (document[k] for k in allowed)
    if live.get("enabled") is not True:
        raise Halt("explicit live.enabled = true is required")

    def local_path(value, label):
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise Halt(label + " requires an absolute Linux path")
        result = Path(value).resolve()
        if os.name != "posix" or str(result).startswith(("/mnt/", "//")) or result == Path("/"):
            raise Halt(label + " requires local Linux storage")
        return result

    directory = local_path(live.get("state_dir"), "state_dir")
    helper = local_path(broker.get("oauth_helper"), "oauth_helper")
    config = live_config(directory, live.get("symbols"))
    config.validate()
    selector = broker.get("account_number")
    if selector is not None and (not isinstance(selector, str) or not selector):
        raise Halt("broker.account_number must identify the chosen eligible account")
    config = replace(
        config, account_selector=digest(selector) if selector else broker.get("account_sha256")
    )
    for name, value in list(limits.items()):
        if isinstance(getattr(Risk(), name), str):
            limits[name] = str(dec(value))
    risk = Risk(**limits)
    risk.validate()
    if "max_notional" in live and "max_order_value" in live:
        raise Halt("use one of live.max_notional or live.max_order_value")
    options = {
        "max_notional": str(dec(live.get("max_order_value", live.get("max_notional", "25")))),
        "hold_seconds": exit_policy.get("hold_seconds", 3600),
        "polls": exit_policy.get("polls", 3),
        "entry": entry,
        "exit_order_type": exit_policy.get(
            "order_type",
            "market"
            if entry.get("order_type") == "market" or "dollar_amount" in entry
            else "limit",
        ),
        "session_buffer_seconds": exit_policy.get("session_buffer_seconds", 600),
        "poll_interval_seconds": exit_policy.get("poll_interval_seconds", 1),
        "max_exit_attempts": exit_policy.get("max_exit_attempts"),
        "risk_reduction_on_limits": exit_policy.get("risk_reduction_on_limits", True),
    }
    from .model import Intent

    if "symbol" in entry and "preferred_symbols" in entry:
        raise Halt("configure entry.symbol or entry.preferred_symbols, not both")
    selection = (
        [entry["symbol"]]
        if "symbol" in entry
        else entry.get("preferred_symbols", [config.allowed_symbols[0]])
    )
    if (
        not isinstance(selection, list)
        or not selection
        or len(set(selection)) != len(selection)
        or any(symbol not in config.allowed_symbols for symbol in selection)
    ):
        raise Halt("entry selection must explicitly name allowed symbols")
    if entry:
        entry.setdefault("order_type", "market" if "dollar_amount" in entry else "limit")
        candidate = Intent(
            config.allowed_symbols[0],
            "buy",
            dec(entry["quantity"]) if "quantity" in entry else None,
            dec(entry["limit_price"]) if "limit_price" in entry else None,
            order_type=entry["order_type"],
            dollar_amount=dec(entry["dollar_amount"]) if "dollar_amount" in entry else None,
        )
        candidate.validate()
        for name in ("quantity", "dollar_amount", "limit_price"):
            if name in entry:
                entry[name] = str(dec(entry[name]))
        if candidate.dollar_amount is not None and candidate.dollar_amount > dec(
            options["max_notional"]
        ):
            raise Halt("configured entry amount exceeds maximum notional")
    if (
        options["exit_order_type"] not in {"market", "limit"}
        or type(options["session_buffer_seconds"]) is not int
        or options["session_buffer_seconds"] < 0
    ):
        raise Halt("invalid exit type/session buffer")
    if (
        entry
        and ("dollar_amount" in entry or ("quantity" in entry and dec(entry["quantity"]) % 1))
        and options["exit_order_type"] != "market"
    ):
        raise Halt("fractional entry requires a market exit")
    if (
        not 0 < dec(options["max_notional"])
        or type(options["hold_seconds"]) is not int
        or options["hold_seconds"] < 0
        or type(options["polls"]) is not int
        or options["polls"] < 1
    ):
        raise Halt("invalid bounded one-shot capital/exit policy")
    if (
        type(options["poll_interval_seconds"]) not in (int, float)
        or not 0 < options["poll_interval_seconds"]
    ):
        raise Halt("exit.poll_interval_seconds must be positive")
    if options["max_exit_attempts"] is not None and (
        type(options["max_exit_attempts"]) is not int or options["max_exit_attempts"] <= 0
    ):
        raise Halt("exit.max_exit_attempts must be a positive integer")
    if type(options["risk_reduction_on_limits"]) is not bool:
        raise Halt("exit.risk_reduction_on_limits must be boolean")
    timeout = broker.get("timeout_seconds", 20)
    if type(timeout) is not int or timeout < 1:
        raise Halt("broker timeout must be a positive number of seconds")
    account = broker.get("account_sha256")
    if account is not None and (
        not isinstance(account, str)
        or len(account) != 64
        or any(c not in "0123456789abcdef" for c in account)
    ):
        raise Halt("account_sha256 must be a complete lowercase SHA-256 digest")
    semantic = {
        "config": asdict(config),
        "risk": asdict(risk),
        "options": options,
        "helper": str(helper),
        "account": account,
        "timeout": timeout,
    }
    # Equivalent decimal spelling is not an economic configuration change.
    for name, value in semantic["risk"].items():
        if isinstance(value, str) and name != "version":
            semantic["risk"][name] = format(dec(value).normalize(), "f")
    semantic["options"] = {**options, "entry": dict(entry)}
    semantic["options"]["max_notional"] = format(dec(options["max_notional"]).normalize(), "f")
    for name in ("quantity", "dollar_amount", "limit_price"):
        if name in entry:
            semantic["options"]["entry"][name] = format(dec(entry[name]).normalize(), "f")
    semantic["options"]["poll_interval_seconds"] = format(
        dec(options["poll_interval_seconds"]).normalize(), "f"
    )
    return LocalSettings(path, config, risk, options, helper, account, timeout, digest(semantic))


def create_policy(
    config, risk, account_digest, options, clock=time.time, simulation=False, owner_config_hash=None
):
    from importlib.metadata import version

    return {
        "version": VERSION,
        "application_version": version("tradeagent"),
        "state_schema_version": STATE_SCHEMA_VERSION,
        "execution_protocol_version": EXECUTION_PROTOCOL_VERSION,
        "account_digest": account_digest,
        "schema_hash": Contracts().hash,
        "state_dir": str(Path(config.state_dir).resolve().parent),
        "config": asdict(config),
        "risk": asdict(risk),
        "symbols": config.allowed_symbols,
        "options": options,
        "simulation": simulation,
        "permissions": {
            "long_only": True,
            "equity_only": True,
            "no_leverage": True,
            "max_entries": 1,
            "cancel_known_pending": True,
        },
    }


def check_compatibility(policy):
    if policy.get("version") != VERSION:
        raise Halt(
            "unsupported execution policy; preserve state and install a compatible application"
        )
    if policy.get("state_schema_version", 1) != STATE_SCHEMA_VERSION:
        raise Halt(
            "incompatible state schema; preserve state and use a supported migration before recover"
        )
    if policy.get("execution_protocol_version", 1) not in COMPATIBLE_EXECUTION_PROTOCOLS:
        raise Halt(
            "incompatible execution protocol; preserve state and install a compatible version for recover"
        )
    if policy.get("schema_hash") != Contracts().hash:
        raise Halt(
            "broker contracts incompatible with active state; preserve evidence and update contract adapter before recover"
        )


def active_settings(settings):
    """New TOML applies to the next lifecycle; active requirements come from its journal."""
    artifact = check_run_state(settings)
    if artifact is None:
        return settings
    policy = artifact["policy"]
    if "config" in policy and "risk" in policy:
        config, risk = Config(**policy["config"]), Risk(**policy["risk"])
    else:
        db = sqlite3.connect(f"file:{settings.directory / 'agent/state.sqlite3'}?mode=ro", uri=True)
        try:
            row = db.execute(
                "SELECT payload FROM events WHERE kind='configuration' ORDER BY seq LIMIT 1"
            ).fetchone()
            if row is None:
                raise Halt(
                    "legacy run lacks recorded configuration; inspect status and preserve evidence"
                )
            recorded = json.loads(row[0])
            config, risk = Config(**recorded["config"]), Risk(**recorded["risk"])
        finally:
            db.close()
    config = replace(config, account_selector=policy["account_digest"])
    return replace(settings, config=config, risk=risk, options=policy["options"], bound=True)


def check_run_state(settings, *, reconciliation_only=False):
    """Read-only lost-state check. The external config receipt cannot rearm a run."""
    directory, receipt = settings.directory, settings.receipt
    marker = directory / "live-run.json"
    if marker.exists() or receipt.exists():
        if not marker.is_file() or not (directory / "agent/state.sqlite3").is_file():
            raise Halt(
                "missing execution state cannot be reset; restore preserved journal before recover"
            )
        with sqlite3.connect(
            f"file:{directory / 'agent/state.sqlite3'}?mode=ro", uri=True
        ) as database:
            if database.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
                raise Halt(
                    "incompatible execution state schema; preserve journal and use a supported migration"
                )
        artifact = json.loads(owner_file(marker).read_text())
        if receipt.exists() and json.loads(owner_file(receipt).read_text()) != {
            "marker_hash": digest(artifact)
        }:
            raise Halt("owner run state binding changed; restore the matching journal/receipt")
        policy = artifact.get("policy", {})
        check_compatibility(policy)
        if artifact.get("actor") != "LOCAL_OWNER" or policy.get("simulation") is not False:
            raise Halt("invalid owner execution context")
        if policy.get("state_dir") != str(directory):
            raise Halt(
                "state directory differs from recorded lifecycle; restore original state path"
            )
        selector = settings.config.account_selector or settings.account_sha256
        if selector and selector != policy.get("account_digest"):
            raise Halt(
                "selected account differs from active lifecycle; recover the recorded account first"
            )
        return artifact
    if directory.exists() and any(directory.iterdir()):
        raise Halt(
            "state exists without its lifecycle marker; restore evidence; state cannot be reset"
        )
    return None


class OwnerPolicy:
    def __init__(
        self,
        artifact,
        state,
        config,
        risk,
        account_digest,
        options,
        clock,
        simulation=False,
        settings=None,
        permission_reader=None,
    ):
        self.artifact, self.state, self.config, self.risk = artifact, state, config, risk
        self.account_digest, self.options, self.clock = account_digest, options, clock
        self.simulation, self.settings = simulation, settings
        self.permission_reader = permission_reader

    def validate(self):
        if not self.simulation:
            if type(self.settings) is not LocalSettings:
                raise Halt("explicit local owner configuration required")
            self.settings.validate()
            check_run_state(self.settings)
            marker = self.settings.directory / "live-run.json"
            if json.loads(owner_file(marker).read_text()) != self.artifact:
                raise Halt("owner run context changed")
        actor = "SIMULATED_POLICY_OWNER" if self.simulation else "LOCAL_OWNER"
        if set(self.artifact) != {"policy", "actor"} or self.artifact["actor"] != actor:
            raise Halt("invalid local owner execution policy")
        policy = self.artifact["policy"]
        check_compatibility(policy)
        if policy["account_digest"] != self.account_digest or policy["options"] != self.options:
            raise Halt("owner execution account or active options changed")
        if "config" in policy:
            recorded_config = Config(**policy["config"])
            recorded_config = replace(
                recorded_config, account_selector=self.config.account_selector
            )
            if (
                asdict(recorded_config) != asdict(self.config)
                or Risk(**policy["risk"]) != self.risk
            ):
                raise Halt("active execution requirements differ from recorded lifecycle")
        limit = dec(self.options["max_notional"])
        if (
            not 0 < limit
            or type(self.options["hold_seconds"]) is not int
            or self.options["hold_seconds"] < 0
            or type(self.options["polls"]) is not int
            or self.options["polls"] < 1
        ):
            raise Halt("owner execution exceeds one-shot bounds")
        intents = self.state.db.execute("SELECT payload FROM intents").fetchall()
        sides = [json.loads(row[0])["side"] for row in intents]
        if sides.count("buy") > 1:
            raise Halt("owner execution one-entry budget exceeded")
        maximum = self.options.get("max_exit_attempts")
        if maximum is not None and sides.count("sell") > maximum:
            raise Halt(
                "configured exit attempt limit reached; reconcile residual and update next recovery policy explicitly"
            )
        return policy

    def check(self, intent, snapshot, baseline, cancel=False, authorize=True):
        self.validate()
        if snapshot.account_key != self.account_digest[:16]:
            raise Halt("owner execution policy account/asset mismatch")
        if intent.asset != "equity" or intent.symbol not in self.config.allowed_symbols:
            raise Halt("owner execution policy requires allowed equity intent")
        if cancel:
            return
        if (
            authorize
            and self.permission_reader is not None
            and self.permission_reader() is not False
        ):
            raise Halt("broker trade approvals enabled or uncertain; owner setup required")
        if not self.simulation and self.permission_reader is None:
            raise Halt("broker approval-setting reader required")
        if not self.simulation and snapshot.countries.get(intent.symbol) != "US":
            raise Halt("US equity eligibility unavailable")
        if (
            intent.side == "buy"
            and self.state.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='one_shot_meta'"
            ).fetchone()
        ):
            row = self.state.db.execute(
                "SELECT payload FROM one_shot_meta WHERE key='decision'"
            ).fetchone()
            recorded = json.loads(row[0]) if row else {}
            economic = (recorded.get("provenance") or {}).get("economic_plan")
            if economic is not None:
                from .research.tradeplan import plan_from_dict, validate_execution_plan

                plan = plan_from_dict(economic)
                validate_execution_plan(plan, self.clock())
                if intent.symbol != plan.decision.instrument or snapshot.asks.get(
                    intent.symbol, dec(0)
                ) > dec(plan.decision.entry_limit):
                    raise Halt("entry price condition is no longer met at owner send boundary")
        if intent.side == "buy" and intent.risk_notional(snapshot, self.risk) > dec(
            self.options["max_notional"]
        ):
            raise Halt("owner execution policy entry capital limit")
        if intent.side == "sell":
            initial = self.state.db.execute(
                "SELECT payload FROM one_shot_meta WHERE key='initial'"
            ).fetchone()
            if not initial:
                raise Halt("cannot attribute sell position to this one-shot run")
            refs = {row[0] for row in self.state.db.execute("SELECT ref_id FROM intents")}
            owned = sum(
                (
                    dec(o["cumulative_quantity"]) * (1 if o["side"] == "buy" else -1)
                    for o in snapshot.orders
                    if o.get("ref_id") in refs and o["symbol"] == intent.symbol
                ),
                dec(0),
            )
            original = dec(json.loads(initial[0])["positions"].get(intent.symbol, 0))
            sellable = max(dec(0), snapshot.available.get(intent.symbol, dec(0)) - original)
            if (
                intent.quantity > min(owned, sellable)
                or snapshot.positions.get(intent.symbol, dec(0)) != original + owned
            ):
                raise Halt("exit quantity exceeds confirmed exclusively owned position")
        check_order(
            intent,
            snapshot,
            self.config,
            self.risk,
            self.clock(),
            baseline,
            risk_reducing=intent.side == "sell"
            and self.options.get("risk_reduction_on_limits", True),
        )
        if intent.side == "buy" and (Path(self.config.state_dir).parent / "KILL").exists():
            raise Halt("emergency stop prevents new exposure at authorization boundary")

    def check_review(self, intent, review, snapshot):
        from .broker import utc_time
        from .model import MAX_FUTURE_SKEW_SECONDS, timestamp_fresh

        quote = review["response"]["data"].get("quote_data")
        if (
            not isinstance(quote, dict)
            or quote.get("symbol") != intent.symbol
            or quote.get("state") != "active"
            or quote.get("has_traded") is not True
        ):
            raise Halt("broker review equity quote is missing/ineligible")
        for field in ("venue_bid_time", "venue_ask_time", "venue_last_trade_time"):
            if not timestamp_fresh(
                utc_time(quote.get(field)),
                self.clock(),
                self.risk.max_data_age_seconds,
                future_skew=MAX_FUTURE_SKEW_SECONDS,
            ):
                raise Halt("stale broker review quote")
        bid, ask = dec(quote.get("bid_price")), dec(quote.get("ask_price"))
        if bid <= 0 or ask < bid:
            raise Halt("invalid broker review market")
        for observed, current in (
            (bid, snapshot.bids[intent.symbol]),
            (ask, snapshot.asks[intent.symbol]),
        ):
            if abs(observed - current) / snapshot.prices[intent.symbol] > dec(
                self.risk.review_price_tolerance_fraction
            ):
                raise Halt("broker review outside unchanged price tolerance")

    def verify(self, artifact, binding, simulation=False):
        self.validate()
        if simulation != self.simulation or artifact.get("policy_context") != self.artifact:
            raise Halt("owner decision authorization mismatch")
        if (
            binding.get("account_digest") != self.account_digest
            or binding.get("simulation") != self.simulation
        ):
            raise Halt("owner decision account/mode mismatch")
        if "payload" in binding:
            if (
                artifact.get("binding") != binding
                or binding.get("config") != json.loads(dumps(asdict(self.config)))
                or binding.get("risk") != json.loads(dumps(asdict(self.risk)))
                or binding.get("schema_hash") != self.validate()["schema_hash"]
            ):
                raise Halt("owner decision differs from reviewed context")
        elif binding.get("action") != "cancel" or artifact.get("binding") != binding:
            raise Halt("owner cancellation differs from exact known order")


class StandingLifecycle(SupervisedLifecycle):
    """Authorization specialization only; durable review/place/cancel/fills stay in core."""

    def __init__(self, state, adapter, config, risk, run, fence, clock, guard):
        if type(guard) is not OwnerPolicy or guard.simulation != adapter.is_simulation:
            raise Halt("production controller requires an exact local owner guard")
        self.guard = guard
        guard.validate()
        super().__init__(state, adapter, config, risk, run, fence, clock)

    def _state_binding(self, snapshot):
        from .supervised import state_binding

        return state_binding(snapshot, economic_only=True)

    def _review_deadline(self, review, now):
        # Standing owner authorization has no human approval pause. Broker evidence
        # still ages from request start and its actual venue timestamps, never receipt.
        quote = review["response"]["data"]["quote_data"]
        from .broker import utc_time

        return (
            min(
                review["asof"],
                *(
                    utc_time(quote[k])
                    for k in ("venue_bid_time", "venue_ask_time", "venue_last_trade_time")
                ),
            )
            + self.risk.max_data_age_seconds
        )

    def _review_market(self, intent, review, snapshot):
        self.guard.check_review(intent, review, snapshot)

    def _risk(self, intent, snapshot, baseline, fees=0):
        try:
            result = self.guard.check(intent, snapshot, baseline, authorize=False)
            self.broker.transport.review_snapshot = (snapshot, baseline)
            return result
        except Halt as exc:
            self.state.event(
                self.run, "risk_rejected", {"payload": intent.payload(), "reason": str(exc)}
            )
            raise

    def execute(self, key, intent):
        binding = json.loads(
            self.state.db.execute("SELECT packet FROM plans WHERE key=?", (key,)).fetchone()[0]
        )["binding"]
        return super().execute(
            key, intent, {"policy_context": self.guard.artifact, "binding": binding}, self.guard
        )

    def cancel(self, key, intent, artifact=None):
        row = self.state.db.execute("SELECT broker_id FROM intents WHERE key=?", (key,)).fetchone()
        binding = {
            "action": "cancel",
            "key": key,
            "order_id": row[0],
            "account_digest": self.guard.account_digest,
            "simulation": self.guard.simulation,
            "expires": self.clock() + 30,
        }
        return super().cancel(
            key, intent, {"policy_context": self.guard.artifact, "binding": binding}, self.guard
        )


@contextmanager
def owner_run_lock(settings):
    """Stable owner-command lock lives outside the state that new-run archives."""
    import fcntl

    settings.validate()
    path = settings.path.with_name(settings.path.name + ".lock")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        owner_file(path)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Halt("another owner command holds the configuration lock") from exc
        yield
    finally:
        os.close(descriptor)
