"""Frozen protocol, calendar and deterministic identities."""

import hashlib
import json
import math
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from ..calendar import session_bounds

NY = ZoneInfo("America/New_York")
PROTOCOL = "weekly-hold-v1"
ARMS = ("codex", "claude", "quant", "random", "spy")
BOOKS = tuple(f"{mode}:{arm}" for mode in ("main", "settled") for arm in ARMS)
UNIVERSE = tuple(json.loads(Path(__file__).with_name("universe.json").read_text()))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


CONFIG = {
    "protocol": PROTOCOL,
    "mode": "SHADOW",
    "timezone": "America/New_York",
    "calendar": "XNYS",
    "initial_cash": "10000",
    "arms": ARMS,
    "universe": UNIVERSE,
    "universe_sha256": digest(UNIVERSE),
    "cutoff": "11:30",
    "deadline": "11:55",
    "fill_window": ["12:00", "12:10"],
    "packet_time": "11:29",
    "history_time": "11:20",
    "risk_time": "15:45",
    "report_time": "17:15",
    "max_names": 3,
    "target_fraction": "1/3",
    "rebalance_band": "0.02",
    "share_decimals": 6,
    "quote_age_seconds": 15,
    "slippage_per_side": "0.0005",
    "fee_fraction": "0",
    "fee_status": "assumed zero",
    "historical_half_spread": "0.0005",
    "risk_loss": "0.10",
    "primary_settlement": "T+1; unsettled buys; no sale before funding settles",
    "sensitivity": "settled-only; next-session purchase window",
    "momentum": "adjusted_close[t-21]/adjusted_close[t-252]-1; previous session t",
    "random_seed": "sha256(protocol + decision_date)",
    "codex_model": "gpt-6.1-sol",
    "claude_model": "claude-opus-5-5",
    "model_effort": "low",
    "model_timeout_seconds": 1400,
    "model_search": "native search; same prompt/schema/packet; sources <= cutoff",
    "source": "official Robinhood bid/ask; Yahoo chart total-return history/events fallback",
    "dividends": "pending cash entitlement at ex-date; spendable only verified pay date",
    "missing": "keep positions; exclude week from inference; preserve account marks",
    "weekly_endpoint": "next decision-day pretrade NAV; pending until both endpoints exist",
    "early_close": "15:45 outside session -> skipped risk check",
    "evaluation_weeks": [4, 13],
    "implementation_version": "1.7.0",
}
CONFIG_HASH = digest(CONFIG)


def implementation_hash():
    root = Path(__file__).parent
    paths = {"weekly/" + f.name: f for f in root.glob("*.py")}
    for name in (
        "calendar.py",
        "market_mcp.py",
        "prospective/assessor.py",
        "prospective/claude_assessor.py",
        "prospective/providers.py",
        "research/opportunities.py",
    ):
        paths[name] = root.parent / name
    return digest(
        {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in sorted(paths.items())
        }
    )


def dec(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("nonfinite ledger value")
    return number


def quantity(value):
    return dec(value).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)


def day_at(now):
    return datetime.fromtimestamp(now, NY).date()


def instant(day, clock):
    return datetime.fromisoformat(f"{day}T{clock}:00").replace(tzinfo=NY).timestamp()


def next_session(day):
    for offset in range(1, 15):
        candidate = day + timedelta(days=offset)
        if session_bounds(candidate):
            return candidate
    raise ValueError("next session unavailable")


def first_session(day):
    monday = day - timedelta(days=day.weekday())
    for offset in range(7):
        candidate = monday + timedelta(days=offset)
        if session_bounds(candidate):
            return candidate
    raise ValueError("week has no XNYS session")


def week_key(day):
    return first_session(day).isoformat()


def next_week(day):
    return first_session(day + timedelta(days=7))


def activation_week(day):
    candidate = first_session(day)
    return candidate if candidate > day else next_week(day)


def valid_quote(q, now, start=None, end=None):
    try:
        return (
            all(math.isfinite(float(q[k])) for k in ("bid", "ask", "asof", "observed_at"))
            and 0 < q["bid"] <= q["ask"]
            and q.get("active", True)
            and q["asof"] <= q["observed_at"] <= now
            and now - q["asof"] <= CONFIG["quote_age_seconds"]
            and (start is None or start <= q["asof"] <= q["observed_at"] < end)
        )
    except (KeyError, TypeError, ValueError):
        return False


@lru_cache(maxsize=2048)
def _session_dates(start, end):
    from ..calendar import calendar

    begin, stop = date.fromisoformat(start), date.fromisoformat(end)
    result = []
    for year in range(begin.year, stop.year + 1):
        lo = max(begin, date(year, 1, 1))
        hi = min(stop, date(year, 12, 31))
        result.extend(label.date() for label in calendar(year).sessions_in_range(str(lo), str(hi)))
    return tuple(result)


def sessions(start, end):
    return iter(_session_dates(str(start), str(end)))
