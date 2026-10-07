"""A narrow upstream trend pillar used only as an infrastructure test strategy."""

import math
from dataclasses import asdict, dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR
from typing import Protocol

from ..model import Config, Halt, Intent, Snapshot, dec
from ..vendor.indicators import ema_series
from ..vendor.trend import score_trend

STATUS = "TEST_STRATEGY_NOT_VALIDATED_FOR_LIVE_TRADING"


@dataclass(frozen=True)
class StrategySignal:
    symbol: str
    bar_time: str
    score: float
    direction: str
    strategy_id: str
    strategy_version: str
    confidence: float | None = None
    rank: int | None = None
    holding_horizon: str | None = None
    features: dict = field(default_factory=dict)
    status: str = STATUS

    def validate(self):
        from ..broker import utc_time

        utc_time(self.bar_time)
        if (
            not self.symbol
            or not self.strategy_id
            or not self.strategy_version
            or not math.isfinite(float(dec(self.score)))
            or self.direction not in {"long", "exit", "hold"}
            or (self.confidence is not None and not 0 <= dec(self.confidence) <= 1)
            or (self.rank is not None and (type(self.rank) is not int or self.rank < 1))
        ):
            raise Halt("invalid normalized strategy signal")
        return self


class Strategy(Protocol):
    def universe(self, config: Config) -> list[str]: ...
    def signals(self, histories: dict, config: Config) -> dict[str, StrategySignal]: ...
    def proposal(
        self, signal: StrategySignal, snapshot: Snapshot, config: Config
    ) -> Intent | None: ...


class TestTrendStrategy:
    def universe(self, config):
        return list(config.allowed_symbols)

    def signals(self, histories, config):
        result = {}
        for symbol in self.universe(config):
            bars = histories[symbol]
            raw = signal([b["close_price"] for b in bars], bars[-1]["begins_at"])
            result[symbol] = StrategySignal(
                symbol,
                raw["bar_time"],
                raw["score"],
                "long" if raw["score"] >= 1 else "exit" if raw["score"] <= -1 else "hold",
                "oft3r-trend-infrastructure-test",
                config.strategy_version,
                holding_horizon="daily-test",
                features=raw,
            ).validate()
        return result

    def proposal(self, sig, snapshot, config):
        sig.validate()
        if sig.strategy_version != config.strategy_version:
            raise Halt("strategy/config identity mismatch")
        return propose(sig.symbol, asdict(sig), snapshot, config)


def signal(closes: list, bar_time: str):
    prices = [float(dec(v)) for v in closes]
    if len(prices) < 205 or any(not math.isfinite(v) or v <= 0 for v in prices):
        raise Halt("insufficient/invalid daily historical bars")
    e20, e50, e200 = (ema_series(prices, n) for n in (20, 50, 200))
    ind = {
        "close": prices[-1],
        "ema20": e20[-1],
        "ema50": e50[-1],
        "ema200": e200[-1],
        "ema200_slope": e200[-1] - e200[-6],
    }
    score, reason = score_trend(ind)
    return {
        "score": score,
        "reason": reason,
        "bar_time": bar_time,
        "last_close": str(closes[-1]),
        "indicators": ind,
    }


def propose(symbol: str, sig: dict, s: Snapshot, c: Config):
    held = s.positions.get(symbol, dec(0))
    if sig["score"] <= -1 and held >= 1:
        return Intent(
            symbol,
            "sell",
            held.to_integral_value(rounding=ROUND_FLOOR),
            s.bids[symbol].quantize(dec("0.01"), rounding=ROUND_FLOOR),
        )
    if sig["score"] >= 1 and held == 0:
        price = s.asks[symbol].quantize(dec("0.01"), rounding=ROUND_CEILING)
        q = (s.nav * dec(c.target_fraction) / price).to_integral_value(rounding=ROUND_FLOOR)
        if q > 0:
            return Intent(symbol, "buy", q, price)
    return None
