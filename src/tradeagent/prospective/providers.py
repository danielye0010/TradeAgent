"""Explicit market-data adapters; only the official Robinhood MCP is used."""

import json
import math
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..calendar import session_bounds
from ..model import Halt
from ..research.domain import Bar, iso, timestamp
from ..schema import structural
from ..standalone_mcp import ExternalOAuthToken, StandaloneMCP

SYMBOLS = ("QQQ", "IWM", "SPY")
URL = "https://data.alpaca.markets/v2/stocks/snapshots?symbols=QQQ%2CIWM%2CSPY&feed=sip"
ROBINHOOD_SOURCE = "robinhood-mcp-prospective-minute-v1"
ALPACA_SOURCE = "alpaca-sip-prospective-minute-v2"


class MarketDataProvider(Protocol):
    source: str

    def fetch(self, bounds, startup) -> tuple[dict, float, float]: ...
    def normalize(self, payload, received, bounds, startup) -> dict: ...


def provider_source(provider):
    if provider == "robinhood":
        return ROBINHOOD_SOURCE
    if provider == "alpaca":
        return ALPACA_SOURCE
    raise ValueError("unknown market-data provider")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Alpaca:
    source = "alpaca-sip-prospective-minute-v2"

    def __init__(self, values):
        self._key, self._secret = values
        self._opener = build_opener(NoRedirect())

    def fetch(self, bounds=None, startup=None):
        started = time.time()
        request = Request(
            URL,
            headers={"APCA-API-KEY-ID": self._key, "APCA-API-SECRET-KEY": self._secret},
        )
        try:
            with self._opener.open(request, timeout=8) as response:
                payload = json.load(response)
            return payload, started, time.time()
        except HTTPError as error:
            raise ValueError(f"Alpaca market-data HTTP {error.code}") from None
        except (OSError, ValueError):
            raise ValueError("Alpaca market-data transport/JSON failure") from None

    @classmethod
    def normalize(cls, payload, received, bounds, startup):
        normalized, quotes, refs = [], [], []
        try:
            for symbol in SYMBOLS:
                item = payload[symbol]
                raw, quote, previous = item["minuteBar"], item["latestQuote"], item["prevDailyBar"]
                start = timestamp(raw["t"])
                bar = Bar(
                    symbol,
                    start,
                    start + 60,
                    received,
                    *[float(raw[k]) for k in ("o", "h", "l", "c", "v")],
                )
                # Deployment-day health never imports a bar from before startup.
                if start < max(bounds[0], startup) or bar.end > bounds[1]:
                    raise ValueError("minute bar is outside forward collection interval")
                qt, bid, ask = timestamp(quote["t"]), float(quote["bp"]), float(quote["ap"])
                if not 0 < bid <= ask or qt > received or received - qt > 120:
                    raise ValueError("stale/invalid live quote")
                if received - bar.end > 120:
                    raise ValueError("stale live minute bar")
                close = float(previous["c"])
                previous_start = timestamp(previous["t"])
                previous_bounds = session_bounds(
                    datetime.fromtimestamp(previous_start, timezone.utc).date()
                )
                if not previous_bounds:
                    raise ValueError("invalid previous daily session")
                close_time = previous_bounds[1]
                if not close > 0 or close_time >= bounds[0]:
                    raise ValueError("invalid previous daily reference")
                normalized.append(asdict(bar))
                quotes.append((symbol, received, qt, bid, ask))
                refs.append((symbol, received, close, close_time))
        except (KeyError, TypeError, OverflowError, ValueError):
            raise ValueError("missing/stale/invalid Alpaca snapshot") from None
        return {"source": cls.source, "bars": normalized, "quotes": quotes, "references": refs}


