"""Local owner one-shot policy; broker authentication and permissions remain external."""

import hashlib
import json
import os
import stat
import time
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path

from .model import Config, Halt, Risk, dec, digest
from .risk import check_order
from .schema import Contracts
from .state import dumps
from .supervised import SupervisedLifecycle

VERSION = "local-owner-one-shot-v2"


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

    @property
    def directory(self):
        return Path(self.config.state_dir).parent

    @property
    def receipt(self):
        return self.path.with_name(self.path.name + ".run.json")

    def validate(self):
        if hashlib.sha256(owner_file(self.path).read_bytes()).hexdigest() != self.file_hash:
            raise Halt("owner LIVE configuration changed during execution")
        if self != load_live_config(self.path):
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
        "live": {"enabled", "symbols", "state_dir", "max_notional"},
        "broker": {"oauth_helper", "account_sha256", "timeout_seconds"},
        "risk": {
            "max_positions",
            "max_position_fraction",
            "max_new_exposure_fraction",
            "min_cash_fraction",
        },
        "exit": {"hold_seconds", "polls"},
    }
    if set(document) != set(allowed):
        raise Halt("owner TOML requires exactly live, broker, risk and exit sections")
    for name, fields in allowed.items():
        if not isinstance(document[name], dict) or set(document[name]) - fields:
            raise Halt("unknown owner configuration field or credential: " + name)
    live, broker, limits, exit_policy = (document[k] for k in allowed)
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
    if not set(config.allowed_symbols) <= {"QQQ", "IWM"}:
        raise Halt("initial one-shot universe permits only ordinary QQQ/IWM ETFs")
    defaults = Risk()
    for name in ("max_position_fraction", "max_new_exposure_fraction", "min_cash_fraction"):
        if name in limits:
            limits[name] = str(dec(limits[name]))
    risk = Risk(**limits)
    risk.validate()
    if (
        risk.max_positions > defaults.max_positions
        or dec(risk.max_position_fraction) > dec(defaults.max_position_fraction)
        or dec(risk.max_new_exposure_fraction) > dec(defaults.max_new_exposure_fraction)
        or dec(risk.min_cash_fraction) < dec(defaults.min_cash_fraction)
    ):
        raise Halt("owner position limits may tighten but not weaken production risk ceilings")
    options = {
        "max_notional": str(dec(live.get("max_notional", "25"))),
        "hold_seconds": exit_policy.get("hold_seconds", 3600),
        "polls": exit_policy.get("polls", 3),
    }
    if (
        not 0 < dec(options["max_notional"]) <= 1000
        or type(options["hold_seconds"]) is not int
        or not 0 <= options["hold_seconds"] <= 21600
        or type(options["polls"]) is not int
        or not 1 <= options["polls"] <= 30
    ):
        raise Halt("invalid bounded one-shot capital/exit policy")
    timeout = broker.get("timeout_seconds", 20)
    if type(timeout) is not int or not 1 <= timeout <= 60:
        raise Halt("broker timeout must be 1..60 seconds")
    account = broker.get("account_sha256")
    if account is not None and (
        not isinstance(account, str)
        or len(account) != 64
        or any(c not in "0123456789abcdef" for c in account)
    ):
        raise Halt("account_sha256 must be a complete lowercase SHA-256 digest")
    return LocalSettings(
        path, config, risk, options, helper, account, timeout, hashlib.sha256(raw).hexdigest()
    )


def create_policy(
    config, risk, account_digest, options, clock=time.time, simulation=False, owner_config_hash=None
):
    return {
        "version": VERSION,
        "account_digest": account_digest,
        "package_hash": package_hash(),
        "config_hash": digest(asdict(config)),
        "risk_hash": digest(asdict(risk)),
        "schema_hash": Contracts().hash,
        "state_dir": str(Path(config.state_dir).resolve().parent),
        "symbols": config.allowed_symbols,
        "options": options,
        "owner_config_hash": owner_config_hash,
        "simulation": simulation,
        "permissions": {
            "long_only": True,
            "equity_only": True,
            "no_leverage": True,
            "max_entries": 1,
            "max_exits": 1,
            "cancel_known_pending": True,
        },
    }


