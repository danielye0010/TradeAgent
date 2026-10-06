"""Deterministic, persistent local MCP simulator. No network or native Codex imports."""

import copy
import json
from dataclasses import replace
from datetime import datetime, timezone
from uuid import NAMESPACE_DNS, uuid5

from .account_policy import POLICY
from .broker import utc_time
from .calendar import regular_session
from .model import Halt, Snapshot, dec, digest
from .options import OptionContract, OptionQuote, normalize_order
from .schema import Contracts
from .state import State, dumps

SCENARIOS = frozenset(
    {
        "full_fill",
        "accepted",
        "rejected",
        "partial_fill",
        "cancel_rejected",
        "delayed_ack",
        "lost_ack",
        "timeout_before_ack",
        "timeout_after_acceptance",
        "crash_during_submission",
        "crash_after_acceptance",
        "stale_review",
    }
)


class SimClock:
    def __init__(self, value=1791208800):  # 2026-10-05 14:00 UTC, regular session.
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds

    def iso(self):
        return datetime.fromtimestamp(self.value, timezone.utc).isoformat()


def example_option(clock, underlying="SPY"):
    contract = OptionContract(
        "b9732dbe-a8c5-5002-9f64-15c5109f8d01",
        underlying,
        "call",
        dec(775),
        "2026-11-20",
        dec(100),
        clock(),
        "e30c23ee-aac1-5f03-9da2-e03e5d3c4fab",
    )
    return OptionQuote(contract, dec(".98"), dec("1.00"), dec(".99"), clock(), 100, 100, 1200, 3500)


def funded_snapshot(clock):
    quotes = {"SPY": dec("769.60"), "QQQ": dec("749.60"), "IWM": dec("281.50")}
    s = Snapshot(
        digest("SYNTHETIC_AGENTIC_ACCOUNT")[:16],
        clock(),
        dec(25000),
        dec(25000),
        dec(25000),
        {},
        {},
        quotes,
        {k: clock() for k in quotes},
        {k: v + dec(".05") for k, v in quotes.items()},
        {k: v - dec(".05") for k, v in quotes.items()},
        [],
        dec(0),
        account_type="limited_margin",
        agentic_eligible=True,
        account_policy=POLICY,
        tradable={k: True for k in quotes},
        bid_times={k: clock() for k in quotes},
        ask_times={k: clock() for k in quotes},
        regular_session=True,
        option_level="option_level_2",
        liquidity={k: {"asof": clock(), "bid_size": 10000, "ask_size": 10000} for k in quotes},
        cashflows=[],
    )
    option = example_option(clock)
    s.option_quotes[option.contract.identifier] = option
    return s


def schema_blank(schema):
    """Fill official required wire fields for fixtures; overrides below set semantics."""
    typ = schema.get("type")
    if isinstance(typ, list):
        typ = next((t for t in typ if t != "null"), None)
    if typ == "object":
        return {
            k: schema_blank(schema.get("properties", {})[k]) for k in schema.get("required", [])
        }
    if typ == "array":
        return []
    if typ == "string":
        return ""
    if typ == "boolean":
        return False
    if typ in {"integer", "number"}:
        return 0
    return None


