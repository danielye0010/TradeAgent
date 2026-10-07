"""Commissioning-only minute data boundary; no account or trading endpoints."""

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .domain import Bar, canonical, identity, iso, timestamp


class HistorySource(Protocol):
    def fetch(self, symbols, start, end) -> dict: ...


class AlpacaHistory:
    """Raw SIP bars, timestamped by minute START; availability is an assumption."""

    def fetch(self, symbols, start, end):
        key, secret = os.getenv("APCA_API_KEY_ID"), os.getenv("APCA_API_SECRET_KEY")
        if not key or not secret:
            raise ValueError(
                "historical data unavailable: set APCA_API_KEY_ID/APCA_API_SECRET_KEY "
                "for market data only, or supply --input cached minute data"
            )
        pages, bars, token = [], [], None
        while True:
            params = {
                "symbols": ",".join(symbols),
                "timeframe": "1Min",
                "start": iso(start),
                "end": iso(end),
                "adjustment": "raw",
                "feed": "sip",
                "sort": "asc",
                "limit": 10000,
            }
            if token:
                params["page_token"] = token
            request = Request(
                "https://data.alpaca.markets/v2/stocks/bars?" + urlencode(params),
                headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            )
            try:
                with urlopen(request, timeout=60) as response:
                    page = json.load(response)
            except HTTPError as exc:
                # Do not echo credential headers or vendor error bodies.
                raise ValueError(f"historical market-data HTTP {exc.code}") from None
            pages.append(page)
            for symbol, values in (page.get("bars") or {}).items():
                for item in values:
                    begin = timestamp(item["t"])
                    bars.append(
                        asdict(
                            Bar(
                                symbol,
                                begin,
                                begin + 60,
                                begin + 60,
                                item["o"],
                                item["h"],
                                item["l"],
                                item["c"],
                                item["v"],
                            )
                        )
                    )
            following = page.get("next_page_token")
            if not following:
                break
            if following == token:
                raise ValueError("market-data pagination did not advance")
            token = following
        return {
            "metadata": {
                "source": "alpaca-sip-raw",
                "adjustment": "raw",
                "frequency": "1Min",
                "symbols": symbols,
                "retrieval_start": iso(start),
                "retrieval_end": iso(end),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "availability": "bar_end_assumed_not_point_in_time_archive",
                "timestamp_convention": "vendor minute start; normalized end=start+60s",
            },
            "bars": bars,
            "raw_pages": pages,
        }


class MinuteHistory:
    """Only cut-off-filtered bars leave this adapter for snapshots/resolution."""

    def __init__(self, dataset):
        self.metadata = dataset["metadata"]
        if not self.metadata.get("source") or self.metadata.get("frequency") != "1Min":
            raise ValueError("history requires named source and 1Min frequency")
        if self.metadata.get("adjustment") != "raw":
            raise ValueError("commissioning requires raw, unadjusted minute bars")
        self._bars = {}
        seen = set()
        for item in dataset["bars"]:
            bar = Bar.from_dict(item)
            if bar.end - bar.start != 60:
                raise ValueError("commissioning requires exactly one-minute bars")
            key = (bar.symbol, bar.start)
            if key in seen:
                raise ValueError("duplicate minute bar")
            seen.add(key)
            self._bars.setdefault(bar.symbol, []).append(bar)
        for bars in self._bars.values():
            bars.sort(key=lambda b: b.start)

    def window(self, symbol, start, end, cutoff):
        return tuple(
            b
            for b in self._bars.get(symbol, ())
            if start <= b.start and b.end <= end and b.available_at <= cutoff
        )


def load_history(output, symbols, start, end, input_path=None, source=None):
    """Freeze both normalized bars and raw vendor pages; never silently refresh."""
    request = {"symbols": symbols, "start": iso(start), "end": iso(end), "source": "alpaca-sip-raw"}
    cache = Path(output) / "cache" / (identity(request) + ".json")
    if input_path:
        dataset = json.loads(Path(input_path).read_text())
    elif cache.exists():
        frozen = json.loads(cache.read_text())
        if frozen["request"] != request or frozen["digest"] != identity(frozen["dataset"]):
            raise ValueError("historical cache integrity failure")
        dataset = frozen["dataset"]
    else:
        dataset = (source or AlpacaHistory()).fetch(symbols, start, end)
    history = MinuteHistory(dataset)
    # Validate before caching; input bundles also become durable, content-addressed artifacts.
    cache.parent.mkdir(parents=True, exist_ok=True)
    frozen = {"request": request, "digest": identity(dataset), "dataset": dataset}
    if cache.exists() and json.loads(cache.read_text()) != frozen:
        raise ValueError("cached input changed; choose a new commissioning output")
    if not cache.exists():
        cache.write_text(canonical(frozen) + "\n")
    return history, frozen["digest"], cache
