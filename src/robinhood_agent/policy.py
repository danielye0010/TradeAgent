"""Signed one-time autonomous envelope. No signer or real release exists here."""

import base64
import hashlib
import json
import math
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .model import MAX_FUTURE_SKEW_SECONDS, Halt, Intent, dec, digest
from .state import dumps

AUTONOMOUS_PUBLIC_KEY_SHA256 = None
MAX_GRANT_LIFETIME_SECONDS = 7 * 86400
GRANT_VERSION = "autonomous-equity-grant-v1"


AUTONOMOUS_DEPLOYMENT_PHASE = "CANARY"


def require_autonomous_release(phase=None):
    # This task prepares the check but deliberately does not enroll a key.
    pin = AUTONOMOUS_PUBLIC_KEY_SHA256
    if (
        not isinstance(pin, str)
        or len(pin) != 64
        or any(c not in "0123456789abcdef" for c in pin)
        or AUTONOMOUS_DEPLOYMENT_PHASE not in {"CANARY", "LIMITED_EQUITY"}
        or (phase is not None and phase != AUTONOMOUS_DEPLOYMENT_PHASE)
    ):
        raise Halt("AUTONOMOUS release closed: pinned production key/phase required")


def deployment_hash(root):
    root = Path(root)
    manifest = json.loads((root / "docs/deployment_manifest.json").read_text())
    expected = manifest["files"]
    for name, sha in expected.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != sha:
            raise Halt("deployment files changed outside grant")
    source = {str(p.relative_to(root)) for p in (root / "src").rglob("*.py")}
    if source != {n for n in expected if n.startswith("src/") and n.endswith(".py")}:
        raise Halt("unfrozen executable source")
    fingerprint = hashlib.sha256(
        json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if fingerprint != manifest["framework_sha256"]:
        raise Halt("deployment manifest mismatch")
    return fingerprint


@dataclass(frozen=True)
class GrantContext:
    account_digest: str
    deployment_version: str
    deployment_hash: str
    strategy_id: str
    strategy_hash: str
    config_hash: str
    risk_hash: str
    schema_hash: str
    universe_rules_hash: str
    universe_evidence_hash: str


@dataclass(frozen=True)
class PolicyLimits:
    max_order_notional: str = "3"
    max_order_fraction: str = "0.03"
    max_single_name_fraction: str = "0.03"
    max_portfolio_fraction: str = "0.03"
    min_cash_fraction: str = "0.97"
    max_positions: int = 1
    max_daily_turnover_fraction: str = "0.06"
    daily_loss_halt_fraction: str = "0.02"
    max_drawdown_fraction: str = "0.10"
    max_quote_age_seconds: int = 120
    max_book_age_seconds: int = 120
    max_book_future_skew_seconds: float = MAX_FUTURE_SKEW_SECONDS
    max_spread_fraction: str = "0.005"
    min_depth_shares: int = 1000

    def validate(self, risk):
        for name, base, upper in (
            ("max_order_fraction", risk.max_new_exposure_fraction, True),
            ("max_single_name_fraction", risk.max_position_fraction, True),
            ("max_portfolio_fraction", 1, True),
            ("min_cash_fraction", risk.min_cash_fraction, False),
            ("max_daily_turnover_fraction", risk.max_daily_turnover_fraction, True),
            ("daily_loss_halt_fraction", risk.daily_loss_halt_fraction, True),
            ("max_drawdown_fraction", risk.max_drawdown_fraction, True),
            ("max_spread_fraction", risk.max_spread_fraction, True),
        ):
            value = dec(getattr(self, name))
            if not 0 < value <= 1 or (value > dec(base) if upper else value < dec(base)):
                raise Halt("grant attempts to expand frozen risk")
        if dec(self.max_order_notional) <= 0:
            raise Halt("invalid grant notional")
        for name, cap in (
            ("max_quote_age_seconds", risk.max_data_age_seconds),
            ("max_book_age_seconds", risk.max_data_age_seconds),
            ("max_positions", risk.max_positions),
        ):
            v = getattr(self, name)
            if type(v) is not int or not 0 < v <= cap:
                raise Halt("invalid grant integer limit")
        if (
            type(self.min_depth_shares) is not int
            or self.min_depth_shares < risk.min_equity_depth_shares
        ):
            raise Halt("grant weakens depth")
        skew = self.max_book_future_skew_seconds
        if (
            type(skew) not in (int, float)
            or not math.isfinite(skew)
            or not 0 <= skew <= MAX_FUTURE_SKEW_SECONDS
        ):
            raise Halt("grant weakens timestamp cap")


class PolicySignature:
    """Verify a HUMAN-issued policy once per boundary. Never creates signatures."""

    def __init__(self, public_key=None):
        self.public_key = Path(public_key) if public_key else None

    def verify(self, artifact, simulation):
        if not isinstance(artifact, dict) or not isinstance(artifact.get("policy"), dict):
            raise Halt("signed autonomous policy required")
        if simulation:
            if (
                set(artifact) != {"policy", "simulation", "actor"}
                or artifact["simulation"] is not True
                or artifact["actor"] != "SIMULATED_POLICY_OWNER"
            ):
                raise Halt("invalid simulated policy grant")
            return
        if (
            set(artifact) != {"policy", "simulation", "signature"}
            or artifact["simulation"] is not False
            or not self.public_key
            or AUTONOMOUS_PUBLIC_KEY_SHA256 is None
        ):
            raise Halt("human autonomous policy key/release required")
        if hashlib.sha256(self.public_key.read_bytes()).hexdigest() != AUTONOMOUS_PUBLIC_KEY_SHA256:
            raise Halt("autonomous policy public-key pin mismatch")
        try:
            signature = base64.b64decode(artifact["signature"], validate=True)
            with tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                message, sig = directory / "policy", directory / "signature"
                message.write_bytes(dumps(artifact["policy"]).encode())
                sig.write_bytes(signature)
                result = subprocess.run(
                    [
                        "openssl",
                        "pkeyutl",
                        "-verify",
                        "-pubin",
                        "-inkey",
                        str(self.public_key),
                        "-rawin",
                        "-in",
                        str(message),
                        "-sigfile",
                        str(sig),
                    ],
                    capture_output=True,
                )
                if result.returncode:
                    raise Halt("invalid autonomous policy signature")
        except (KeyError, ValueError, OSError) as exc:
            raise Halt("malformed autonomous policy signature") from exc


class PolicyGuard:
    def __init__(
        self,
        artifact,
        context,
        limits,
        state,
        config,
        risk,
        simulation,
        clock,
        context_reader,
        verifier=None,
    ):
        if simulation is not True:
            require_autonomous_release()
        self.artifact, self.context, self.limits = artifact, context, limits
        self.state, self.config, self.risk = state, config, risk
        self.simulation, self.clock, self.context_reader = simulation, clock, context_reader
        self.verifier = verifier or PolicySignature()
        state.db.executescript("""
        CREATE TABLE IF NOT EXISTS emergency_halt(singleton INTEGER PRIMARY KEY CHECK(singleton=1), reason TEXT NOT NULL, at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS policy_decisions(grant_id TEXT NOT NULL, side TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY(grant_id, side));
        """)

    def validate(self):
        self.verifier.verify(self.artifact, self.simulation)
        policy = self.artifact["policy"]
        expected = {
            "version",
            "grant_id",
            "phase",
            "issued_at",
            "expires_at",
            "context",
            "limits",
            "asset_class",
            "long_only",
            "no_leverage",
            "allowed_session",
            "order_type",
            "time_in_force",
            "max_canary_buys",
            "max_canary_sells",
        }
        if (
            set(policy) != expected
            or policy["version"] != GRANT_VERSION
            or policy["phase"] not in {"CANARY", "LIMITED_EQUITY"}
        ):
            raise Halt("unsupported autonomous grant phase/version")
        if not isinstance(policy["grant_id"], str) or not policy["grant_id"]:
            raise Halt("missing autonomous grant identity")
        issued, expires = policy["issued_at"], policy["expires_at"]
        if (
            any(type(v) not in (int, float) or not math.isfinite(v) for v in (issued, expires))
            or not issued <= self.clock() < expires
            or not 0 < expires - issued <= MAX_GRANT_LIFETIME_SECONDS
        ):
            raise Halt("autonomous grant expired/not active")
        if (
            policy["context"] != asdict(self.context)
            or self.context_reader() != self.context
            or self.context.config_hash != digest(asdict(self.config))
            or self.context.risk_hash != digest(asdict(self.risk))
        ):
            raise Halt("account/deployment/strategy/risk/schema/universe outside grant")
        if policy["limits"] != asdict(self.limits):
            raise Halt("autonomous limits changed")
        quota = 1 if policy["phase"] == "CANARY" else 0
        fixed = {
            "asset_class": "equity",
            "long_only": True,
            "no_leverage": True,
            "allowed_session": "XNYS_REGULAR",
            "order_type": "limit",
            "time_in_force": "gfd",
            "max_canary_buys": quota,
            "max_canary_sells": quota,
        }
        if any(type(policy[k]) is not type(v) or policy[k] != v for k, v in fixed.items()):
            raise Halt("unsupported autonomous permission")
        if not self.simulation:
            require_autonomous_release(policy["phase"])
        self.limits.validate(self.risk)
        return policy

    def check(self, intent, snapshot, baseline):
        policy = self.validate()
        if (
            type(intent) is not Intent
            or intent.asset != "equity"
            or intent.quantity <= 0
            or intent.quantity != intent.quantity.to_integral_value()
        ):
            raise Halt("grant requires whole long equity shares")
        if policy["phase"] == "CANARY" and intent.quantity != dec(1):
            raise Halt("canary requires one whole long equity share")
        if snapshot.account_key != self.context.account_digest[:16]:
            raise Halt("grant account mismatch")
        if snapshot.options or not snapshot.regular_session:
            raise Halt("grant requires equity-only regular session")
        if (
            intent.side == "buy"
            and self.state.db.execute("SELECT 1 FROM emergency_halt").fetchone()
        ):
            raise Halt("emergency halt blocks new entries")
        if intent.side not in {"buy", "sell"}:
            raise Halt("grant side unsupported")
        limit, nav = self.limits, snapshot.nav
        if dec(baseline) <= 0 or nav <= 0:
            raise Halt("positive grant accounting baseline/NAV required")
        notional = intent.quantity * intent.limit_price
        if notional > min(dec(limit.max_order_notional), nav * dec(limit.max_order_fraction)):
            raise Halt("grant per-order exposure")
        from .model import timestamp_fresh

        for times in (snapshot.quote_times, snapshot.bid_times, snapshot.ask_times):
            if not timestamp_fresh(
                times.get(intent.symbol),
                self.clock(),
                limit.max_quote_age_seconds,
                future_skew=limit.max_book_future_skew_seconds,
            ):
                raise Halt("grant quote freshness")
        book = snapshot.liquidity.get(intent.symbol, {})
        if (
            not timestamp_fresh(
                book.get("asof"),
                self.clock(),
                limit.max_book_age_seconds,
                future_skew=limit.max_book_future_skew_seconds,
            )
            or min(dec(book.get("bid_size")), dec(book.get("ask_size"))) < limit.min_depth_shares
        ):
            raise Halt("grant liquidity")
        if (snapshot.asks[intent.symbol] - snapshot.bids[intent.symbol]) / snapshot.prices[
            intent.symbol
        ] > dec(limit.max_spread_fraction):
            raise Halt("grant spread")
        if (dec(baseline) - nav) / dec(baseline) >= dec(limit.daily_loss_halt_fraction):
            raise Halt("grant daily loss")
        if snapshot.high_water_nav is not None and (
            snapshot.high_water_nav - nav
        ) / snapshot.high_water_nav >= dec(limit.max_drawdown_fraction):
            raise Halt("grant drawdown")
        if snapshot.daily_turnover + notional > nav * dec(limit.max_daily_turnover_fraction):
            raise Halt("grant turnover")
        if intent.side == "buy":
            holdings = sum(q * snapshot.prices[s] for s, q in snapshot.positions.items())
            held = snapshot.positions.get(intent.symbol, dec(0))
            if holdings + notional > nav * dec(
                limit.max_portfolio_fraction
            ) or held * snapshot.prices[intent.symbol] + notional > nav * dec(
                limit.max_single_name_fraction
            ):
                raise Halt("grant portfolio/name exposure")
            if held == 0 and sum(q > 0 for q in snapshot.positions.values()) >= limit.max_positions:
                raise Halt("grant position count")
            if snapshot.cash - notional < nav * dec(limit.min_cash_fraction):
                raise Halt("grant cash reserve")

    def halt_entries(self, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise Halt("halt reason required")
        with self.state.db:
            self.state.db.execute(
                "INSERT OR IGNORE INTO emergency_halt VALUES(1,?,?)", (reason, self.clock())
            )


def grant_context(root, adapter, config, risk, facts, selector):
    """Derive context from verified deployment bytes; callers cannot supply hash claims."""
    root = Path(root)
    return GrantContext(
        adapter.account_digest,
        "predeployment-policy-v1",
        deployment_hash(root),
        config.strategy_version,
        hashlib.sha256((root / "src/robinhood_agent/strategy.py").read_bytes()).hexdigest(),
        digest(asdict(config)),
        digest(asdict(risk)),
        adapter.contracts.hash,
        selector.hash,
        digest({k: asdict(v) for k, v in sorted(facts.items())}),
    )
