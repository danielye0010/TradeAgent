"""Standard US equity long-premium options only; no exercise or short-leg path."""

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlparse
from uuid import UUID
from zoneinfo import ZoneInfo

from .broker import data, rows, unique, utc_time
from .model import Halt, dec, timestamp_fresh
from .risk import check_state


@dataclass(frozen=True)
class OptionContract:
    identifier: str
    underlying: str
    kind: str
    strike: Decimal
    expiration: str
    multiplier: Decimal
    observed_at: float
    chain_id: str
    can_open: bool = True
    tick_above: Decimal = Decimal(".05")
    tick_below: Decimal = Decimal(".01")
    tick_cutoff: Decimal = Decimal("3")

    def validate(self):
        try:
            UUID(self.identifier)
            UUID(self.chain_id)
            date.fromisoformat(self.expiration)
        except (TypeError, ValueError) as exc:
            raise Halt("ambiguous option contract identity") from exc
        if (
            self.kind not in {"call", "put"}
            or not self.underlying
            or dec(self.strike) <= 0
            or dec(self.multiplier) != 100
            or min(dec(self.tick_above), dec(self.tick_below)) <= 0
            or dec(self.tick_cutoff) < 0
        ):
            raise Halt("nonstandard/ambiguous option contract")
        return self

    def dte(self, now):
        return (
            date.fromisoformat(self.expiration)
            - datetime.fromtimestamp(now, ZoneInfo("America/New_York")).date()
        ).days


@dataclass(frozen=True)
class OptionQuote:
    contract: OptionContract
    bid: Decimal
    ask: Decimal
    mark: Decimal
    asof: float
    bid_size: int
    ask_size: int
    volume: int
    open_interest: int
    implied_volatility: Decimal | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None


@dataclass(frozen=True)
class OptionIntent:
    contract: OptionContract
    side: str
    quantity: Decimal
    limit_price: Decimal
    effect: str = "open"
    asset: str = "option"

    @property
    def symbol(self):
        return self.contract.identifier

    @property
    def premium_at_risk(self):
        return self.quantity * self.limit_price * self.contract.multiplier

    def payload(self):
        return {
            "legs": [
                {
                    "option_id": self.symbol,
                    "side": self.side,
                    "position_effect": self.effect,
                    "ratio_quantity": 1,
                }
            ],
            "direction": "debit" if self.side == "buy" else "credit",
            "type": "limit",
            "quantity": str(self.quantity),
            "price": str(self.limit_price),
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
        }


def check_option_order(i, s, c, r, now, baseline, fees=0):
    c.validate()
    check_state(s, r, now)
    k = i.contract.validate()
    if (
        not s.regular_session
        or k.underlying not in c.allowed_symbols
        or s.tradable.get(k.underlying) is not True
    ):
        raise Halt("option underlying/session not eligible")
    if s.option_level not in {"option_level_2", "option_level_3"}:
        raise Halt("options approval unavailable")
    if (i.side, i.effect) not in {("buy", "open"), ("sell", "close")}:
        raise Halt("long premium only; short opening/undefined risk prohibited")
    if not r.min_option_dte <= k.dte(now) <= r.max_option_dte:
        raise Halt("option DTE/exercise exposure gate")
    q = s.option_quotes.get(k.identifier)

    def static(contract):
        return {key: value for key, value in asdict(contract).items() if key != "observed_at"}

    if q is None or static(q.contract) != static(k) or not k.can_open:
        raise Halt("missing/ambiguous option chain or contract")
    if any(
        not timestamp_fresh(v, now, r.max_data_age_seconds)
        for v in (q.asof, q.contract.observed_at, k.observed_at)
    ):
        raise Halt("stale option chain/quote")
    if min(dec(q.bid), dec(q.mark)) <= 0 or dec(q.ask) < dec(q.bid):
        raise Halt("invalid option book")
    if (q.ask - q.bid) / q.mark > dec(r.max_option_spread_fraction):
        raise Halt("option spread limit")
    if (
        min(q.bid_size, q.ask_size) < max(r.min_option_quote_size, dec(i.quantity))
        or q.volume < r.min_option_volume
        or q.open_interest < r.min_option_open_interest
    ):
        raise Halt("option liquidity limit")
    if (
        dec(i.quantity) <= 0
        or i.quantity != i.quantity.to_integral_value()
        or dec(i.limit_price) <= 0
    ):
        raise Halt("invalid whole-contract quantity/premium")
    tick = k.tick_above if i.limit_price >= k.tick_cutoff else k.tick_below
    if i.limit_price % tick:
        raise Halt("option minimum price increment")
    quote_price = q.ask if i.side == "buy" else q.bid
    if abs(i.limit_price - quote_price) / q.mark > dec(r.review_price_tolerance_fraction):
        raise Halt("option price changed")
    fees = dec(fees)
    if fees < 0:
        raise Halt("invalid option fees")
    premium = i.premium_at_risk + fees
    baseline = dec(baseline)
    if baseline <= 0 or (baseline - s.nav) / baseline >= dec(r.daily_loss_halt_fraction):
        raise Halt("daily loss halt")
    if s.daily_turnover + premium > s.nav * dec(r.max_daily_turnover_fraction):
        raise Halt("daily turnover limit")
    if i.side == "sell":
        if i.quantity > min(
            dec(s.options.get(i.symbol, 0)), dec(s.option_available.get(i.symbol, 0))
        ):
            raise Halt("option sell would short/use reserved contracts")
        return
    existing = sum(dec(v) for v in s.option_cost_basis.values())
    single = dec(s.option_cost_basis.get(i.symbol, 0)) + premium
    if single > s.nav * dec(r.max_option_premium_fraction):
        raise Halt("option premium-at-risk limit")
    if existing + premium > s.nav * dec(r.max_total_option_premium_fraction):
        raise Halt("total option premium-at-risk limit")
    if not s.options.get(i.symbol) and len(s.options) >= r.max_option_positions:
        raise Halt("option position count limit")
    if premium > s.nav * dec(r.max_new_exposure_fraction):
        raise Halt("new exposure limit")
    underlying_exposure = dec(s.positions.get(k.underlying, 0)) * s.prices[k.underlying]
    if underlying_exposure + single > s.nav * dec(r.max_position_fraction):
        raise Halt("underlying concentration limit")
    if premium > min(s.cash, s.buying_power) or s.cash - premium < s.nav * dec(r.min_cash_fraction):
        raise Halt("option cash reserve/buying power limit")


