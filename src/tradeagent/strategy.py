"""Account-free quantitative strategies: MarketSnapshot -> immutable Prediction."""

import hashlib
from pathlib import Path
from typing import Protocol

from .research.domain import Payload, Prediction, identity
from .research.market import features, snapshot_identity

FAMILIES = {
    "opening_momentum": ("opening_return", 1),
    "opening_reversal": ("opening_return", -1),
    "gap_continuation": ("gap", 1),
    "gap_reversal": ("gap", -1),
    "relative_strength": ("relative_momentum", 1),
    "mean_reversion": ("mean_deviation", -1),
    "null_control": (None, 0),
    "random_control": (None, 0),
}
DEFAULT_FAMILIES = (
    "opening_momentum",
    "opening_reversal",
    "gap_continuation",
    "relative_strength",
    "mean_reversion",
    "null_control",
    "random_control",
)
DEFAULT_PARAMS = {"threshold": 0.001, "scale": 0.5, "horizon": 3600, "max_expected": 0.03}


def implementation_hash():
    package = Path(__file__).parent
    files = (Path(__file__), package / "research/market.py", package / "research/domain.py")
    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()


def validate_params(params):
    from .research.domain import finite

    if set(params) != set(DEFAULT_PARAMS):
        raise ValueError("unknown/missing strategy parameter")
    for key in ("threshold", "scale", "max_expected"):
        finite(params[key])
    if (
        not 0 <= params["threshold"] <= 0.05
        or not 0 < params["scale"] <= 2
        or not 0 < params["max_expected"] <= 0.05
        or type(params["horizon"]) is not int
        or not 300 <= params["horizon"] <= 21600
    ):
        raise ValueError("strategy outside fixed research bounds")


class Strategy(Protocol):
    def predict(self, snapshot, created_at: float) -> Prediction: ...


class BaselineStrategy:
    def __init__(self, strategy_id, version, family, params):
        if family not in FAMILIES:
            raise ValueError("unsupported strategy family")
        validate_params(params)
        self.strategy_id, self.version, self.family = strategy_id, version, family
        self.params = Payload.of(params)

    def predict(self, snapshot, created_at):
        f = features(snapshot)
        params = self.params.value
        field, sign = FAMILIES[self.family]
        signal = f[field] * sign if field and f[field] is not None else None
        if self.family == "random_control":
            # Stable, unrelated to future returns; repeat invocations reproduce the control.
            signal = (
                0.004
                if int(identity([snapshot.symbol, snapshot.decision_time])[:8], 16) % 2
                else -0.004
            )
        active = signal is not None and abs(signal) > params["threshold"]
        if self.family.startswith(("opening_", "gap_")):
            active = (
                active
                and f["minutes_since_open"] is not None
                and 0 <= f["minutes_since_open"] <= 90
            )
        direction = (1 if signal > 0 else -1) if active else 0
        expected = (
            max(-params["max_expected"], min(params["max_expected"], signal * params["scale"]))
            if active
            else 0.0
        )
        confidence = min(0.7, 0.5 + abs(expected) * 5) if active else 0.0
        snap_id = snapshot_identity(snapshot)
        pred_id = identity(
            [
                self.strategy_id,
                self.version,
                snapshot.symbol,
                snapshot.decision_time,
                params["horizon"],
            ]
        )
        return Prediction(
            pred_id,
            self.strategy_id,
            self.version,
            snap_id,
            snapshot.symbol,
            snapshot.decision_time,
            params["horizon"],
            direction,
            expected,
            confidence,
            Payload.of(f),
            Payload.of(
                {
                    "regime": f["regime"],
                    "source": snapshot.source,
                    "benchmark": snapshot.benchmark,
                    "evidence_kind": snapshot.evidence_kind,
                }
            ),
            Payload.of(
                {
                    "kind": "point_estimate",
                    "mean": expected,
                    "sigma": f["volatility"],
                    "calibrated_probability": False,
                }
            ),
            created_at,
        )
