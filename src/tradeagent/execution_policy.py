"""Owner-installed, signed one-shot standing authorization; no production signer."""

import base64
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import time
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from .model import Config, Halt, dec, digest
from .risk import check_order
from .schema import Contracts
from .state import dumps
from .supervised import SupervisedLifecycle

VERSION = "owner-one-shot-v1"


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
        raise Halt("authorization files require owner-only regular local files")
    return path


def verify_signature(policy, signature, public_key):
    try:
        raw = base64.b64decode(signature, validate=True)
        with tempfile.TemporaryDirectory() as folder:
            message, sig = Path(folder) / "message", Path(folder) / "signature"
            message.write_bytes(dumps(policy).encode())
            sig.write_bytes(raw)
            result = subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-verify",
                    "-pubin",
                    "-inkey",
                    str(public_key),
                    "-rawin",
                    "-in",
                    str(message),
                    "-sigfile",
                    str(sig),
                ],
                capture_output=True,
                timeout=10,
                check=False,
            )
            if result.returncode:
                raise Halt("invalid owner standing-authorization signature")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise Halt("owner signature verification unavailable or malformed") from exc


def request_policy(config, risk, account_digest, options, clock=time.time, simulation=False):
    now = clock()
    return {
        "version": VERSION,
        "grant_id": str(uuid4()),
        "account_digest": account_digest,
        "package_hash": package_hash(),
        "config_hash": digest(asdict(config)),
        "risk_hash": digest(asdict(risk)),
        "schema_hash": Contracts().hash,
        "state_dir": str(Path(config.state_dir).resolve().parent),
        "symbols": config.allowed_symbols,
        "options": options,
        "issued_at": now,
        "expires_at": now + 86400,
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


def install_authorization(request, public_key, fingerprint, signature_path, destination):
    """Owner runs this after offline signing. Never generates a key or signature."""
    policy = json.loads(owner_file(request).read_text())
    key = owner_file(public_key).read_bytes()
    if hashlib.sha256(key).hexdigest() != fingerprint:
        raise Halt("owner public-key fingerprint mismatch")
    if policy.get("simulation") is not False or policy.get("package_hash") != package_hash():
        raise Halt("owner setup requires current production request")
    signature = base64.b64encode(owner_file(signature_path).read_bytes()).decode()
    verify_signature(policy, signature, public_key)
    path = Path(destination).resolve()
    if path.exists():
        raise Halt("authorization directory already exists; never silently replace trust")
    os.umask(0o077)
    path.mkdir(parents=True, mode=0o700)
    (path / "owner-public.pem").write_bytes(key)
    (path / "authorization.json").write_text(
        dumps({"policy": policy, "signature": signature, "key_sha256": fingerprint})
    )
    return {
        "status": "OWNER_AUTHORIZATION_INSTALLED",
        "armed": False,
        "authorization_dir": str(path),
    }


class OwnerGrant:
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
        public_key=None,
        permission_reader=None,
    ):
        self.artifact, self.state, self.config, self.risk = artifact, state, config, risk
        self.account_digest, self.options, self.clock = account_digest, options, clock
        self.simulation, self.public_key = simulation, public_key
        self.permission_reader = permission_reader

    @classmethod
    def load(cls, directory, **kwargs):
        root = Path(directory).resolve()
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Halt("owner authorization directory must have mode 0700")
        artifact = json.loads(owner_file(root / "authorization.json").read_text())
        key = owner_file(root / "owner-public.pem")
        return cls(artifact, public_key=key, **kwargs)

    def validate(self):
        artifact = self.artifact
        policy = artifact.get("policy", {})
        if self.simulation:
            if (
                set(artifact) != {"policy", "actor"}
                or artifact["actor"] != "SIMULATED_POLICY_OWNER"
            ):
                raise Halt("invalid simulated standing authorization")
        else:
            if set(artifact) != {"policy", "signature", "key_sha256"} or not self.public_key:
                raise Halt("owner standing authorization setup required")
            if (
                hashlib.sha256(owner_file(self.public_key).read_bytes()).hexdigest()
                != artifact["key_sha256"]
            ):
                raise Halt("owner public-key pin changed")
            verify_signature(policy, artifact["signature"], self.public_key)
        expected = request_policy(
            self.config, self.risk, self.account_digest, self.options, self.clock, self.simulation
        )
        for name in (
            "version",
            "account_digest",
            "package_hash",
            "config_hash",
            "risk_hash",
            "schema_hash",
            "state_dir",
            "symbols",
            "options",
            "permissions",
            "simulation",
        ):
            if digest(policy.get(name)) != digest(expected[name]):
                raise Halt("standing authorization context changed: " + name)
        if (
            set(policy) != set(expected)
            or not isinstance(policy.get("grant_id"), str)
            or not policy["grant_id"]
        ):
            raise Halt("invalid standing authorization identity")
        issued, expires = dec(policy.get("issued_at")), dec(policy.get("expires_at"))
        if not issued <= dec(self.clock()) < expires or not 0 < expires - issued <= 86400:
            raise Halt("standing authorization expired or not active")
        limit = dec(self.options["max_notional"])
        if (
            not 0 < limit <= 1000
            or not 0 <= self.options["hold_seconds"] <= 21600
            or not 1 <= self.options["polls"] <= 30
        ):
            raise Halt("standing authorization exceeds one-shot bounds")
        if self.config.allowed_symbols != ["QQQ", "IWM"]:
            raise Halt("one-shot universe requires the frozen ordinary QQQ/IWM ETFs")
        intents = self.state.db.execute("SELECT payload FROM intents").fetchall()
        sides = [json.loads(row[0])["side"] for row in intents]
        if len(sides) > 2 or sides.count("buy") > 1 or sides.count("sell") > 1:
            raise Halt("standing authorization one-entry/one-exit budget exceeded")
        return policy

    def check(self, intent, snapshot, baseline, cancel=False):
        self.validate()
        if snapshot.account_key != self.account_digest[:16] or snapshot.options:
            raise Halt("standing authorization account/asset mismatch")
        if intent.asset != "equity" or intent.symbol not in self.config.allowed_symbols:
            raise Halt("standing authorization requires allowed equity intent")
        if cancel:
            return
        if self.permission_reader is not None and self.permission_reader() is not False:
            raise Halt("broker trade approvals enabled or uncertain; owner setup required")
        if not self.simulation and self.permission_reader is None:
            raise Halt("broker approval-setting reader required")
        if intent.side == "buy" and self.clock() + self.options["hold_seconds"] + 600 >= float(
            self.artifact["policy"]["expires_at"]
        ):
            raise Halt("standing authorization has insufficient time for its bounded exit")
        if intent.side == "buy" and intent.quantity * intent.limit_price > dec(
            self.options["max_notional"]
        ):
            raise Halt("standing authorization entry capital limit")
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
        if simulation != self.simulation or artifact.get("grant") != self.artifact:
            raise Halt("standing decision authorization mismatch")
        if (
            binding.get("account_digest") != self.account_digest
            or binding.get("simulation") != self.simulation
        ):
            raise Halt("standing decision account/mode mismatch")
        if "payload" in binding:
            if (
                artifact.get("binding") != binding
                or binding.get("config") != json.loads(dumps(asdict(self.config)))
                or binding.get("risk") != json.loads(dumps(asdict(self.risk)))
                or binding.get("schema_hash") != self.validate()["schema_hash"]
            ):
                raise Halt("standing decision differs from reviewed context")
        elif binding.get("action") != "cancel" or artifact.get("binding") != binding:
            raise Halt("standing cancellation differs from exact known order")


class StandingLifecycle(SupervisedLifecycle):
    """Authorization specialization only; durable review/place/cancel/fills stay in core."""

    def __init__(self, state, adapter, config, risk, run, fence, clock, guard):
        if type(guard) is not OwnerGrant or guard.simulation != adapter.is_simulation:
            raise Halt("production controller requires an exact owner standing guard")
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
            key, intent, {"grant": self.guard.artifact, "binding": binding}, self.guard
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
            key, intent, {"grant": self.guard.artifact, "binding": binding}, self.guard
        )
