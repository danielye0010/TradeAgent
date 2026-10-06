"""Extracted upstream component; see THIRD_PARTY.md and licenses/."""
from __future__ import annotations
from typing import Optional


def ema_series(values: list[float], period: int) -> list[Optional[float]]:
    """
    EMA with None padding in the warmup. Seed = SMA of the first `period`
    observations (TradingView / ta-lib adjust=False convention).
    Returns list of same length as `values`.
    """
    n = len(values)
    out: list[Optional[float]] = [None] * n
    if n < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out
