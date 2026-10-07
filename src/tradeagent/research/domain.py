"""Immutable, account-free research records with explicit information timestamps."""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("expected finite number")
    return float(value)


def timestamp(value):
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamps require timezone")
        return parsed.timestamp()
    return finite(value)


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(freeze(v) for v in value)
    return value


@dataclass(frozen=True)
class Payload:
    encoded: str

    def __post_init__(self):
        object.__setattr__(self, "encoded", canonical(json.loads(self.encoded)))

    @classmethod
    def of(cls, value):
        return cls(canonical(value))

    @property
    def value(self):
        return freeze(json.loads(self.encoded))

    def plain(self):
        return json.loads(self.encoded)


@dataclass(frozen=True)
class Bar:
    symbol: str
    start: float
    end: float
    available_at: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0

    def __post_init__(self):
        for name in ("start", "end", "available_at", "open", "high", "low", "close", "volume"):
            finite(getattr(self, name))
        if not self.symbol or not self.start < self.end <= self.available_at:
            raise ValueError("invalid bar identity/timestamps")
        if (
            not 0
            < self.low
            <= min(self.open, self.close)
            <= max(self.open, self.close)
            <= self.high
        ):
            raise ValueError("invalid OHLC")
        if self.volume < 0:
            raise ValueError("negative volume")

    @classmethod
    def from_dict(cls, item):
        value = dict(item)
        for key in ("start", "end", "available_at"):
            value[key] = timestamp(value[key])
        return cls(**value)


@dataclass(frozen=True)
class OptionBook:
    contract_id: str
    underlying: str
    kind: str
    expiration: str
    strike: float
    asof: float
    available_at: float
    bid: float
    ask: float
    mark: float | None = None
    multiplier: int = 100
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    volume: int | None = None
    open_interest: int | None = None

    def __post_init__(self):
        for key in ("strike", "asof", "available_at", "bid", "ask"):
            finite(getattr(self, key))
        for key in ("mark", "iv", "delta", "gamma", "theta", "vega"):
            if getattr(self, key) is not None:
                finite(getattr(self, key))
        expiry = datetime.fromisoformat(self.expiration)
        if (
            not self.contract_id
            or not self.underlying
            or self.kind not in {"call", "put"}
            or not 0 <= self.bid <= self.ask
            or self.ask <= 0
            or self.strike <= 0
            or self.asof > self.available_at
            or self.multiplier != 100
            or expiry.date() < datetime.fromtimestamp(self.asof, timezone.utc).date()
        ):
            raise ValueError("invalid option book")

    @classmethod
    def from_dict(cls, item):
        value = dict(item)
        for key in ("asof", "available_at"):
            value[key] = timestamp(value[key])
        return cls(**value)


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    decision_time: float
    source: str
    evidence_kind: str
    benchmark: str
    bars: tuple[Bar, ...]
    benchmark_bars: tuple[Bar, ...]
    bid: float
    ask: float
    quote_time: float
    quote_available_at: float
    session_open: float | None = None
    previous_close: float | None = None
    options: tuple[OptionBook, ...] = ()
    session_open_time: float | None = None
    previous_close_time: float | None = None

    def __post_init__(self):
        for key in ("bars", "benchmark_bars", "options"):
            object.__setattr__(self, key, tuple(getattr(self, key)))
        for key in ("decision_time", "bid", "ask", "quote_time", "quote_available_at"):
            finite(getattr(self, key))
        if (
            not self.source
            or not self.symbol
            or self.symbol == self.benchmark
            or self.evidence_kind not in {"prospective", "synthetic", "replay"}
            or not 0 < self.bid <= self.ask
            or not self.quote_time <= self.quote_available_at <= self.decision_time
            or self.decision_time - self.quote_time > 120
        ):
            raise ValueError("invalid snapshot or stale/future quote")
        for key in ("session_open", "previous_close"):
            value = getattr(self, key)
            if value is not None and finite(value) <= 0:
                raise ValueError("invalid session reference")
            reference_time = getattr(self, key + "_time")
            if value is not None and (
                reference_time is None or timestamp(reference_time) > self.decision_time
            ):
                raise ValueError("session references need a known, prior timestamp")
        if self.previous_close_time is not None and self.session_open_time is not None:
            if self.previous_close_time >= self.session_open_time:
                raise ValueError("previous close must precede session open")
        for bars, symbol in ((self.bars, self.symbol), (self.benchmark_bars, self.benchmark)):
            if not bars or any(
                b.symbol != symbol or b.available_at > self.decision_time for b in bars
            ):
                raise ValueError("future/unknown information in decision snapshot")
            if any(a.end > b.start for a, b in zip(bars, bars[1:], strict=False)):
                raise ValueError("unordered or overlapping decision bars")
            if bars[-1].end != self.decision_time:
                raise ValueError("decision requires completed bars aligned with current quote")
        if len({q.contract_id for q in self.options}) != len(self.options):
            raise ValueError("duplicate option quote")
        if any(
            q.underlying != self.symbol
            or q.available_at > self.decision_time
            or self.decision_time - q.asof > 120
            for q in self.options
        ):
            raise ValueError("future/stale option snapshot")

    @classmethod
    def from_dict(cls, item):
        value = dict(item)
        for key in ("decision_time", "quote_time", "quote_available_at"):
            value[key] = timestamp(value[key])
        for key in ("session_open_time", "previous_close_time"):
            if value.get(key) is not None:
                value[key] = timestamp(value[key])
        value["bars"] = tuple(Bar.from_dict(b) for b in value["bars"])
        value["benchmark_bars"] = tuple(Bar.from_dict(b) for b in value["benchmark_bars"])
        value["options"] = tuple(OptionBook.from_dict(q) for q in value.get("options", []))
        return cls(**value)


@dataclass(frozen=True)
class Prediction:
    prediction_id: str
    strategy_id: str
    strategy_version: str
    snapshot_id: str
    symbol: str
    decision_time: float
    horizon: int
    direction: int
    expected_return: float
    confidence: float
    features: Payload
    context: Payload
    distribution: Payload
    created_at: float
    model_version: str = "prediction-v1"

    def __post_init__(self):
        for key in ("decision_time", "expected_return", "confidence", "created_at"):
            finite(getattr(self, key))
        if (
            type(self.horizon) is not int
            or self.horizon <= 0
            or type(self.direction) is not int
            or self.direction not in {-1, 0, 1}
            or not 0 <= self.confidence <= 1
            or not self.decision_time <= self.created_at < self.decision_time + self.horizon
            or self.expected_return * self.direction < 0
            or (self.direction == 0 and self.expected_return != 0)
        ):
            raise ValueError("invalid prediction")
        if not all((self.prediction_id, self.strategy_id, self.strategy_version, self.snapshot_id)):
            raise ValueError("missing prediction identity")
