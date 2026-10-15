"""Read-only long Put research. No account access, intents, or option placement."""

import json
import re
import time
from dataclasses import asdict
from datetime import date, datetime
from importlib.resources import files
from pathlib import Path
from zoneinfo import ZoneInfo

from ..model import Halt, digest
from ..standalone_mcp import ExternalOAuthToken, StandaloneMCP
from .domain import OptionBook, timestamp
from .option_economics import long_premium

OPTION_TOOLS = frozenset(
    {"get_option_chains", "get_option_instruments", "get_option_quotes", "get_option_historicals"}
)
PUT_RULE = {
    "dte": [14, 35],
    "expiry": "earliest eligible",
    "strike": "nearest three to spot, then nearest liquid",
    "max_relative_spread": 0.10,
    "min_open_interest": 100,
    "min_volume": 10,
    "min_size": 1,
    "multiplier": 100,
    "quote_age_seconds": 60,
    "capital_dollars": 1000,
    "endpoint_window_seconds": 60,
    "fees": "unobserved; quoted spread P&L excludes unknown commissions/regulatory fees",
}


class OptionAccessDenied(Halt):
    """Authenticated upstream option-data denial; never retry automatically."""


class OptionsResearchMCP(StandaloneMCP):
    read_tools = OPTION_TOOLS
    require_server_version = False

    def __init__(self, token_source, timeout=20):
        super().__init__(token_source, timeout)
        pin = json.loads(
            files("tradeagent").joinpath("contracts/options-research-1.8.0.json").read_text()
        )
        self.contracts.manifest, self.contracts.tools = pin, pin["tools"]
        self.contracts.hash = digest(self.contracts.tools)

    def rpc(self, method, params, *, notification=False, before_send=None):
        if before_send is not None or (
            method == "tools/call" and params.get("name") not in OPTION_TOOLS
        ):
            raise Halt("option research permits only four market-data reads")
        response = super().rpc(method, params, notification=notification)
        if method == "tools/call" and response.get("isError"):
            # Keep raw diagnostics/token-bearing responses out of journals.
            if re.search(r"\b403\b", json.dumps(response)):
                raise OptionAccessDenied(
                    "Robinhood option market data HTTP 403; provider access required"
                )
        return response

    def read(self, name, arguments=None):
        if name not in OPTION_TOOLS:
            raise Halt("option research cannot access accounts or execution")
        # No retries: the collection deadline and actual receipt time remain visible.
        return self._call(name, arguments or {})


class PutReader:
    def __init__(self, bridge, clock=time.time):
        self.bridge, self.clock = bridge, clock
        self.deadline = None

    def read(self, name, arguments):
        if self.deadline is not None:
            remaining = self.deadline - self.clock()
            if remaining <= 0:
                raise ValueError("option discovery deadline exceeded")
            self.bridge.timeout = min(self.bridge.timeout, remaining)
        result = self.bridge.read(name, arguments)
        if self.deadline is not None and self.clock() >= self.deadline:
            raise ValueError("option discovery deadline exceeded")
        return result

    def quotes(self, contracts):
        ids = [c["id"] for c in contracts]
        raw = self.read("get_option_quotes", {"instrument_ids": ids})["data"]
        received = self.clock()
        by_id = {c["id"]: c for c in contracts}
        result = []
        seen = set()
        for item in raw.get("results") or []:
            if item is None or item.get("quote") is None:
                continue
            q = item["quote"]
            cid = q.get("instrument_id")
            if cid not in by_id or cid in seen:
                raise ValueError("unexpected or duplicate option quote")
            seen.add(cid)
            c = by_id[cid]
            book = OptionBook(
                cid,
                c["chain_symbol"],
                c["type"],
                c["expiration_date"],
                float(c["strike_price"]),
                timestamp(q["updated_at"]),
                received,
                float(q["bid_price"]),
                float(q["ask_price"]),
                multiplier=int(float(c["trade_value_multiplier"])),
                volume=q["volume"],
                open_interest=q["open_interest"],
            )
            result.append(
                {"book": asdict(book), "bid_size": q["bid_size"], "ask_size": q["ask_size"]}
            )
        return result

    def select(
        self, symbol, spot, *, deadline=None, kind="put", strict_quotes=False, fees_per_contract=0
    ):
        if kind not in {"call", "put"}:
            raise ValueError("option kind must be call or put")
        self.deadline = deadline
        chains = (
            self.read("get_option_chains", {"underlying_symbol": symbol})["data"].get("chains")
            or []
        )
        chains = [
            c
            for c in chains
            if c["symbol"] == symbol
            and c["can_open_position"]
            and float(c["trade_value_multiplier"]) == 100
            and float(c.get("cash_component") or 0) == 0
            and len(c.get("underlying_instruments") or []) == 1
        ]
        if len(chains) != 1:
            return None
        chain = chains[0]
        today = datetime.fromtimestamp(self.clock(), ZoneInfo("America/New_York")).date()
        expiries = sorted(
            e for e in chain["expiration_dates"] if 14 <= (date.fromisoformat(e) - today).days <= 35
        )
        if not expiries:
            return None
        expiry = expiries[0]
        args = {
            "chain_id": chain["id"],
            "expiration_dates": expiry,
            "type": kind,
            "state": "active",
            "tradability": "tradable",
        }
        instruments, seen, exhausted = [], set(), False
        for _ in range(20):
            if deadline is not None and self.clock() >= deadline:
                raise ValueError("option discovery deadline exceeded")
            page = self.read("get_option_instruments", args)["data"]
            instruments.extend(page.get("instruments") or [])
            cursor = page.get("next")
            if not cursor:
                exhausted = True
                break
            if cursor in seen:
                raise ValueError("repeated option instrument cursor")
            seen.add(cursor)
            args["cursor"] = cursor
        if not exhausted:
            raise ValueError("incomplete option strike universe")
        instruments = [
            c
            for c in instruments
            if c["chain_id"] == chain["id"]
            and c["chain_symbol"] == symbol
            and c["underlying_type"] == "equity"
            and c["type"] == kind
            and c["expiration_date"] == expiry
            and c["state"] == "active"
            and c["tradability"] == "tradable"
            and float(c["trade_value_multiplier"]) == 100
        ]
        nearest = sorted(
            instruments,
            key=lambda c: (abs(float(c["strike_price"]) - spot), float(c["strike_price"]), c["id"]),
        )[:3]
        if not nearest:
            return None
        quotes = self.quotes(nearest)
        if strict_quotes and {q["book"]["contract_id"] for q in quotes} != {
            c["id"] for c in nearest
        }:
            raise ValueError("complete genuine option quotes unavailable")
        eligible = [
            q
            for q in quotes
            if liquid(q, self.clock()) and q["book"]["ask"] * 100 + 2 * fees_per_contract <= 1000
        ]
        if not eligible:
            return None
        chosen = min(
            eligible,
            key=lambda q: (
                abs(q["book"]["strike"] - spot),
                q["book"]["strike"],
                q["book"]["contract_id"],
            ),
        )
        chosen["contract"] = next(c for c in nearest if c["id"] == chosen["book"]["contract_id"])
        chosen["selection_spot"] = spot
        return chosen