class SimulatedMCP:
    is_simulation = True

    def __init__(self, directory, clock=None, scenario="full_fill", initial=None):
        if scenario not in SCENARIOS:
            raise Halt("unknown deterministic simulation scenario")
        self.store = State(directory)
        self.db = self.store.db
        self.clock = clock or SimClock()
        self.scenario = scenario
        self.initial = copy.deepcopy(initial or funded_snapshot(self.clock))
        self.account = {"account_number": "SYNTHETIC_AGENTIC_ACCOUNT"}
        self.calls = []
        self.contracts = Contracts()
        self.db.executescript("""CREATE TABLE IF NOT EXISTS simulated_orders(
            ref_id TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE, asset TEXT NOT NULL,
            arguments TEXT NOT NULL, response TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS simulated_meta(key TEXT PRIMARY KEY, payload TEXT NOT NULL);""")
        saved = self.db.execute("SELECT payload FROM simulated_meta WHERE key='initial'").fetchone()
        # Fixture identity is pinned on restart. Exact marks/capital must not be reset
        # under existing broker orders; fail rather than invent a new starting balance.
        pin = digest(
            {
                "cash": self.initial.cash,
                "nav": self.initial.nav,
                "positions": self.initial.positions,
                "options": self.initial.options,
                "prices": self.initial.prices,
            }
        )
        if saved and saved[0] != pin:
            raise Halt("simulation fixture changed across broker restart")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO simulated_meta VALUES('initial',?)", (pin,))

    def close(self):
        self.store.close()

    def _quote(self, symbol):
        s = self.snapshot()
        quote = schema_blank(
            self.contracts.tools["review_equity_order"]["outputSchema"]["properties"]["data"][
                "properties"
            ]["quote_data"]
        )
        quote.update(
            symbol=symbol,
            last_trade_price=str(s.prices[symbol]),
            venue_last_trade_time=self.clock.iso(),
            last_non_reg_trade_price=None,
            venue_last_non_reg_trade_time=None,
            adjusted_previous_close=str(s.prices[symbol]),
            previous_close=str(s.prices[symbol]),
            previous_close_date="2026-10-02",
            bid_price=str(s.bids[symbol]),
            ask_price=str(s.asks[symbol]),
            venue_bid_time=self.clock.iso(),
            venue_ask_time=self.clock.iso(),
            has_traded=True,
            state="active",
        )
        return quote

    def _option_quote(self, identifier):
        q = self.snapshot().option_quotes[identifier]
        quote = schema_blank(
            self.contracts.tools["review_option_order"]["outputSchema"]["properties"]["data"][
                "properties"
            ]["option_quotes"]["items"]
        )
        quote.update(
            instrument_id=identifier,
            ask_price=str(q.ask),
            bid_price=str(q.bid),
            mark_price=str(q.mark),
            adjusted_mark_price=str(q.mark),
            ask_size=q.ask_size,
            bid_size=q.bid_size,
            volume=q.volume,
            open_interest=q.open_interest,
            updated_at=self.clock.iso(),
        )
        return quote

    def invoke(self, name, args):
        self.contracts.validate(name, args)
        if args.get("account_number") != "SYNTHETIC_AGENTIC_ACCOUNT":
            raise Halt("simulator refuses any real account identity")
        self.calls.append({"tool": name, "arguments": copy.deepcopy(args), "simulation": True})
        if name.startswith("review_"):
            return self._review(name, args)
        if name.startswith("place_"):
            return self._place(name, args)
        if name.startswith("cancel_"):
            return self._cancel(name, args)
        raise Halt("simulator tool unavailable")

    def _review(self, name, args):
        if name == "review_equity_order":
            body = {k: args[k] for k in ("symbol", "side", "type", "quantity", "limit_price")}
            body.update(
                order_checks={},
                quote_data=self._quote(args["symbol"]),
                market_data_disclosure="SIMULATED QUOTE; synthetic data; no real broker order.",
            )
        else:
            body = {
                k: args[k]
                for k in (
                    "account_number",
                    "legs",
                    "direction",
                    "type",
                    "quantity",
                    "price",
                    "time_in_force",
                    "market_hours",
                )
            }
            fees = schema_blank(
                self.contracts.tools[name]["outputSchema"]["properties"]["data"]["properties"][
                    "fees"
                ]
            )
            for part in ("occ_fee", "or_fee", "contract_fee", "exchange_fee", "cat_fee"):
                fees[part] = {"fee_rate": "0", "fee": "0"}
            fees["gold_fee_savings"] = {"fee_saving_rate": "0", "fee_total_savings": "0"}
            fees.update(total_fee="0", sales_taxes=[], is_gold=False)
            body.update(
                order_checks={},
                option_quotes=[self._option_quote(args["legs"][0]["option_id"])],
                fees=fees,
            )
        result = {
            "data": body,
            "guide": "SIMULATED review. Requires explicit simulated human approval.",
        }
        self.contracts.validate(name, result, "outputSchema")
        if self.scenario == "stale_review":
            self.clock.advance(31)
        return result

    def _place(self, name, args):
        ref = args["ref_id"]
        prior = self.db.execute("SELECT * FROM simulated_orders WHERE ref_id=?", (ref,)).fetchone()
        if prior:
            if json.loads(prior["arguments"]) != args:
                raise Halt("same client reference with different payload")
            return json.loads(prior["response"])
        if self.scenario == "timeout_before_ack":
            raise TimeoutError("simulated transport failed before acceptance")
        if self.scenario == "crash_during_submission":
            raise SystemExit("simulated process crash before send")
        asset = "option" if name == "place_option_order" else "equity"
        order_schema = self.contracts.tools[name]["outputSchema"]["properties"]["data"][
            "properties"
        ]["order"]
        order = schema_blank(order_schema)
        identity = str(uuid5(NAMESPACE_DNS, "simulated-broker:" + ref))
        order.update(
            id=identity,
            state="rejected" if self.scenario == "rejected" else "queued",
            type="limit",
            quantity=args["quantity"],
            price=args.get("limit_price", args.get("price")),
            stop_price=None,
            time_in_force="gfd",
            market_hours="regular_hours",
            trigger="immediate",
            placed_agent="agentic",
            created_at=self.clock.iso(),
            last_transaction_at=None,
        )
        if asset == "equity":
            order.update(
                ref_id=ref,
                instrument_id=str(uuid5(NAMESPACE_DNS, args["symbol"])),
                symbol=args["symbol"],
                side=args["side"],
                cumulative_quantity="0",
                average_price=None,
                fees="0",
                dollar_based_amount=None,
                executions=[],
            )
        else:
            leg = args["legs"][0]
            contract = self.initial.option_quotes[leg["option_id"]].contract
            order.update(
                chain_id=contract.chain_id,
                chain_symbol=contract.underlying,
                direction=args["direction"],
                processed_quantity="0",
                pending_quantity=args["quantity"],
                canceled_quantity="0",
                premium=str(dec(args["quantity"]) * dec(args["price"]) * contract.multiplier),
                processed_premium="0",
                trade_value_multiplier=str(contract.multiplier),
                opening_strategy="long_call" if contract.kind == "call" else "long_put",
                closing_strategy=None,
                is_replaceable=False,
                updated_at=self.clock.iso(),
                legs=[
                    {
                        **leg,
                        "id": str(uuid5(NAMESPACE_DNS, identity + ":leg")),
                        "expiration_date": contract.expiration,
                        "strike_price": str(contract.strike),
                        "option_type": contract.kind,
                        "executions": [],
                    }
                ],
            )
        response = {
            "data": {"order": order},
            "guide": "SIMULATED submission; inspect state, not an assumed fill.",
        }
        self.contracts.validate(name, response, "outputSchema")
        with self.db:
            self.db.execute(
                "INSERT INTO simulated_orders VALUES(?,?,?,?,?)",
                (ref, identity, asset, dumps(args), dumps(response)),
            )
        if self.scenario == "full_fill":
            self.fill(identity, dec(args["quantity"]))
        elif self.scenario == "partial_fill":
            if dec(args["quantity"]) < 2:
                raise Halt("partial-fill fixture needs at least two units")
            self.fill(identity, dec(args["quantity"]) / 2)
        if self.scenario == "crash_after_acceptance":
            raise SystemExit("simulated crash after broker commit, before ACK/local persistence")
        if self.scenario in {"lost_ack", "timeout_after_acceptance"}:
            raise TimeoutError("simulated acceptance followed by lost acknowledgment")
        if self.scenario == "delayed_ack":
            self.clock.advance(10)
        return json.loads(
            self.db.execute(
                "SELECT response FROM simulated_orders WHERE id=?", (identity,)
            ).fetchone()[0]
        )

    def fill(self, identity, cumulative):
        row = self.db.execute("SELECT * FROM simulated_orders WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise Halt("unknown simulated broker order")
        response = json.loads(row["response"])
        o = response["data"]["order"]
        if o["state"] in {"rejected", "cancelled", "partially_filled_rest_cancelled"}:
            raise Halt("terminal simulated order cannot fill")
        asset = row["asset"]
        old = dec(o["cumulative_quantity" if asset == "equity" else "processed_quantity"])
        quantity = dec(o["quantity"])
        cumulative = dec(cumulative)
        if not old < cumulative <= quantity:
            raise Halt("invalid deterministic fill progression")
        execution = {
            "id": str(uuid5(NAMESPACE_DNS, identity + ":fill:" + str(cumulative))),
            "quantity": str(cumulative - old),
            "price": o["price"],
            "timestamp": self.clock.iso(),
        }
        if asset == "equity":
            execution["fees"] = "0"
            o["executions"].append(execution)
            o.update(cumulative_quantity=str(cumulative), average_price=o["price"])
        else:
            execution.update(settlement_date="2026-10-06", trade_date="2026-10-05")
            o["legs"][0]["executions"].append(execution)
            o.update(
                processed_quantity=str(cumulative),
                pending_quantity=str(quantity - cumulative),
                processed_premium=str(
                    cumulative * dec(o["price"]) * dec(o["trade_value_multiplier"])
                ),
            )
        o.update(
            state="filled" if cumulative == quantity else "partially_filled",
            last_transaction_at=self.clock.iso(),
        )
        self.contracts.validate(f"place_{asset}_order", response, "outputSchema")
        with self.db:
            self.db.execute(
                "UPDATE simulated_orders SET response=? WHERE id=?", (dumps(response), identity)
            )

    def _cancel(self, name, args):
        row = self.db.execute(
            "SELECT * FROM simulated_orders WHERE id=?", (args["order_id"],)
        ).fetchone()
        if not row:
            raise Halt("unknown simulated cancel identity")
        response = json.loads(row["response"])
        o = response["data"]["order"]
        accepted = self.scenario != "cancel_rejected" and o["state"] in {
            "queued",
            "partially_filled",
        }
        if accepted:
            cumulative = dec(
                o["cumulative_quantity" if row["asset"] == "equity" else "processed_quantity"]
            )
            o.update(
                state="partially_filled_rest_cancelled" if cumulative else "cancelled",
                last_transaction_at=self.clock.iso(),
            )
            if row["asset"] == "option":
                o.update(
                    canceled_quantity=str(dec(o["quantity"]) - cumulative), pending_quantity="0"
                )
            with self.db:
                self.db.execute(
                    "UPDATE simulated_orders SET response=? WHERE id=?", (dumps(response), o["id"])
                )
        result = {
            "data": {"accepted": accepted},
            "guide": "SIMULATED cancel acknowledgment; reconcile state.",
        }
        self.contracts.validate(name, result, "outputSchema")
        return result

    def snapshot(self):
        s = copy.deepcopy(self.initial)
        s.asof = self.clock()
        s.regular_session = regular_session(self.clock())
        for table in (s.quote_times, s.bid_times, s.ask_times):
            for k in table:
                table[k] = self.clock()
        for book in s.liquidity.values():
            book["asof"] = self.clock()
        for k, q in s.option_quotes.items():
            s.option_quotes[k] = replace(
                q, asof=self.clock(), contract=replace(q.contract, observed_at=self.clock())
            )
        s.orders = []
        s.fills = []
        for row in self.db.execute("SELECT * FROM simulated_orders ORDER BY id"):
            raw = json.loads(row["response"])["data"]["order"]
            o = normalize_order(raw, row["asset"])
            if row["asset"] == "option":
                o["fees"] = "0"  # Explicit SIMULATED fee fixture, never assumed for a live order.
            s.orders.append(o)
            multiplier = dec(raw.get("trade_value_multiplier", 1))
            for f in o["executions"]:
                quantity, price = dec(f["quantity"]), dec(f["price"])
                symbol = o["symbol"]
                cash_delta = quantity * price * multiplier * (1 if o["side"] == "sell" else -1)
                book = s.options if row["asset"] == "option" else s.positions
                available = s.option_available if row["asset"] == "option" else s.available
                book[symbol] = book.get(symbol, dec(0)) + quantity * (
                    1 if o["side"] == "buy" else -1
                )
                available[symbol] = book[symbol]
                if row["asset"] == "option":
                    s.option_cost_basis[symbol] = book[symbol] * price * multiplier
                if not book[symbol]:
                    book.pop(symbol, None)
                    available.pop(symbol, None)
                    s.option_cost_basis.pop(symbol, None)
                s.cash += cash_delta
                if (
                    datetime.fromtimestamp(utc_time(f["timestamp"]), timezone.utc).date()
                    == datetime.fromtimestamp(self.clock(), timezone.utc).date()
                ):
                    s.daily_turnover += abs(cash_delta)
                s.fills.append(
                    {
                        "id": f["id"],
                        "symbol": symbol,
                        "side": o["side"],
                        "quantity": f["quantity"],
                        "price": f["price"],
                        "cash_delta": str(cash_delta),
                        "timestamp": utc_time(f["timestamp"]),
                    }
                )
        s.buying_power = min(self.initial.buying_power, s.cash)
        s.nav = (
            s.cash
            + sum(q * s.prices[k] for k, q in s.positions.items())
            + sum(
                q * s.option_quotes[k].mark * s.option_quotes[k].contract.multiplier
                for k, q in s.options.items()
            )
        )
        return s