class Robinhood:
    """One-minute raw regular-session history plus timestamped bid/ask, never orders."""

    source = ROBINHOOD_SOURCE

    def __init__(self, bridge, clock=time.time):
        self.bridge, self.clock = bridge, clock
        pin = json.loads(
            files("tradeagent").joinpath("contracts/market-data-1.7.0.json").read_text()
        )
        if bridge.server_info.get("version") != pin["server_version"]:
            raise Halt("Robinhood market-data server version drift")
        for name, expected in pin["tools"].items():
            live = bridge.tools.get(name, {})
            if structural({k: live[k] for k in expected if k in live}) != expected:
                raise Halt("Robinhood market-data contract unavailable or changed")

    def fetch(self, bounds, startup):
        started = self.clock()
        # Bounded current session: never import predeployment history as prospective input.
        start = max(bounds[0], math.ceil(startup / 60) * 60)
        end = min(started, bounds[1])
        if end <= start:
            raise ValueError("Robinhood completed market data unavailable")
        history = self.bridge.read(
            "get_equity_historicals",
            {
                "symbols": list(SYMBOLS),
                "start_time": iso(start),
                "end_time": iso(end),
                "interval": "minute",
                "bounds": "regular",
                "adjustment_type": "none",
            },
        )
        quotes = self.bridge.read("get_equity_quotes", {"symbols": list(SYMBOLS)})
        return {"history": history, "quotes": quotes}, started, self.clock()

    @classmethod
    def normalize(cls, payload, received, bounds, startup):
        bars, quotes, refs = [], [], []
        try:
            history = payload["history"]["data"]
            if history.get("not_found"):
                raise ValueError("missing symbol")
            seen = set()
            for result in history["results"]:
                symbol = result["symbol"]
                if symbol not in SYMBOLS or symbol in seen:
                    raise ValueError("invalid history identity")
                seen.add(symbol)
                if result["interval"] != "minute" or result["bounds"] != "regular":
                    raise ValueError("unsupported interval/session")
                starts = set()
                for raw in result["bars"] or []:
                    start = timestamp(raw["begins_at"])
                    if start in starts or start % 60:
                        raise ValueError("duplicate or unaligned minute")
                    starts.add(start)
                    # Forming, predeployment, out-of-session and gap-fill bars are unusable.
                    if (
                        start < max(bounds[0], startup)
                        or start + 60 > min(received, bounds[1])
                        or raw.get("interpolated", False)
                        or raw.get("session") != "reg"
                    ):
                        continue
                    bars.append(
                        asdict(
                            Bar(
                                symbol,
                                start,
                                start + 60,
                                received,
                                *[
                                    float(raw[k])
                                    for k in (
                                        "open_price",
                                        "high_price",
                                        "low_price",
                                        "close_price",
                                        "volume",
                                    )
                                ],
                            )
                        )
                    )
            if seen != set(SYMBOLS):
                raise ValueError("missing history")
            seen = set()
            for result in payload["quotes"]["data"]["results"]:
                quote = result["quote"]
                symbol = quote["symbol"]
                if symbol not in SYMBOLS or symbol in seen:
                    raise ValueError("invalid quote identity")
                seen.add(symbol)
                if not quote["has_traded"] or quote["state"] != "active":
                    raise ValueError("inactive quote")
                times = [timestamp(quote[k]) for k in ("venue_bid_time", "venue_ask_time")]
                if any(t > received or received - t > 120 for t in times):
                    raise ValueError("stale/future book side")
                quotes.append(
                    (
                        symbol,
                        received,
                        min(times),
                        float(quote["bid_price"]),
                        float(quote["ask_price"]),
                    )
                )
                previous = session_bounds(date.fromisoformat(quote["previous_close_date"]))
                if not previous or previous[1] >= bounds[0]:
                    raise ValueError("invalid previous session")
                # Match the raw unadjusted bar basis; do not substitute adjusted prices.
                refs.append((symbol, received, float(quote["previous_close"]), previous[1]))
            if seen != set(SYMBOLS):
                raise ValueError("missing quote")
        except (KeyError, TypeError, OverflowError, ValueError):
            raise ValueError("missing/stale/invalid Robinhood market data") from None
        return {"source": cls.source, "bars": bars, "quotes": quotes, "references": refs}


@contextmanager
def open_provider(provider, repo, oauth_helper=None):
    """External authentication remains outside this repository; no silent fallback."""
    provider_source(provider)
    if provider == "alpaca":
        from . import access

        yield Alpaca(access.load())
    else:
        helper = oauth_helper or Path.home() / ".local/libexec/robinhood-mcp-oauth-helper"
        with StandaloneMCP(ExternalOAuthToken(helper, repo)) as bridge:
            yield Robinhood(bridge)


def provider_adapter(provider):
    provider_source(provider)
    return Robinhood if provider == "robinhood" else Alpaca
