"""Extracted upstream component; see THIRD_PARTY.md and licenses/."""
from __future__ import annotations
from typing import Optional


def score_trend(ind: dict) -> tuple[int, str]:
    c = ind["close"]
    e20, e50, e200 = ind["ema20"], ind["ema50"], ind["ema200"]
    s200 = ind["ema200_slope"]
    pts, bits = 0, []
    if e20 is not None:
        if c > e20: pts += 1; bits.append("price>EMA20")
        else: pts -= 1; bits.append("price<EMA20")
    if e20 is not None and e50 is not None:
        if e20 > e50: pts += 1; bits.append("EMA20>EMA50")
        else: pts -= 1; bits.append("EMA20<EMA50")
    if e50 is not None and e200 is not None:
        if e50 > e200: pts += 1; bits.append("EMA50>EMA200")
        else: pts -= 1; bits.append("EMA50<EMA200")
    if s200 is not None:
        if s200 > 0: pts += 1; bits.append("EMA200↑")
        else: pts -= 1; bits.append("EMA200↓")
    score = 2 if pts >= 3 else 1 if pts >= 1 else 0 if pts == 0 else -1 if pts >= -2 else -2
    return score, ", ".join(bits)
