"""Synthetic TradeAgent state for dashboard tests.

Everything here runs the real research and execution code against generated
market data: the strategy lab demo, a paper round trip on the simulated broker,
and the daily four-arm autopilot over a few weeks of synthetic sessions. All of
it is tagged synthetic by the code that produced it, and nothing contacts a
broker or the network.
"""

import hashlib
import json
import math
import random
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tradeagent.calendar import session_bounds

NY = ZoneInfo("America/New_York")
DEMO_SESSIONS = 21  # just past the 20 paired sessions a comparison needs
_SPIKE_SYMBOLS = ("NVDA", "TSLA", "AMD", "META", "NFLX", "UBER", "AVGO", "CRM", "ORCL", "LLY")
_THESES = {
    "long": [
        "Leading SPY by {rs:+.1%} on heavier volume; continuation more likely than a fade.",
        "Clean higher lows since the open and holding above the session mean.",
        "Breaking out of the opening range while the index is flat.",
        "Relative strength persists across the last three 10-minute blocks.",
    ],
    "avoid": [
        "Move is mostly index beta; nothing stock-specific behind it.",
        "Spike faded twice already; reversal risk outweighs the setup.",
        "Volatility jumped more than price; looks like noise, not trend.",
    ],
}
_CATALYSTS = [
    "Price action only (synthetic demo, no news)",
    "Sector strength in the scan window (synthetic)",
    "Opening gap follow-through (synthetic)",
]


def _rng(*parts):
    seed = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(seed[:16], 16))


def _base_price(symbol):
    return 40 + _rng("price", symbol).random() * 400


