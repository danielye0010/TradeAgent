"""The execution runner's deterministic, fail-closed risk authority."""

from .account_policy import POLICY
from .model import (
    MAX_FUTURE_SKEW_SECONDS,
    Config,
    Halt,
    Intent,
    Risk,
    Snapshot,
    dec,
    timestamp_fresh,
)

TERMINAL = {
    "filled",
    "cancelled",
    "canceled",
    "rejected",
    "failed",
    "expired",
    "voided",
    "partially_filled_rest_cancelled",
    "locate_failed",
}


def check_state(s: Snapshot, r: Risk, now: float):
    r.validate()
    account_ok = (
        s.account_type in {"cash", "limited_margin"}
        and s.agentic_eligible is True
        and s.account_policy == POLICY
    )
    if not s.valid or not s.reconciled or not account_ok:
        raise Halt("uncertain/unreconciled state or non-cash account")
    if not s.accounting_verified:
        raise Halt("unverified cash-flow accounting; manual rebaseline required")
    if not timestamp_fresh(s.asof, now, r.max_data_age_seconds):
        raise Halt("stale account state")
    if dec(s.nav) <= 0 or dec(s.cash) < 0 or dec(s.buying_power) < 0:
        raise Halt("invalid balances")
    if any(dec(q) < 0 for q in s.positions.values()):
        raise Halt("short position detected")
    if s.high_water_nav is not None:
        peak = dec(s.high_water_nav)
        if peak <= 0 or (peak - s.nav) / peak >= dec(r.max_drawdown_fraction):
            raise Halt("portfolio drawdown halt")
    if any(o.get("state") not in TERMINAL for o in s.orders):
        raise Halt("open/unknown broker orders require reconciliation")
    for symbol in s.positions.keys() | s.tradable.keys():
        if symbol not in s.prices or dec(s.prices[symbol]) <= 0:
            raise Halt("missing position or universe valuation")
        if not timestamp_fresh(
            s.quote_times.get(symbol),
            now,
            r.max_data_age_seconds,
            future_skew=MAX_FUTURE_SKEW_SECONDS,
        ):
            raise Halt("stale market data")
    if set(s.options) - set(s.option_quotes) or set(s.options) - set(s.option_cost_basis):
        raise Halt("unknown option position/valuation/cost basis")
    for k in s.options:
        quote = s.option_quotes[k]
        quote.contract.validate()
        if (
            not timestamp_fresh(quote.asof, now, r.max_data_age_seconds)
            or not timestamp_fresh(quote.contract.observed_at, now, r.max_data_age_seconds)
            or dec(quote.mark) <= 0
            or dec(s.option_cost_basis[k]) < 0
        ):
            raise Halt("stale/invalid option position valuation")
    option_value = sum(
        dec(q) * s.option_quotes[k].mark * s.option_quotes[k].contract.multiplier
        for k, q in s.options.items()
    )
    if any(dec(q) < 0 for q in s.options.values()):
        raise Halt("short option position detected")
    valued = sum(s.positions[k] * s.prices[k] for k in s.positions) + option_value
    if abs(s.nav - s.cash - valued) > s.nav * dec("0.01"):
        raise Halt("portfolio/position valuation does not reconcile")


def check_order(
    i: Intent,
    s: Snapshot,
    c: Config,
    r: Risk,
    now: float,
    day_start_nav,
    cycle_exposure=0,
    cycle_turnover=0,
):
    c.validate()
    check_state(s, r, now)
    if not s.regular_session:
        raise Halt("regular session unavailable; no after-hours orders")
    if i.asset not in {"equity", "etf"} or i.symbol not in c.allowed_symbols:
        raise Halt("asset or symbol not allowed")
    i.validate()
    fractional = i.dollar_amount is not None or i.quantity != i.quantity.to_integral_value()
    if fractional and s.fractional_tradable.get(i.symbol) is not True:
        raise Halt("fractional trading eligibility unavailable")
    if s.tradable.get(i.symbol) is not True:
        raise Halt("symbol not tradable")
    book = s.liquidity.get(i.symbol)
    if (
        not isinstance(book, dict)
        or not timestamp_fresh(
            book.get("asof"),
            now,
            r.max_data_age_seconds,
            future_skew=MAX_FUTURE_SKEW_SECONDS,
        )
        or min(dec(book.get("bid_size")), dec(book.get("ask_size"))) < r.min_equity_depth_shares
        or dec(book.get("ask_size" if i.side == "buy" else "bid_size"))
        < (i.quantity if i.quantity is not None else i.dollar_amount / dec(s.bids.get(i.symbol)))
    ):
        raise Halt("equity liquidity/depth gate")
    ask, bid = dec(s.asks.get(i.symbol)), dec(s.bids.get(i.symbol))
    if bid <= 0 or ask < bid:
        raise Halt("invalid bid/ask book")
    if any(
        not timestamp_fresh(
            t.get(i.symbol), now, r.max_data_age_seconds, future_skew=MAX_FUTURE_SKEW_SECONDS
        )
        for t in (s.bid_times, s.ask_times)
    ):
        raise Halt("stale bid/ask book")
    if (ask - bid) / s.prices[i.symbol] > dec(r.max_spread_fraction):
        raise Halt("spread limit")
    if i.order_type == "limit" and abs(
        i.limit_price - (ask if i.side == "buy" else bid)
    ) / s.prices[i.symbol] > dec(r.review_price_tolerance_fraction):
        raise Halt("order price differs from current quote")
    if dec(cycle_exposure) < 0 or dec(cycle_turnover) < 0 or dec(s.daily_turnover) < 0:
        raise Halt("invalid risk accounting")
    baseline = dec(day_start_nav)
    if baseline <= 0 or (baseline - s.nav) / baseline >= dec(r.daily_loss_halt_fraction):
        raise Halt("daily loss halt")
    notional = i.risk_notional(s, r)
    if fractional and i.side == "buy" and notional < 1:
        raise Halt("fractional entry minimum is $1")
    if s.daily_turnover + dec(cycle_turnover) + notional > s.nav * dec(
        r.max_daily_turnover_fraction
    ):
        raise Halt("daily turnover limit")
    held = s.positions.get(i.symbol, dec(0))
    if i.side == "sell":
        if i.quantity > min(held, s.available.get(i.symbol, dec(0))):
            raise Halt("sell would short or use reserved shares")
        return
    if notional + dec(cycle_exposure) > s.nav * dec(r.max_new_exposure_fraction):
        raise Halt("new exposure limit")
    if (held * s.prices[i.symbol] + notional) > s.nav * dec(r.max_position_fraction):
        raise Halt("concentration limit")
    if held == 0 and sum(q > 0 for q in s.positions.values()) >= r.max_positions:
        raise Halt("position count limit")
    if notional > min(s.cash, s.buying_power):
        raise Halt("insufficient unleveraged buying power")
    if s.cash - notional < s.nav * dec(r.min_cash_fraction):
        raise Halt("cash reserve limit")
