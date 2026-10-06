"""Official MCP read adapter; full pagination and explicit normalization."""

import math
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from .account_policy import capital, eligible
from .calendar import regular_session, session_bounds
from .model import MAX_FUTURE_SKEW_SECONDS, Halt, Snapshot, dec, digest, timestamp_fresh


def utc_time(value):
    if not isinstance(value, str):
        raise Halt("missing broker timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("naive timestamp")
        result = dt.timestamp()
        if not math.isfinite(result):
            raise ValueError("non-finite timestamp")
        return result
    except (ValueError, OverflowError) as exc:
        raise Halt("invalid broker timestamp") from exc


def data(response):
    payload = response.get("data")
    if not isinstance(payload, dict):
        raise Halt("missing broker data envelope")
    return payload


def rows(value, label):
    # The server explicitly uses null to represent an empty collection.
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, dict) for v in value):
        raise Halt(f"malformed {label} collection")
    return value


def unique(values, key):
    result = {}
    for value in values:
        identity = value.get(key)
        if not isinstance(identity, str) or not identity or identity in result:
            raise Halt("missing/duplicate broker record identity")
        result[identity] = value
    return result


class Broker:
    """Account numbers stay in memory; persisted account keys are SHA-256 digests."""

    def __init__(self, bridge, config, risk, options_enabled=False):
        self.bridge, self.config, self.risk = bridge, config, risk
        self.account = None
        self.account_scope = []
        self.options_enabled = options_enabled

    def accounts(self):
        accounts = rows(data(self.bridge.read("get_accounts")).get("accounts"), "accounts")
        unique(accounts, "account_number")
        self.account_scope = [
            {
                "account_key": digest(a["account_number"])[:16],
                "type": a.get("type"),
                "brokerage_account_type": a.get("brokerage_account_type"),
                "state": a.get("state"),
                "accessible_to_this_agent": a.get("agentic_allowed") is True,
            }
            for a in accounts
        ]
        choices = [a for a in accounts if a.get("agentic_allowed") is True]
        if len(choices) != 1:
            raise Halt("exactly one account accessible to this agent is required")
        selected = choices[0]
        if (
            selected.get("state") != "active"
            or selected.get("deactivated") is not False
            or selected.get("permanently_deactivated") is not False
        ):
            raise Halt("agent account inactive or uncertain")
        if self.account and self.account["account_number"] != selected["account_number"]:
            raise Halt("agent account scope changed during run")
        self.account = selected
        return self.account_scope

    def paged(self, tool, collection):
        output, cursors, cursor = [], set(), None
        for _ in range(100):
            args = {"account_number": self.account["account_number"]}
            if cursor:
                args["cursor"] = cursor
            payload = data(self.bridge.read(tool, args))
            if collection not in payload:
                raise Halt("missing paginated broker collection")
            output.extend(rows(payload[collection], collection))
            cursor = payload.get("next", "")
            if not cursor:
                return output
            if not isinstance(cursor, str) or cursor in cursors:
                raise Halt("invalid/repeated broker pagination cursor")
            cursors.add(cursor)
        raise Halt("broker pagination exceeded safety bound; state incomplete")

    def snapshot(self, now=None):
        observed = datetime.now(timezone.utc).timestamp() if now is None else now
        self.accounts()
        p = data(
            self.bridge.read("get_portfolio", {"account_number": self.account["account_number"]})
        )
        if p.get("currency") != "USD" or p.get("buying_power", {}).get("display_currency") != "USD":
            raise Halt("unsupported/missing portfolio currency or buying power")
        unsupported = (
            "options_value",
            "futures_value",
            "event_contracts_value",
            "crypto_value",
            "mutual_funds_value",
            "fixed_income_value",
            "pending_deposits",
        )
        if any(
            dec(p.get(k)) != 0
            for k in unsupported
            if k != "options_value" or not self.options_enabled
        ):
            raise Halt("unsupported holdings or unsettled deposit in account")
        pos_rows = self.paged("get_equity_positions", "positions")
        pos = unique(pos_rows, "symbol")
        positions, available = {}, {}
        for symbol, row in pos.items():
            qty, sellable = dec(row.get("quantity")), dec(row.get("shares_available_for_sells"))
            if (
                qty < 0
                or sellable < 0
                or sellable > qty
                or row.get("type") not in {"long", "empty"}
            ):
                raise Halt("invalid, short, boxed or uncertain position")
            if dec(row.get("shares_pending_from_options_events")) != 0:
                raise Halt("pending option-related shares require operator reconciliation")
            if qty:
                positions[symbol], available[symbol] = qty, sellable
        raw_orders = self.paged("get_equity_orders", "orders")
        orders_by_id = unique(raw_orders, "id")
        orders, fills, ledger, turnover = [], set(), [], Decimal(0)
        day = datetime.fromtimestamp(observed, ZoneInfo("America/New_York")).date()
        for row in orders_by_id.values():
            order = {
                k: row.get(k)
                for k in (
                    "id",
                    "ref_id",
                    "symbol",
                    "side",
                    "state",
                    "type",
                    "quantity",
                    "cumulative_quantity",
                    "price",
                )
            }
            if not isinstance(order["state"], str):
                raise Halt("missing order lifecycle state")
            executed = Decimal(0)
            for fill in rows(row.get("executions"), "executions"):
                fid = fill.get("id")
                if not isinstance(fid, str) or fid in fills:
                    raise Halt("missing/duplicate fill identity")
                fills.add(fid)
                qty, price = dec(fill.get("quantity")), dec(fill.get("price"))
                if qty <= 0 or price <= 0:
                    raise Halt("invalid fill")
                executed += qty
                ts = utc_time(fill.get("timestamp"))
                if ts > observed:
                    raise Halt("future fill timestamp")
                ledger.append(
                    {
                        "id": fid,
                        "symbol": row.get("symbol"),
                        "quantity": str(qty),
                        "side": row.get("side"),
                        "price": str(price),
                        "cash_delta": str(
                            (1 if row.get("side") == "sell" else -1) * qty * price
                            - dec(fill.get("fees", "0"))
                        ),
                        "timestamp": ts,
                    }
                )
                if datetime.fromtimestamp(ts, ZoneInfo("America/New_York")).date() == day:
                    turnover += qty * price
            if executed != dec(row.get("cumulative_quantity")):
                raise Halt("execution details do not reconcile cumulative fills")
            if order["state"] == "filled" and executed != dec(row.get("quantity")):
                raise Halt("filled equity order has incomplete executions")
            # Production reconciliation needs the same fee/execution detail that
            # the simulator already exposes. Never discard it after normalization.
            order["executions"] = rows(row.get("executions"), "executions")
            order["fees"] = row.get("fees")
            if order["executions"]:
                fees = dec(order["fees"])
                execution_fees = [dec(fill.get("fees")) for fill in order["executions"]]
                if (
                    fees < 0
                    or any(fee < 0 for fee in execution_fees)
                    or sum(execution_fees) != fees
                ):
                    raise Halt("equity execution fees do not reconcile order fees")
            orders.append(order)
        symbols = sorted(set(self.config.allowed_symbols) | set(positions))
        if len(symbols) > 100:
            raise Halt("position universe exceeds supported snapshot bound")
        quotes = {}
        for offset in range(0, len(symbols), 20):
            response = data(
                self.bridge.read("get_equity_quotes", {"symbols": symbols[offset : offset + 20]})
            )
            for result in rows(response.get("results"), "quotes"):
                q = result.get("quote")
                if not isinstance(q, dict) or q.get("symbol") in quotes:
                    raise Halt("missing/duplicate quote")
                quotes[q["symbol"]] = q
        if set(quotes) != set(symbols):
            raise Halt("incomplete quote coverage")
        prices, quote_times, bids, asks, bid_times, ask_times = {}, {}, {}, {}, {}, {}
        for symbol, q in quotes.items():
            if q.get("state") != "active" or q.get("has_traded") is not True:
                raise Halt("inactive/untraded instrument")
            candidates = [
                (utc_time(q.get("venue_last_trade_time")), dec(q.get("last_trade_price")))
            ]
            if q.get("last_non_reg_trade_price") is not None:
                candidates.append(
                    (
                        utc_time(q.get("venue_last_non_reg_trade_time")),
                        dec(q["last_non_reg_trade_price"]),
                    )
                )
            quote_times[symbol], prices[symbol] = max(candidates, key=lambda v: v[0])
            bids[symbol], asks[symbol] = dec(q.get("bid_price")), dec(q.get("ask_price"))
            bid_times[symbol], ask_times[symbol] = (
                utc_time(q.get("venue_bid_time")),
                utc_time(q.get("venue_ask_time")),
            )
        tradability = data(
            self.bridge.read(
                "get_equity_tradability",
                {
                    "account_number": self.account["account_number"],
                    "symbols": self.config.allowed_symbols,
                },
            )
        )
        trade_rows = unique(rows(tradability.get("results"), "tradability"), "symbol")
        if set(trade_rows) != set(self.config.allowed_symbols):
            raise Halt("incomplete tradability coverage")
        tradable = {
            sym: row.get("tradeable") is True
            and row.get("state") == "active"
            and not row.get("internal_halt_reason")
            and not row.get("internal_halt_sessions")
            for sym, row in trade_rows.items()
        }
        policy = eligible(self.account)
        snapshot = Snapshot(
            account_key=digest(self.account["account_number"])[:16],
            asof=observed,
            nav=dec(p.get("total_value")),
            cash=dec(p.get("cash")),
            buying_power=capital(p),
            positions=positions,
            available=available,
            prices=prices,
            quote_times=quote_times,
            asks=asks,
            bids=bids,
            orders=sorted(orders, key=lambda o: o["id"]),
            daily_turnover=turnover,
            account_type=self.account.get("type", "unknown"),
            tradable=tradable,
            bid_times=bid_times,
            ask_times=ask_times,
            regular_session=False,
            agentic_eligible=True,
            account_policy=policy,
            option_level=self.account.get(
                "user_option_level", self.account.get("option_level", "")
            ),
            fills=ledger,
        )
        books = {}
        for offset in range(0, len(self.config.allowed_symbols), 4):
            response = data(
                self.bridge.read(
                    "get_equity_price_book",
                    {"symbols": self.config.allowed_symbols[offset : offset + 4]},
                )
            )
            if response.get("errors"):
                raise Halt("equity depth unavailable")
            for book in rows(response.get("books"), "books"):
                symbol = book.get("symbol")
                if symbol in books:
                    raise Halt("duplicate equity book")
                books[symbol] = book
        if set(books) != set(self.config.allowed_symbols):
            raise Halt("equity depth coverage incomplete")
        for symbol, book in books.items():
            bid_rows, ask_rows = rows(book.get("bids"), "bids"), rows(book.get("asks"), "asks")
            if not bid_rows or not ask_rows:
                raise Halt("empty equity book")
            best_bid = max(dec(b["price"]) for b in bid_rows)
            best_ask = min(dec(a["price"]) for a in ask_rows)
            if best_bid != snapshot.bids[symbol] or best_ask != snapshot.asks[symbol]:
                raise Halt("quote/depth disagreement; refresh required")
            if any(
                type(row.get("quantity")) is not int or row["quantity"] < 0
                for row in bid_rows + ask_rows
            ):
                raise Halt("invalid equity depth size")
            snapshot.liquidity[symbol] = {
                "asof": utc_time(book.get("updated_at")),
                "bid_size": sum(b["quantity"] for b in bid_rows if dec(b["price"]) == best_bid),
                "ask_size": sum(a["quantity"] for a in ask_rows if dec(a["price"]) == best_ask),
            }
        if self.options_enabled:
            from .options import OptionsReader

            OptionsReader(self).enrich(snapshot)
            option_value = sum(
                q * snapshot.option_quotes[k].mark * snapshot.option_quotes[k].contract.multiplier
                for k, q in snapshot.options.items()
            )
            if abs(dec(p.get("options_value")) - option_value) > max(
                dec(".01"), abs(snapshot.nav) * dec(".01")
            ):
                raise Halt("option/portfolio valuation mismatch")
        if abs(dec(p.get("equity_value")) - sum(positions[s] * prices[s] for s in positions)) > max(
            dec("0.01"), abs(snapshot.nav) * dec("0.01")
        ):
            raise Halt("equity positions do not reconcile portfolio equity value")
        # A market update produced while these reads were in flight is not future
        # data. Keep account asof at request start; validate returned market data
        # at completion, including the existing bounded provider clock allowance.
        validated = datetime.now(timezone.utc).timestamp() if now is None else now
        snapshot.regular_session = regular_session(validated) and all(
            timestamp_fresh(
                utc_time(quotes[s]["venue_last_trade_time"]),
                validated,
                self.risk.max_data_age_seconds,
                future_skew=MAX_FUTURE_SKEW_SECONDS,
            )
            for s in self.config.allowed_symbols
        )
        return snapshot

    def histories(self, now=None):
        now = (
            datetime.now(timezone.utc) if now is None else datetime.fromtimestamp(now, timezone.utc)
        )
        response = data(
            self.bridge.read(
                "get_equity_historicals",
                {
                    "symbols": self.config.allowed_symbols,
                    "start_time": (now - timedelta(days=730)).isoformat(),
                    "end_time": now.isoformat(),
                    "interval": "day",
                    "bounds": "regular",
                    "adjustment_type": "split",
                },
            )
        )
        if response.get("not_found"):
            raise Halt("historical symbol unresolved")
        by_symbol = unique(rows(response.get("results"), "historicals"), "symbol")
        if set(by_symbol) != set(self.config.allowed_symbols):
            raise Halt("incomplete historical universe")
        histories = {}
        local = now.astimezone(ZoneInfo("America/New_York"))
        for symbol, result in by_symbol.items():
            if result.get("interval") != "day" or result.get("bounds") != "regular":
                raise Halt("unexpected historical interval/bounds")
            bars, previous = [], -1
            for bar in rows(result.get("bars"), "bars"):
                ts = utc_time(bar.get("begins_at"))
                if ts <= previous or ts > now.timestamp():
                    raise Halt("out-of-order, duplicate or future historical bar")
                previous = ts
                if bar.get("interpolated") is True:
                    continue
                bar_day = datetime.fromtimestamp(ts, ZoneInfo("America/New_York")).date()
                utc_day = datetime.fromtimestamp(ts, timezone.utc).date()
                bounds = session_bounds(local.date())
                if local.date() in {bar_day, utc_day} and (
                    bounds is None or now.timestamp() < bounds[1]
                ):
                    # Native daily bars can use a midnight-UTC date label, which
                    # maps to yesterday in New York. Conservatively exclude
                    # either representation of today's unfinished daily candle.
                    continue  # Never calculate with an incomplete current daily bar.
                if bar.get("session") not in {"reg", ""}:
                    raise Halt("unexpected historical session")
                opening, high, low, closing = (
                    dec(bar.get(k + "_price")) for k in ("open", "high", "low", "close")
                )
                if low <= 0 or not low <= min(opening, closing) <= max(opening, closing) <= high:
                    raise Halt("invalid historical OHLC")
                bars.append({"begins_at": bar["begins_at"], "close_price": str(closing)})
            if (
                len(bars) < 205
                or not 0
                <= now.timestamp() - utc_time(bars[-1]["begins_at"])
                <= self.risk.max_history_age_seconds
            ):
                raise Halt("insufficient/stale completed historical bars")
            histories[symbol] = bars
        return histories

    def reconcile(self, initial):
        final = self.snapshot()
        fields = (
            "account_key",
            "account_type",
            "positions",
            "available",
            "cash",
            "buying_power",
            "orders",
        )
        before, after = asdict(initial), asdict(final)
        if any(before[k] != after[k] for k in fields):
            raise Halt(
                "account state changed during shadow cycle; operator reconciliation required"
            )
        return final

    def review(self, _):
        raise Halt("real broker review is disabled by the read-only connection")

    def submit(self, *_):
        raise Halt("real order submission is disabled by the read-only connection")

    def cancel(self, *_):
        raise Halt("real order cancellation is disabled by the read-only connection")
