"""Deterministic features from completed, timestamp-checked bars only."""

import math
import statistics
from dataclasses import asdict

from .domain import identity


def features(snapshot):
    closes = [b.close for b in snapshot.bars]
    benchmark = [b.close for b in snapshot.benchmark_bars]
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]
    momentum = closes[-1] / closes[-2] - 1 if len(closes) >= 2 else None
    relative = None
    if (
        len(closes) >= 2
        and len(benchmark) >= 2
        and snapshot.bars[-2].end == snapshot.benchmark_bars[-2].end
    ):
        relative = momentum - (benchmark[-1] / benchmark[-2] - 1)
    mean = statistics.fmean(closes[-5:])
    gap = (
        snapshot.session_open / snapshot.previous_close - 1
        if snapshot.session_open is not None and snapshot.previous_close is not None
        else None
    )
    opening = closes[-1] / snapshot.session_open - 1 if snapshot.session_open is not None else None
    volatility = statistics.pstdev(returns) if len(returns) >= 2 else None
    regime = (
        "unavailable"
        if momentum is None
        else "up"
        if momentum > 0.001
        else "down"
        if momentum < -0.001
        else "flat"
    )
    return {
        "momentum": momentum,
        "relative_momentum": relative,
        "gap": gap,
        "opening_return": opening,
        "mean_deviation": closes[-1] / mean - 1,
        "volatility": volatility,
        "entry_close": closes[-1],
        "benchmark_close": benchmark[-1],
        "regime": regime,
        "information_cutoff": snapshot.decision_time,
        "feature_version": "completed-bars-v1",
        "minutes_since_open": (snapshot.decision_time - snapshot.session_open_time) / 60
        if snapshot.session_open_time is not None
        else None,
    }


def snapshot_identity(snapshot):
    return identity(asdict(snapshot))