def _session_path(day, symbol, opening, closing):
    """Deterministic minute closes for one symbol and one session."""
    r = _rng("path", day.isoformat(), symbol)
    market = _rng("market", day.isoformat())
    drift_day = market.gauss(0, 0.004)
    beta = 0.0 if symbol in {"TLT", "GLD"} else 0.6 + r.random() * 0.8
    vol = 0.0006 + r.random() * 0.0009
    spike = symbol in _SPIKE_SYMBOLS and r.random() < 0.35
    spike_minute = r.randint(5, 25) if spike else -1
    spike_size = r.choice((-1, 1)) * (0.006 + r.random() * 0.012) if spike else 0.0
    trend = r.gauss(0, 0.00006)
    minutes = int((closing - opening) // 60)
    price = _base_price(symbol) * (1 + r.gauss(0, 0.01))
    market_rng = _rng("market-minutes", day.isoformat())
    closes = []
    for i in range(minutes):
        common = market_rng.gauss(drift_day / 390, 0.0005)
        move = beta * common + trend + r.gauss(0, vol)
        if i == spike_minute:
            move += spike_size
        price *= math.exp(move)
        closes.append(price)
    return closes


def _volume(symbol, i, day):
    r = _rng("volume", day.isoformat(), symbol, i)
    return 2000 + r.random() * 3000 * (2 if i < 30 else 1)


class SyntheticMarket:
    """Minute bars, quotes and references in the shape the live providers return."""

    def __init__(self, clock):
        self.clock = clock
        self._paths = {}

    def _closes(self, day, symbol, opening, closing):
        key = (day, symbol)
        if key not in self._paths:
            self._paths[key] = _session_path(day, symbol, opening, closing)
        return self._paths[key]

    def __call__(self, symbols, provider=None, helper=None, *, session=None):
        now = self.clock()
        day = session or datetime.fromtimestamp(now, NY).date()
        opening, closing = session_bounds(day)
        bars, quotes, references = [], {}, {}
        # Resolving an outcome only needs the holding window, not the whole morning.
        earliest = now - 4500 if session is not None else opening
        for symbol in symbols:
            closes = self._closes(day, symbol, opening, closing)
            previous = closes[0] / (1 + _rng("gap", day.isoformat(), symbol).gauss(0, 0.004))
            last = previous
            for i, close in enumerate(closes):
                start = opening + 60 * i
                end = start + 60
                if end > now:
                    break
                if start < earliest:
                    last = close
                    continue
                bar_open = previous if i == 0 else closes[i - 1]
                bars.append(
                    {
                        "symbol": symbol,
                        "start": start,
                        "end": end,
                        "available_at": end,
                        "open": round(bar_open, 4),
                        "high": round(max(bar_open, close) * 1.0004, 4),
                        "low": round(min(bar_open, close) * 0.9996, 4),
                        "close": round(close, 4),
                        "volume": round(_volume(symbol, i, day)),
                    }
                )
                last = close
            spread = max(0.01, round(last * 0.0001, 2))
            quotes[symbol] = {
                "bid": round(last - spread / 2, 2),
                "ask": round(last + spread / 2, 2),
                "asof": now,
                "observed_at": now,
            }
            references[symbol] = {
                "previous_close": round(previous, 4),
                "previous_close_time": opening - 86400,
            }
        return {
            "schema_version": 1,
            "source": "robinhood-opportunity-minute-v1",
            "evidence_kind": "synthetic",
            "observed_at": now,
            "requested_at": now - 1,
            "session_open": opening,
            "session_close": closing,
            "interval_seconds": 60,
            "symbols": list(symbols),
            "bars": bars,
            "quotes": quotes,
            "references": references,
            "limitations": [],
        }


def synthetic_assessment(report):
    """A stand-in for Codex that writes plausible, clearly synthetic research notes."""
    assessments = []
    r = _rng("assess", report["scan_id"])
    candidates = list(report["candidates"])
    scored = []
    for c in candidates:
        features = c.get("features", {})
        rs = features.get("relative_strength", features.get("window_return", 0.0))
        scored.append((rs + r.gauss(0, 0.01), c, rs))
    scored.sort(key=lambda item: -item[0])
    for rank, (score, c, rs) in enumerate(scored, start=1):
        stance = "long" if score > 0.002 and rank == 1 or score > 0.006 else "avoid"
        thesis = r.choice(_THESES[stance]).format(rs=rs)
        assessments.append(
            {
                "symbol": c["symbol"],
                "stance": stance,
                "rank": rank,
                "thesis": thesis,
                "catalyst": r.choice(_CATALYSTS),
                "priced_in": "No news in the synthetic demo",
                "contradictions": ["Synthetic data; not a market view"],
                "invalidation": "Gives back the move or relative strength turns negative",
            }
        )
    return {"assessments": assessments}, {"model": "synthetic-demo-analyst", "tool_calls": 0}


def _sessions(end, count):
    days, day = [], end
    from tradeagent.prospective import daily

    while len(days) < count:
        if daily.window(day):
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


def build_daily(directory, *, sessions=DEMO_SESSIONS, end=None):
    """Run the real daily four-arm pipeline over synthetic sessions; zero orders."""
    from tradeagent.prospective import daily

    directory = Path(directory)
    end = end or datetime.now(NY).date() - timedelta(days=1)
    days = _sessions(end, sessions)
    clock = [daily.window(days[0])[0] + 5]
    market = SyntheticMarket(lambda: clock[0])
    pipeline = daily.Pipeline(
        directory,
        collector=market,
        assessor=synthetic_assessment,
        clock=lambda: clock[0],
        evidence_kind="synthetic",
    )
    try:
        for day in days:
            clock[0] = daily.window(day)[0] + 5
            pipeline.tick()
            decision = next(
                (
                    d
                    for d in pipeline.store.records("decisions")
                    if datetime.fromtimestamp(d["session_open"], NY).date() == day
                ),
                None,
            )
            if decision:
                clock[0] = decision["outcome_exit_time"]
                pipeline.tick()
            # Raw minute captures are only needed while a session resolves.
            shutil.rmtree(directory / "captures", ignore_errors=True)
    finally:
        pipeline.store.close()
    return directory


def build(directory, *, rebuild=False, sessions=DEMO_SESSIONS):
    """Create (or reuse) the demo workspace; returns the state directories it holds."""
    directory = Path(directory).expanduser()
    marker = directory / "demo-workspace.json"
    if rebuild and directory.exists():
        if not marker.exists():
            raise ValueError(f"{directory} is not a dashboard demo workspace; refusing to delete")
        shutil.rmtree(directory)
    if not marker.exists():
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        from tradeagent.oneshot import run_paper
        from tradeagent.research.demo import run_demo

        run_demo(directory / "lab")
        run_paper(directory / "paper", "25", 3600, 3, "full_fill", "full_fill", None)
        build_daily(directory / "daily-four-arm", sessions=sessions)
        marker.write_text(
            json.dumps(
                {"kind": "tradeagent-dashboard-demo", "synthetic": True, "sessions": sessions}
            )
        )
    return [directory / "lab", directory / "paper", directory / "daily-four-arm"]


def synthetic_news_assessor(name):
    """A stand-in news-reading analyst; ``name`` gives each model its own judgement."""

    def assess(report, now=None, search=True):
        salted = {**report, "scan_id": f"{name}:{search}:{report['scan_id']}"}
        document, _ = synthetic_assessment(salted)
        for item in document["assessments"]:
            item.update(hypothesis_type="price_action", sources=[])
            if search:
                item["catalyst"] = "Synthetic headline read (demo, no real news)"
        return document, {"model": f"synthetic-{name}", "web_searches": int(search)}

    return assess


def build_duel(directory, *, sessions=DEMO_SESSIONS, end=None):
    """daily-news-v2 (synthetic Codex) plus the Claude companion (synthetic Claude)."""
    from tradeagent.prospective import claude_compare, daily

    directory = Path(directory)
    end = end or datetime.now(NY).date() - timedelta(days=1)
    days = [d for d in _sessions(end, sessions + 5) if daily.window(d, daily.NEWS_PROTOCOL)]
    days = days[-sessions:]
    clock = [daily.window(days[0], daily.NEWS_PROTOCOL)[0] + 5]
    market = SyntheticMarket(lambda: clock[0])
    v2 = daily.Pipeline(
        directory / "daily-news-v2",
        collector=market,
        assessor=synthetic_assessment,
        news_assessor=synthetic_news_assessor("codex"),
        clock=lambda: clock[0],
        evidence_kind="synthetic",
        protocol=daily.NEWS_PROTOCOL,
    )
    v2.tick()  # writes the v2 deployment the companion checks before it starts
    companion = claude_compare.Companion(
        directory / "daily-news-claude-v1",
        directory / "daily-news-v2",
        assessor=synthetic_news_assessor("claude"),
        clock=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    try:
        for day in days:
            clock[0] = daily.window(day, daily.NEWS_PROTOCOL)[0] + 5
            v2.tick()
            companion.tick()
            decision = next(
                (
                    d
                    for d in v2.store.records("decisions")
                    if datetime.fromtimestamp(d["session_open"], NY).date() == day
                ),
                None,
            )
            if decision:
                clock[0] = decision["outcome_exit_time"]
                v2.tick()
                companion.tick()
            shutil.rmtree(directory / "daily-news-v2" / "captures", ignore_errors=True)
    finally:
        companion.close()
        v2.store.close()
    return [directory / "daily-news-v2", directory / "daily-news-claude-v1"]