def check_run_state(settings):
    """Read-only lost-state check. The external config receipt cannot rearm a run."""
    directory, receipt = settings.directory, settings.receipt
    marker = directory / "live-run.json"
    if receipt.exists():
        owner_file(receipt)
        if not marker.is_file() or not (directory / "agent/state.sqlite3").is_file():
            raise Halt("owner run was already bound; missing execution state cannot be reset")
        artifact = json.loads(owner_file(marker).read_text())
        if json.loads(receipt.read_text()) != {"marker_hash": digest(artifact)}:
            raise Halt("owner run state binding changed")
        policy = artifact.get("policy", {})
        expected = create_policy(
            settings.config,
            settings.risk,
            policy.get("account_digest"),
            settings.options,
            owner_config_hash=settings.file_hash,
        )
        if policy != expected:
            raise Halt("LIVE run account/code/configuration changed; never replay")
        if settings.account_sha256 and policy.get("account_digest") != settings.account_sha256:
            raise Halt("owner run account differs from configured account pin")
    elif marker.exists() or (directory.exists() and any(directory.iterdir())):
        raise Halt("LIVE one-shot requires isolated fresh state or its existing config receipt")


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
        expected = create_policy(
            self.config,
            self.risk,
            self.account_digest,
            self.options,
            self.clock,
            self.simulation,
            self.settings.file_hash if self.settings else None,
        )
        if policy != expected:
            raise Halt("owner execution account/code/configuration context changed")
        limit = dec(self.options["max_notional"])
        if (
            not 0 < limit <= 1000
            or type(self.options["hold_seconds"]) is not int
            or not 0 <= self.options["hold_seconds"] <= 21600
            or type(self.options["polls"]) is not int
            or not 1 <= self.options["polls"] <= 30
        ):
            raise Halt("owner execution exceeds one-shot bounds")
        if not set(self.config.allowed_symbols) <= {"QQQ", "IWM"}:
            raise Halt("initial one-shot universe permits only ordinary QQQ/IWM ETFs")
        intents = self.state.db.execute("SELECT payload FROM intents").fetchall()
        sides = [json.loads(row[0])["side"] for row in intents]
        if len(sides) > 2 or sides.count("buy") > 1 or sides.count("sell") > 1:
            raise Halt("owner execution one-entry/one-exit budget exceeded")
        return policy

    def check(self, intent, snapshot, baseline, cancel=False):
        self.validate()
        if snapshot.account_key != self.account_digest[:16] or snapshot.options:
            raise Halt("owner execution policy account/asset mismatch")
        if intent.asset != "equity" or intent.symbol not in self.config.allowed_symbols:
            raise Halt("owner execution policy requires allowed equity intent")
        if cancel:
            return
        if self.permission_reader is not None and self.permission_reader() is not False:
            raise Halt("broker trade approvals enabled or uncertain; owner setup required")
        if not self.simulation and self.permission_reader is None:
            raise Halt("broker approval-setting reader required")
        if intent.side == "buy" and intent.quantity * intent.limit_price > dec(
            self.options["max_notional"]
        ):
            raise Halt("owner execution policy entry capital limit")
        if intent.side == "sell":
            initial = self.state.db.execute(
                "SELECT payload FROM one_shot_meta WHERE key='initial'"
            ).fetchone()
            if not initial or dec(json.loads(initial[0])["positions"].get(intent.symbol, 0)) != 0:
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
            if owned != intent.quantity or snapshot.positions.get(intent.symbol) != owned:
                raise Halt("exit quantity exceeds confirmed exclusively owned position")
        check_order(intent, snapshot, self.config, self.risk, self.clock(), baseline)
        if intent.side == "buy" and (Path(self.config.state_dir).parent / "KILL").exists():
            raise Halt("emergency stop prevents new exposure at authorization boundary")

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

    def _review_market(self, intent, review, snapshot):
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

    def _risk(self, intent, snapshot, baseline, fees=0):
        self.guard.check(intent, snapshot, baseline)
        return super()._risk(intent, snapshot, baseline, fees)

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
