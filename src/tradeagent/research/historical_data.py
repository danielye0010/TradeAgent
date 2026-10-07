"""Historical-only minute inputs. No runtime credentials or broker dependencies."""

import csv
import io
import json
import zipfile
from bisect import bisect_left
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from .domain import Bar, identity
from .history_data import AlpacaHistory, MinuteHistory


class IndexedHistory(MinuteHistory):
    """Reuse validated bars; index instead of scanning years for every window."""

    def __init__(self, dataset):
        adjustment = dataset["metadata"].get("adjustment")
        if adjustment not in {"raw", "split"}:
            raise ValueError("historical alpha requires raw or split-only prices")
        # MinuteHistory's commissioning contract remains raw-only. Split-only ratios
        # are valid for this unleveraged fractional intraday experiment, not fills.
        normalized = {**dataset, "metadata": {**dataset["metadata"], "adjustment": "raw"}}
        super().__init__(normalized)
        self.metadata = dataset["metadata"]
        self.starts = {s: [b.start for b in bars] for s, bars in self._bars.items()}

    def window(self, symbol, start, end, cutoff):
        bars = self._bars.get(symbol, ())
        starts = self.starts.get(symbol, ())
        return tuple(
            b
            for b in bars[bisect_left(starts, start) : bisect_left(starts, end)]
            if b.end <= end and b.available_at <= cutoff
        )


def sample_dataset(cache, symbols):
    """Explicitly requested public sample, frozen locally; never refresh silently."""
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    normalized = cache / "minutes.json"
    if normalized.exists():
        return json.loads(normalized.read_text())
    bars, archives = [], []
    for symbol in symbols:
        url = f"https://frd001.s3.us-east-2.amazonaws.com/frd_sample_etf_{symbol}.zip"
        path = cache / f"{symbol}.zip"
        if not path.exists():
            with urlopen(url, timeout=60) as response:
                content = response.read()
            path.write_bytes(content)
        content = path.read_bytes()
        import hashlib

        archives.append(
            {"symbol": symbol, "url": url, "sha256": hashlib.sha256(content).hexdigest()}
        )
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            readme = archive.read("_readme_documentation.txt").decode()
            if "split-adjusted sample" not in readme or "start of the period" not in readme:
                raise ValueError("sample adjustment/timestamp documentation changed")
            rows = csv.DictReader(io.StringIO(archive.read(f"{symbol}_1min_sample.csv").decode()))
            for row in rows:
                start = (
                    datetime.fromisoformat(row["timestamp"])
                    .replace(tzinfo=ZoneInfo("America/New_York"))
                    .timestamp()
                )
                bars.append(
                    asdict(
                        Bar(
                            symbol,
                            start,
                            start + 60,
                            start + 60,
                            *[float(row[k]) for k in ("open", "high", "low", "close", "volume")],
                        )
                    )
                )
    dataset = {
        "metadata": {
            "source": "firstrate-public-sample",
            "frequency": "1Min",
            "adjustment": "split",
            "evidence_kind": "historical_market",
            "symbols": symbols,
            "archives": archives,
            "availability": "bar_end_assumed_not_point_in_time_archive",
            "timestamp_convention": "US Eastern minute start; end=start+60 seconds",
            "retrieved_at": datetime.now().astimezone().isoformat(),
            "limitations": "vendor-selected two-week sample; omitted zero-volume minutes; revised data",
        },
        "bars": bars,
    }

    normalized.write_text(json.dumps(dataset, separators=(",", ":")) + "\n")
    return dataset


class HistoricalInputs:
    """Monthly Alpaca chunks bound memory for multi-year ranges; input is loaded once."""

    def __init__(self, output, symbols, source="alpaca", input_path=None):
        self.output, self.symbols, self.source = Path(output), symbols, source
        self.history = None
        self.records = []
        if input_path:
            dataset = json.loads(Path(input_path).read_text())
            self._record(dataset)
            self.history = IndexedHistory(dataset)
        elif source == "firstrate-sample":
            dataset = sample_dataset(self.output / "cache", symbols)
            self._record(dataset)
            self.history = IndexedHistory(dataset)

    def _record(self, dataset):
        digest = identity(dataset)
        if not any(r["digest"] == digest for r in self.records):
            bars = dataset["bars"]
            self.records.append(
                {
                    "digest": digest,
                    "metadata": dataset["metadata"],
                    "bars": len(bars),
                    "first_start": min((b["start"] for b in bars), default=None),
                    "last_end": max((b["end"] for b in bars), default=None),
                }
            )

    def chunks(self, sessions):
        if self.history is not None:
            yield self.history, sessions
            return
        grouped = {}
        for session in sessions:
            grouped.setdefault(session["date"][:7], []).append(session)
        for month, rows in grouped.items():
            cache = (
                self.output / "cache" / f"alpaca-{month}-{rows[0]['date']}-{rows[-1]['date']}.json"
            )
            cache.parent.mkdir(parents=True, exist_ok=True)
            if cache.exists():
                dataset = json.loads(cache.read_text())
            else:
                dataset = AlpacaHistory().fetch(
                    self.symbols, rows[0]["previous_close"] - 60, rows[-1]["close"]
                )
                cache.write_text(json.dumps(dataset, separators=(",", ":")) + "\n")
            self._record(dataset)
            yield IndexedHistory(dataset), rows