def normalize_order(row, asset="equity"):
    if asset == "equity":
        required = (
            "id",
            "symbol",
            "side",
            "type",
            "state",
            "quantity",
            "cumulative_quantity",
            "price",
        )
        result = {k: row.get(k) for k in required}
        result["ref_id"] = row.get("ref_id")
        fills = rows(row.get("executions"), "executions")
    else:
        legs = rows(row.get("legs"), "option legs")
        if len(legs) != 1 or legs[0].get("ratio_quantity") != 1 or not legs[0].get("option_id"):
            raise Halt("unsupported/ambiguous multileg option order")
        leg = legs[0]
        if (leg.get("side"), leg.get("position_effect")) not in {
            ("buy", "open"),
            ("sell", "close"),
        }:
            raise Halt("non-long-premium option order")
        result = {
            "id": row.get("id"),
            "symbol": leg["option_id"],
            "side": leg["side"],
            "state": row.get("state"),
            "type": row.get("type"),
            "quantity": row.get("quantity"),
            "cumulative_quantity": row.get("processed_quantity"),
            "price": row.get("price"),
            "ref_id": None,
            "position_effect": leg["position_effect"],
            "legs": legs,
        }
        fills = rows(leg.get("executions"), "option executions")
    if (
        not result.get("id")
        or result.get("side") not in {"buy", "sell"}
        or result.get("type") != "limit"
        or not result.get("state")
        or dec(result.get("quantity")) <= 0
        or dec(result.get("price")) <= 0
    ):
        raise Halt("malformed/unsupported broker order")
    cumulative = dec(result["cumulative_quantity"])
    if not 0 <= cumulative <= dec(result["quantity"]):
        raise Halt("invalid cumulative fill quantity")
    if result["state"] == "filled" and cumulative != dec(result["quantity"]):
        raise Halt("filled order has incomplete executions")
    if sum((dec(f["quantity"]) for f in fills), dec(0)) != cumulative:
        raise Halt("fill/order quantities do not reconcile")
    unique(fills, "id")
    for f in fills:
        if dec(f["quantity"]) <= 0 or dec(f["price"]) <= 0:
            raise Halt("invalid execution")
        utc_time(f["timestamp"])
    result.update(asset=asset, executions=fills, fees=row.get("fees"))
    return result


