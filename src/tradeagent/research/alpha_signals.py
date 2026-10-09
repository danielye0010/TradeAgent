"""Three frozen economic hypotheses using the existing immutable prediction interface."""

import math
import statistics

from .domain import Payload, Prediction, identity
from .market import features, snapshot_identity

FAMILIES = ("opening_continuation", "stabilized_reversal", "residual_strength")
# Numerical/feature contract, independent of formatting, comments and file location.
# Changes to any rule below require a new semantic protocol, never an added alias.
SEMANTICS = {
    "protocol": "alpha-v1",
    "features": "completed-bars-v1/opening=last-close/session-open-1;gap=session-open/previous-close-1",
    "threshold": {"floor": 0.001, "rv_multiplier": 0.5, "rv": "sqrt(sum(log(close/open)^2))"},
    "continuation": "abs(opening)>threshold and opening*gap>0 and opening*market>0",
    "reversal": "abs(opening)>threshold and opening*last<0 and opening*market_last<=0",
    "residual": "abs(opening-beta*market)>threshold; opening and gap required",
    "regime": "trend if opening*gap>0 and abs(market)>0.001 else range",
    "expected": "clip(signal*0.5,-0.03,0.03); uncalibrated point",
    "confidence": "0.5 active; 0 abstention",
    "beta": "prior-only; last20/min10; covariance/variance clipped[0,3]; default1",
    "parameters": "family,horizon,beta bound in prediction identity",
    "horizons": [1800, 2100, 3600, 3900, 7200, 7500],
}
ALPHA_VERSION = identity(SEMANTICS)[:16]
# Exact original implementation at 11f5b10 through 15315ef, with identical rules.
LEGACY_ALPHA_VERSIONS = {"228456be7e1ac282"}


def semantic_version(version):
    return ALPHA_VERSION if version in LEGACY_ALPHA_VERSIONS else version


def prior_beta(pairs):
    """Opening-return beta from PRIOR sessions only; caller supplies the cutoff."""
    if len(pairs) < 10:
        return 1.0
    pairs = pairs[-20:]
    x, y = zip(*pairs, strict=True)
    xm, ym = statistics.fmean(x), statistics.fmean(y)
    denominator = sum((v - xm) ** 2 for v in x)
    return (
        min(3.0, max(0.0, sum((a - xm) * (b - ym) for a, b in pairs) / denominator))
        if denominator
        else 1.0
    )


class AlphaStrategy:
    def __init__(self, family, horizon=3600, beta=1.0):
        if family not in FAMILIES or horizon not in (1800, 2100, 3600, 3900, 7200, 7500):
            raise ValueError("unknown frozen hypothesis/horizon")
        if not math.isfinite(beta) or not 0 <= beta <= 3:
            raise ValueError("invalid prior beta")
        self.family, self.horizon, self.beta = family, horizon, beta

    def predict(self, snapshot, created_at):
        f = features(snapshot)
        opening = f["opening_return"]
        market = snapshot.benchmark_bars[-1].close / snapshot.benchmark_bars[0].open - 1
        increments = [math.log(b.close / b.open) for b in snapshot.bars]
        rv = math.sqrt(sum(r * r for r in increments))
        threshold = max(0.001, 0.5 * rv)
        last = snapshot.bars[-1].close / snapshot.bars[-1].open - 1
        market_last = snapshot.benchmark_bars[-1].close / snapshot.benchmark_bars[-1].open - 1
        residual = opening - self.beta * market if opening is not None else None
        regime = (
            "trend"
            if opening is not None
            and f["gap"] is not None
            and opening * f["gap"] > 0
            and abs(market) > 0.001
            else "range"
        )
        signal = 0.0
        if opening is not None and f["gap"] is not None:
            if self.family == "opening_continuation":
                if abs(opening) > threshold and opening * f["gap"] > 0 and opening * market > 0:
                    signal = opening
            elif self.family == "stabilized_reversal":
                if abs(opening) > threshold and opening * last < 0 and opening * market_last <= 0:
                    signal = -opening
            elif abs(residual) > threshold:
                signal = residual
        direction = (1 if signal > 0 else -1) if signal else 0
        expected = max(-0.03, min(0.03, signal * 0.5))
        f.update(
            opening_market=market,
            opening_rv=rv,
            threshold=threshold,
            residual_opening=residual,
            beta=self.beta,
            regime=regime,
        )
        version = ALPHA_VERSION
        return Prediction(
            identity([self.family, version, snapshot_identity(snapshot), self.horizon, self.beta]),
            self.family,
            version,
            snapshot_identity(snapshot),
            snapshot.symbol,
            snapshot.decision_time,
            self.horizon,
            direction,
            expected,
            0.5 if direction else 0.0,
            Payload.of(f),
            Payload.of(
                {
                    "regime": regime,
                    "evidence_kind": snapshot.evidence_kind,
                    "source": snapshot.source,
                    "benchmark": snapshot.benchmark,
                }
            ),
            Payload.of(
                {
                    "kind": "uncalibrated_point",
                    "mean": expected,
                    "sigma": rv,
                    "calibrated_probability": False,
                }
            ),
            created_at,
        )
