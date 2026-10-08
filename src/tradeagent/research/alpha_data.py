"""Explicit public research download and validated fixed-interval bars; no broker access."""

import hashlib
import json
from bisect import bisect_left
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from .domain import Bar


class ResearchBars:
    def __init__(self, dataset):
        self.metadata = dataset["metadata"]
        self.interval = self.metadata["interval_seconds"]
        if self.interval not in (60, 300):
            raise ValueError("require one- or five-minute bars")
        if self.metadata.get("adjustment") not in ("raw", "split", "vendor_intraday"):
            raise ValueError("unsupported adjustment")
        if not self.metadata.get("source"):
            raise ValueError("source required")
        self.bars = {}
        for item in dataset["bars"]:
            b = Bar.from_dict(item)
            if b.end - b.start != self.interval:
                raise ValueError("interval mismatch")
            self.bars.setdefault(b.symbol, []).append(b)
        for bars in self.bars.values():
            bars.sort(key=lambda b: b.start)
            if any(a.end > b.start for a, b in zip(bars, bars[1:], strict=False)):
                raise ValueError("duplicate/overlapping bars")
        self.starts = {s: [b.start for b in bars] for s, bars in self.bars.items()}

    def window(self, symbol, start, end, cutoff):
        bars = self.bars.get(symbol, ())
        starts = self.starts.get(symbol, ())
        return tuple(
            b
            for b in bars[bisect_left(starts, start) : bisect_left(starts, end)]
            if b.end <= end and b.available_at <= cutoff
        )

    def exact(self, symbol, start, end, cutoff):
        bars = self.window(symbol, start, end, cutoff)
        if (
            not bars
            or bars[0].start != start
            or bars[-1].end != end
            or any(a.end != b.start for a, b in zip(bars, bars[1:], strict=False))
        ):
            raise ValueError(f"{symbol}: incomplete window {start}..{end}")
        return bars


def download_public(output, symbols=("QQQ", "IWM", "SPY")):
    """Freeze raw responses and normalized data. Fresh directory required."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    bars, sources, rejected = [], [], []
    fetched = datetime.now(timezone.utc).timestamp()
    for symbol in symbols:
        if symbol not in ("QQQ", "IWM", "SPY"):
            raise ValueError("public experiment universe is fixed")
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            "?range=60d&interval=5m&includePrePost=false&events=div%2Csplits"
        )
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=30) as response:
            raw = response.read()
        (output / f"{symbol}.json").write_bytes(raw)
        payload = json.loads(raw)
        if payload["chart"]["error"]:
            raise ValueError(str(payload["chart"]["error"]))
        result = payload["chart"]["result"][0]
        if result["meta"]["symbol"] != symbol or result["meta"]["dataGranularity"] != "5m":
            raise ValueError("unexpected data identity")
        quote = result["indicators"]["quote"][0]
        for i, start in enumerate(result["timestamp"]):
            values = [quote[k][i] for k in ("open", "high", "low", "close", "volume")]
            if start + 300 > fetched or any(v is None for v in values):
                rejected.append({"symbol": symbol, "start": start, "reason": "null or incomplete"})
                continue
            try:
                bars.append(asdict(Bar(symbol, start, start + 300, start + 300, *values)))
            except ValueError as exc:
                rejected.append({"symbol": symbol, "start": start, "reason": str(exc)})
        sources.append(
            {
                "symbol": symbol,
                "url": url,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "events": result.get("events", {}),
            }
        )
    dataset = {
        "metadata": {
            "source": "yahoo-public-chart",
            "interval_seconds": 300,
            "adjustment": "vendor_intraday",
            "evidence_kind": "historical_market",
            "retrieved_at": fetched,
            "sources": sources,
            "rejected": rejected,
            "availability": "bar_end_assumed; historical receipt times unavailable",
            "limitations": "revised indicative prices; corporate actions require review; no NBBO",
        },
        "bars": bars,
    }
    (output / "bars.json").write_text(json.dumps(dataset, separators=(",", ":")) + "\n")
    return dataset
