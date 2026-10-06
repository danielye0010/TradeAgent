"""Strict configuration and Decimal-valued normalized broker state."""

import hashlib
import json
import math
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any


class Halt(ValueError):
    """Uncertain or unsafe state: no further execution."""


def dec(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise Halt("missing/invalid numeric value")
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise Halt("invalid numeric value") from exc
    if not result.is_finite():
        raise Halt("non-finite numeric value")
    return result


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class Config:
    version: str = "1"
    mode: str = "SHADOW"
    supervised_enabled: bool = False
    live_enabled: bool = False
    strategy_version: str = "oft3r-trend-test-v1"
    target_fraction: str = "0.05"
    lease_seconds: int = 1200
    request_timeout_seconds: int = 60
    state_dir: str = "data"
    allowed_symbols: list[str] = field(default_factory=lambda: ["SPY", "QQQ", "IWM"])

    def validate(self):
        if self.mode not in {"SHADOW", "SUPERVISED", "LIVE"}:
            raise Halt("invalid execution mode")
        if type(self.live_enabled) is not bool or type(self.supervised_enabled) is not bool:
            raise Halt("mode flags must be booleans")
        if self.live_enabled or self.mode == "LIVE":
            raise Halt("LIVE mode is disabled")
        if self.mode == "SUPERVISED" and not self.supervised_enabled:
            raise Halt("SUPERVISED needs explicit configuration enablement")
        if not 0 < dec(self.target_fraction) <= 1:
            raise Halt("invalid target fraction")
        if type(self.lease_seconds) is not int or self.lease_seconds < 60:
            raise Halt("invalid lease duration")
        if (
            type(self.request_timeout_seconds) is not int
            or not 1 <= self.request_timeout_seconds <= 120
        ):
            raise Halt("invalid request timeout")
        if (
            not isinstance(self.allowed_symbols, list)
            or not self.allowed_symbols
            or len(self.allowed_symbols) > 100
            or any(
                not isinstance(s, str)
                or not s.isascii()
                or len(s) > 10
                or not s.replace(".", "").isalpha()
                or s != s.upper()
                for s in self.allowed_symbols
            )
            or len(set(self.allowed_symbols)) != len(self.allowed_symbols)
        ):
            raise Halt("invalid allowed-symbol universe")
        if not self.version or not self.strategy_version or not self.state_dir:
            raise Halt("missing configuration identity")


@dataclass(frozen=True)
class Risk:
    version: str = "1"
    max_positions: int = 5
    max_position_fraction: str = "0.20"
    max_new_exposure_fraction: str = "0.10"
    min_cash_fraction: str = "0.20"
    daily_loss_halt_fraction: str = "0.02"
    max_daily_turnover_fraction: str = "0.20"
    max_data_age_seconds: int = 120
    max_history_age_seconds: int = 604800
    max_spread_fraction: str = "0.005"
    review_price_tolerance_fraction: str = "0.01"
    max_drawdown_fraction: str = "0.10"
    max_option_premium_fraction: str = "0.01"
    max_total_option_premium_fraction: str = "0.03"
    max_option_positions: int = 2
    max_option_spread_fraction: str = "0.10"
    min_option_quote_size: int = 1
    min_option_volume: int = 100
    min_option_open_interest: int = 100
    min_option_dte: int = 7
    max_option_dte: int = 90
    min_equity_depth_shares: int = 100

    def validate(self):
        if not isinstance(self.version, str) or not self.version:
            raise Halt("missing risk configuration identity")
        for name in (
            "max_position_fraction",
            "max_new_exposure_fraction",
            "min_cash_fraction",
            "daily_loss_halt_fraction",
            "max_daily_turnover_fraction",
            "max_spread_fraction",
            "review_price_tolerance_fraction",
            "max_drawdown_fraction",
            "max_option_premium_fraction",
            "max_total_option_premium_fraction",
            "max_option_spread_fraction",
        ):
            if not 0 < dec(getattr(self, name)) <= 1:
                raise Halt(f"invalid risk configuration: {name}")
        for name in (
            "max_positions",
            "max_data_age_seconds",
            "max_history_age_seconds",
            "max_option_positions",
            "min_option_quote_size",
            "min_option_volume",
            "min_option_open_interest",
            "min_option_dte",
            "max_option_dte",
            "min_equity_depth_shares",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise Halt(f"invalid risk configuration: {name}")
        if self.min_option_dte > self.max_option_dte:
            raise Halt("invalid option DTE range")


def load_config(path: Path, risk_path: Path):
    try:
        c, r = Config(**json.loads(path.read_text())), Risk(**json.loads(risk_path.read_text()))
        c.validate()
        r.validate()
        return c, r
    except (TypeError, OSError, json.JSONDecodeError) as exc:
        raise Halt("configuration missing or malformed") from exc


@dataclass
class Snapshot:
    account_key: str
    asof: float
    nav: Decimal
    cash: Decimal
    buying_power: Decimal
    positions: dict[str, Decimal]
    available: dict[str, Decimal]
    prices: dict[str, Decimal]
    quote_times: dict[str, float]
    asks: dict[str, Decimal]
    bids: dict[str, Decimal]
    orders: list[dict]
    daily_turnover: Decimal
    valid: bool = True
    reconciled: bool = True
    account_type: str = "cash"
    tradable: dict[str, bool] = field(default_factory=dict)
    bid_times: dict[str, float] = field(default_factory=dict)
    ask_times: dict[str, float] = field(default_factory=dict)
    regular_session: bool = False
    agentic_eligible: bool = False
    account_policy: str = ""
    option_level: str = ""
    options: dict = field(default_factory=dict)
    option_quotes: dict = field(default_factory=dict)
    option_cost_basis: dict = field(default_factory=dict)
    option_available: dict = field(default_factory=dict)
    fills: list[dict] = field(default_factory=list)
    cashflows: list[dict] | None = None
    high_water_nav: Decimal | None = None
    accounting_verified: bool = True
    liquidity: dict[str, dict] = field(default_factory=dict)


# Immutable allowance for the observed official equity price-book clock skew.
# Other timestamp categories retain zero future tolerance unless explicitly opted in.
MAX_FUTURE_SKEW_SECONDS = 0.25


def timestamp_fresh(value, now, age, *, future_skew=0):
    return (
        isinstance(future_skew, (float, int))
        and not isinstance(future_skew, bool)
        and math.isfinite(future_skew)
        and 0 <= future_skew <= MAX_FUTURE_SKEW_SECONDS
        and isinstance(value, (float, int))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and -future_skew <= now - value <= age
    )


@dataclass(frozen=True)
class Intent:
    symbol: str
    side: str
    quantity: Decimal
    limit_price: Decimal
    asset: str = "equity"

    def payload(self):
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "type": "limit",
            "limit_price": str(self.limit_price),
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
        }