def liquid(quote, now):
    book = OptionBook.from_dict(quote["book"])
    return (
        0 <= now - book.asof <= 60
        and book.available_at <= now
        and book.bid > 0
        and (book.ask - book.bid) / ((book.ask + book.bid) / 2) <= 0.10
        and (book.open_interest or 0) >= 100
        and (book.volume or 0) >= 10
        and min(quote["bid_size"], quote["ask_size"]) >= 1
        and book.ask * 100 <= 1000
    )


def put_outcome(
    selection, entry, exit_quote, entry_time, exit_time, *, capital=1000, fees_per_contract=0
):
    """Whole contracts with idle cash; returns always divide by the shared capital."""
    unavailable = {
        "status": "UNAVAILABLE",
        "instrument": "LONG_PUT",
        "contract_id": selection["book"]["contract_id"] if selection else None,
        "direction": -1,
        "entry_price": None,
        "exit_price": None,
        "capital_dollars": capital,
        "pnl_dollars": None,
        "net_return": None,
        "reason": "missing contemporaneous option entry/exit quotes",
    }
    if not selection or not entry or not exit_quote:
        return unavailable
    try:
        start, end = OptionBook.from_dict(entry["book"]), OptionBook.from_dict(exit_quote["book"])
        if (
            start.contract_id != selection["book"]["contract_id"]
            or any(
                getattr(start, k) != selection["book"][k]
                for k in ("contract_id", "underlying", "kind", "expiration", "strike", "multiplier")
            )
            or start.kind != "put"
            or not entry_time <= start.asof <= start.available_at < entry_time + 60
            or not exit_time <= end.asof <= end.available_at < exit_time + 60
            or selection["book"]["available_at"] > entry_time
            or not liquid(entry, start.available_at)
            or exit_quote["bid_size"] < 1
        ):
            return unavailable
        one = long_premium(start, end, fees_per_contract=fees_per_contract)
        count = int(capital // one["max_loss"])
        # Top-of-book size limits the modeled executable quantity.
        count = min(count, entry["ask_size"])
        if exit_quote["bid_size"] < count:
            return {
                **unavailable,
                "reason": "exit quoted size cannot cover the frozen entry quantity",
            }
        if count < 1:
            return {**unavailable, "reason": "one contract exceeds fixed capital or quoted size"}
        return {
            "status": "AVAILABLE",
            "instrument": "LONG_PUT",
            "contract_id": start.contract_id,
            "symbol": start.underlying,
            "direction": -1,
            "expiration": start.expiration,
            "strike": start.strike,
            "multiplier": 100,
            "contracts": count,
            "entry_price": start.ask,
            "exit_price": end.bid,
            "entry_time": start.asof,
            "exit_time": end.asof,
            "entry_observed_at": start.available_at,
            "exit_observed_at": end.available_at,
            "premium_per_contract": one["entry_debit"],
            "premium_required": count * one["entry_debit"],
            "idle_cash": capital - count * one["max_loss"],
            "capital_dollars": capital,
            "pnl_dollars": count * one["pnl"],
            "net_return": count * one["pnl"] / capital,
            "return_on_premium": one["return_on_debit"],
            "known_modeled_fees": 2 * fees_per_contract * count,
            "fee_status": "explicit input" if fees_per_contract else "UNOBSERVED",
            "price_model": "observed ask to observed bid; quoted-size simulation, no actual fills",
        }
    except (ValueError, KeyError, TypeError, OverflowError):
        return unavailable


def open_put_reader(repo, oauth_helper=None):
    helper = oauth_helper or Path.home() / ".local/libexec/robinhood-mcp-oauth-helper"
    return OptionsResearchMCP(ExternalOAuthToken(helper, repo))