class OptionsReader:
    def __init__(self, broker):
        self.broker = broker

    def contract_quote(self, identifier, now):
        bridge = self.broker.bridge
        instruments = unique(
            rows(
                data(bridge.read("get_option_instruments", {"ids": identifier})).get("instruments"),
                "instruments",
            ),
            "id",
        )
        if set(instruments) != {identifier}:
            raise Halt("option contract lookup incomplete")
        inst = instruments[identifier]
        chains = unique(
            rows(
                data(bridge.read("get_option_chains", {"ids": inst["chain_id"]})).get("chains"),
                "chains",
            ),
            "id",
        )
        chain = chains.get(inst["chain_id"])
        if (
            not chain
            or inst.get("underlying_type") != "equity"
            or inst.get("state") != "active"
            or inst.get("tradability") != "tradable"
            or chain.get("can_open_position") is not True
            or (chain.get("cash_component") is not None and dec(chain["cash_component"]) != 0)
            or inst.get("expiration_date") not in (chain.get("expiration_dates") or [])
            or dec(chain.get("trade_value_multiplier")) != dec(inst.get("trade_value_multiplier"))
            or chain.get("symbol") != inst.get("chain_symbol")
            or len(rows(chain.get("underlying_instruments"), "underlyings")) != 1
        ):
            raise Halt("nonstandard/ambiguous option chain")
        underlying = chain["underlying_instruments"][0]
        if underlying.get("symbol") != chain["symbol"]:
            if underlying.get("symbol"):
                raise Halt("option underlying symbol mismatch")
            # Actual 1.6.2 responses can have a blank symbol and an internal URL.
            # NEVER follow that URL. Resolve its opaque UUID using the OFFICIAL
            # read-only symbol search, requiring one exact symbol/ID match.
            path = urlparse(underlying.get("instrument", "")).path.rstrip("/").split("/")
            if len(path) < 2 or path[-2] != "instruments":
                raise Halt("ambiguous option underlying identifier")
            try:
                identifier_uuid = str(UUID(path[-1]))
            except ValueError as exc:
                raise Halt("invalid option underlying identifier") from exc
            matches = [
                x
                for x in rows(
                    data(
                        bridge.read(
                            "search",
                            {"query": chain["symbol"], "asset_type": "instrument", "limit": 20},
                        )
                    ).get("results"),
                    "search",
                )
                if x.get("symbol") == chain["symbol"]
            ]
            if len(matches) != 1 or matches[0].get("instrument_id") != identifier_uuid:
                raise Halt("official search does not verify option underlying")
        tick = inst["min_ticks"]
        contract = OptionContract(
            identifier,
            inst["chain_symbol"],
            inst["type"],
            dec(inst["strike_price"]),
            inst["expiration_date"],
            dec(inst["trade_value_multiplier"]),
            now,
            inst["chain_id"],
            tick_above=dec(tick["above_tick"]),
            tick_below=dec(tick["below_tick"]),
            tick_cutoff=dec(tick["cutoff_price"]),
        ).validate()
        results = rows(
            data(bridge.read("get_option_quotes", {"instrument_ids": [identifier]})).get("results"),
            "quotes",
        )
        if (
            len(results) != 1
            or not results[0].get("quote")
            or results[0]["quote"].get("instrument_id") != identifier
        ):
            raise Halt("option quote lookup incomplete")
        q = results[0]["quote"]
        return OptionQuote(
            contract,
            dec(q["bid_price"]),
            dec(q["ask_price"]),
            dec(q["mark_price"]),
            utc_time(q["updated_at"]),
            q["bid_size"],
            q["ask_size"],
            q["volume"],
            q["open_interest"],
            **{
                key: dec(q[key]) if q.get(key) is not None else None
                for key in ("implied_volatility", "delta", "gamma", "theta", "vega")
            },
        )

    def enrich(self, snapshot):
        b, s = self.broker, snapshot
        positions = unique(b.paged("get_option_positions", "positions"), "option_id")
        order_rows = unique(b.paged("get_option_orders", "orders"), "id")
        for identifier, row in positions.items():
            quantity = dec(row["quantity"])
            if row.get("type") not in {"long", "empty"} or quantity < 0:
                raise Halt("short/unknown options position")
            pending_buy, pending_sell = (
                dec(row["pending_buy_quantity"]),
                dec(row["pending_sell_quantity"]),
            )
            if min(pending_buy, pending_sell) < 0 or pending_sell > quantity:
                raise Halt("invalid option reservation quantities")
            if any(
                dec(row[k]) != 0
                for k in (
                    "pending_exercise_quantity",
                    "pending_assignment_quantity",
                    "pending_expiration_quantity",
                )
            ):
                raise Halt("pending option lifecycle event requires reconciliation")
            if quantity:
                q = self.contract_quote(identifier, s.asof)
                if dec(row["trade_value_multiplier"]) != q.contract.multiplier:
                    raise Halt("option multiplier mismatch")
                s.options[identifier] = quantity
                s.option_available[identifier] = quantity - pending_sell
                s.option_quotes[identifier] = q
                s.option_cost_basis[identifier] = quantity * dec(
                    row["average_price"]
                )  # Schema: USD per contract.
        for row in order_rows.values():
            order = normalize_order(row, "option")
            s.orders.append(order)
            if order["executions"]:
                # Principal is known; final options fees are NOT in current order
                # schemas. Keep uncertainty explicit; never invent a zero live fee.
                multiplier = dec(row["trade_value_multiplier"])
                if multiplier != 100:
                    raise Halt("unsupported option execution multiplier")
                for fill in order["executions"]:
                    quantity, price = dec(fill["quantity"]), dec(fill["price"])
                    stamp = utc_time(fill["timestamp"])
                    if stamp > s.asof:
                        raise Halt("future option execution")
                    principal = quantity * price * multiplier
                    s.fills.append(
                        {
                            "id": fill["id"],
                            "symbol": order["symbol"],
                            "side": order["side"],
                            "quantity": fill["quantity"],
                            "price": fill["price"],
                            "cash_delta": str(principal * (1 if order["side"] == "sell" else -1)),
                            "timestamp": stamp,
                            "fees_known": False,
                        }
                    )
                    if (
                        datetime.fromtimestamp(stamp, ZoneInfo("America/New_York")).date()
                        == datetime.fromtimestamp(s.asof, ZoneInfo("America/New_York")).date()
                    ):
                        s.daily_turnover += principal
        unique(s.orders, "id")
        return s
